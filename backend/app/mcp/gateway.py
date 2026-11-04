"""MCPGateway — the one controlled door to external MCP servers.

Rules the gateway enforces for every call:
- only servers in the registry, only tools in that server's `allowed_tools`;
- URL arguments pass the SSRF / domain allowlist check;
- one timeout per attempt, a few retries with backoff for connection problems;
- a per-server rate limit and a circuit breaker so a dead server costs one timeout,
  not one timeout per interview turn;
- results are cleaned, size-capped and cached; stale cache is served if the server fails;
- **it never raises for operational problems.** A failure comes back as
  `ToolResult(ok=False, ...)`. MCP data is enrichment only — interviews and roadmaps
  must keep working when a server is down.

Each call opens a short-lived session (connect, initialize, call, close). That keeps
the gateway stateless and simple; the cache removes most of the repeated cost.
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import anyio
import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from app.mcp import security
from app.mcp.cache import TTLCache, make_key
from app.mcp.config import ServerConfig, load_registry

logger = logging.getLogger(__name__)

# ── Error codes returned in ToolResult.error_code ────────────────────────────
UNKNOWN_SERVER = "unknown_server"
SERVER_DISABLED = "server_disabled"
TOOL_NOT_ALLOWED = "tool_not_allowed"
INVALID_ARGUMENTS = "invalid_arguments"
NOT_CONFIGURED = "not_configured"
RATE_LIMITED = "rate_limited"
CIRCUIT_OPEN = "circuit_open"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
TOOL_ERROR = "tool_error"

SessionFactory = Callable[[ServerConfig], AbstractAsyncContextManager[ClientSession]]


class ToolCallError(Exception):
    """The server was reachable but reported the tool call as an error."""


class NotConfiguredError(Exception):
    """A required secret (or other local setting) is missing. Not retried."""


@dataclass
class ToolResult:
    ok: bool
    server: str
    tool: str
    text: str = ""  # cleaned and size-capped, but still UNTRUSTED external text
    source: str = "unavailable"  # "live" | "cache" | "stale_cache" | "unavailable"
    error_code: str | None = None
    error: str | None = None
    truncated: bool = False
    latency_ms: float = 0.0
    meta: dict = field(default_factory=dict)

    def as_prompt_context(self) -> str:
        """The only safe way to put this text in a prompt: delimited as untrusted data."""
        if not self.ok or not self.text:
            return ""
        return security.wrap_untrusted(f"{self.server}/{self.tool}", self.text)


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0


# ── Session factories (how we actually reach a server) ───────────────────────


def _no_redirect_client(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """httpx client that does not follow redirects, so a server cannot bounce our
    Authorization header (or us) to another host."""
    return httpx.AsyncClient(
        follow_redirects=False,
        headers=headers,
        timeout=timeout or httpx.Timeout(30.0),
        auth=auth,
    )


@asynccontextmanager
async def http_session(cfg: ServerConfig, environ: Mapping[str, str]):
    headers: dict[str, str] = {}
    if cfg.api_key_env:
        key = environ.get(cfg.api_key_env)
        if not key:
            raise NotConfiguredError(f"{cfg.api_key_env} is not set")
        headers[cfg.api_key_header] = f"{cfg.api_key_prefix}{key}"

    async with streamablehttp_client(
        cfg.url,
        headers=headers,
        timeout=cfg.timeout_seconds,
        httpx_client_factory=_no_redirect_client,
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def stdio_session(cfg: ServerConfig, environ: Mapping[str, str]):
    """Local-development transport. Refused in production."""
    if environ.get("APP_ENV", "").lower() == "production":
        raise NotConfiguredError("stdio MCP servers are disabled in production")
    env = {}
    if cfg.api_key_env and environ.get(cfg.api_key_env):
        env[cfg.api_key_env] = environ[cfg.api_key_env]
    params = StdioServerParameters(
        command=cfg.command[0], args=list(cfg.command[1:]), env=env or None
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def make_default_session_factory(environ: Mapping[str, str]) -> SessionFactory:
    def factory(cfg: ServerConfig) -> AbstractAsyncContextManager[ClientSession]:
        if cfg.transport == "stdio":
            return stdio_session(cfg, environ)
        return http_session(cfg, environ)

    return factory


# ── Helpers ──────────────────────────────────────────────────────────────────


def _leaves(exc: BaseException) -> list[BaseException]:
    """Flatten ExceptionGroups (anyio task groups wrap errors in them)."""
    children = getattr(exc, "exceptions", None)  # ExceptionGroup / BaseExceptionGroup
    if isinstance(children, (tuple, list)):
        out: list[BaseException] = []
        for inner in children:
            out.extend(_leaves(inner))
        return out
    return [exc]


def _classify(exc: BaseException) -> tuple[str, bool]:
    """Map an exception to (error_code, retryable)."""
    leaves = _leaves(exc)
    for leaf in leaves:
        if isinstance(leaf, NotConfiguredError):
            return NOT_CONFIGURED, False
        if isinstance(leaf, ToolCallError):
            return TOOL_ERROR, False
    for leaf in leaves:
        if isinstance(leaf, (TimeoutError, httpx.TimeoutException)):
            return TIMEOUT, True
    return UNAVAILABLE, True


def _extract_text(result) -> str:
    """Pull text out of a CallToolResult. Images / resources are ignored on purpose."""
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    text = "\n".join(parts).strip()
    if not text and getattr(result, "structuredContent", None):
        text = json.dumps(result.structuredContent, ensure_ascii=False)
    if result.isError:
        raise ToolCallError(text[:300] or "tool reported an error")
    return text


# ── The gateway ──────────────────────────────────────────────────────────────


class MCPGateway:
    def __init__(
        self,
        registry: dict[str, ServerConfig] | None = None,
        *,
        session_factory: SessionFactory | None = None,
        cache: TTLCache | None = None,
        url_resolver: security.Resolver = security.default_resolver,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = asyncio.sleep,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        backoff_base_seconds: float = 0.2,
    ):
        self._environ = environ if environ is not None else os.environ
        self.registry = registry if registry is not None else load_registry()
        self._session_factory = session_factory or make_default_session_factory(
            self._environ
        )
        self.cache = cache or TTLCache(clock=clock)
        self._resolver = url_resolver
        self._clock = clock
        self._sleep = sleep
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._backoff_base = backoff_base_seconds
        self._calls: dict[str, deque[float]] = {}
        self._circuits: dict[str, _CircuitState] = {}

    # ── public API ───────────────────────────────────────────────────────────

    async def call_tool(
        self, server: str, tool: str, arguments: dict | None = None
    ) -> ToolResult:
        started = self._clock()
        result = await self._call_tool(server, tool, arguments)
        result.latency_ms = round((self._clock() - started) * 1000, 1)
        # Log shape, never argument values (they can contain resume text / PII).
        logger.info(
            "mcp_call server=%s tool=%s ok=%s source=%s code=%s ms=%.0f arg_keys=%s",
            server,
            tool,
            result.ok,
            result.source,
            result.error_code,
            result.latency_ms,
            sorted((arguments or {}).keys()),
        )
        return result

    async def list_tools(self, server: str) -> list[str]:
        """Tool names a server offers (used by health checks and the vetting spike)."""
        cfg = self.registry[server]
        with anyio.fail_after(cfg.timeout_seconds):
            async with self._session_factory(cfg) as session:
                listed = await session.list_tools()
        return sorted(t.name for t in listed.tools)

    async def health(self) -> dict[str, dict]:
        """Per-server status for GET /api/health/mcp. Never raises."""
        names = list(self.registry)
        checks = await asyncio.gather(*(self._health_one(n) for n in names))
        return dict(zip(names, checks, strict=True))

    # ── internals ────────────────────────────────────────────────────────────

    def _secrets(self, cfg: ServerConfig) -> list[str]:
        return [self._environ.get(cfg.api_key_env, "")] if cfg.api_key_env else []

    def _fail(self, cfg_or_name, tool: str, code: str, message: str) -> ToolResult:
        name = cfg_or_name if isinstance(cfg_or_name, str) else cfg_or_name.name
        return ToolResult(
            ok=False, server=name, tool=tool, error_code=code, error=message[:300]
        )

    def _degrade(
        self, cfg: ServerConfig, tool: str, key: str, code: str, msg: str
    ) -> ToolResult:
        """Server unusable right now: serve stale cache if we have it, else report failure."""
        hit = self.cache.get(key, allow_stale=True)
        if hit is not None:
            text, truncated = hit[0]
            return ToolResult(
                ok=True,
                server=cfg.name,
                tool=tool,
                text=text,
                source="stale_cache",
                truncated=truncated,
                meta={"degraded_reason": code},
            )
        return self._fail(cfg, tool, code, msg)

    def _rate_limit_ok(self, cfg: ServerConfig) -> bool:
        now = self._clock()
        window = self._calls.setdefault(cfg.name, deque())
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= cfg.rate_limit_per_minute:
            return False
        window.append(now)
        return True

    def _circuit(self, name: str) -> _CircuitState:
        return self._circuits.setdefault(name, _CircuitState())

    def _record_failure(self, name: str) -> None:
        state = self._circuit(name)
        state.failures += 1
        if state.failures >= self._failure_threshold:
            state.open_until = self._clock() + self._cooldown

    def _record_success(self, name: str) -> None:
        self._circuits[name] = _CircuitState()

    async def _call_tool(
        self, server: str, tool: str, arguments: dict | None
    ) -> ToolResult:
        cfg = self.registry.get(server)
        if cfg is None:
            return self._fail(
                server, tool, UNKNOWN_SERVER, f"server '{server}' is not registered"
            )
        if not cfg.enabled:
            return self._fail(
                cfg, tool, SERVER_DISABLED, f"server '{server}' is disabled"
            )
        if tool not in cfg.allowed_tools:
            logger.warning("mcp tool blocked server=%s tool=%s", server, tool)
            return self._fail(
                cfg, tool, TOOL_NOT_ALLOWED, f"tool '{tool}' is not allowed"
            )

        try:
            await security.check_arguments(cfg, arguments, self._resolver)
        except security.UrlNotAllowedError as exc:
            logger.warning(
                "mcp url argument blocked server=%s tool=%s: %s", server, tool, exc
            )
            return self._fail(cfg, tool, INVALID_ARGUMENTS, str(exc))

        key = make_key(server, tool, arguments)
        fresh = self.cache.get(key)
        if fresh is not None:
            text, truncated = fresh[0]
            return ToolResult(
                ok=True,
                server=server,
                tool=tool,
                text=text,
                source="cache",
                truncated=truncated,
            )

        if self._circuit(server).open_until > self._clock():
            return self._degrade(
                cfg, tool, key, CIRCUIT_OPEN, "server temporarily skipped"
            )
        if not self._rate_limit_ok(cfg):
            return self._degrade(
                cfg, tool, key, RATE_LIMITED, "per-server rate limit reached"
            )

        last_code, last_msg = UNAVAILABLE, "unknown error"
        for attempt in range(cfg.max_retries + 1):
            try:
                raw = await self._call_once(cfg, tool, arguments or {})
            except Exception as exc:
                # Every failure becomes a ToolResult; nothing escapes to the caller.
                last_code, retryable = _classify(exc)
                last_msg = security.scrub_secrets(
                    str(exc) or type(exc).__name__, self._secrets(cfg)
                )
                if last_code == TOOL_ERROR or last_code == NOT_CONFIGURED:
                    # Server is fine (or we cannot call it at all): no retry, no breaker hit.
                    return self._fail(cfg, tool, last_code, last_msg)
                if retryable and attempt < cfg.max_retries:
                    await self._sleep(self._backoff_base * (2**attempt))
                    continue
                break
            else:
                text, truncated = security.clean_text(raw, cfg.max_result_chars)
                self._record_success(server)
                self.cache.put(
                    key,
                    (text, truncated),
                    ttl=cfg.cache_ttl_seconds,
                    stale_ttl=cfg.stale_ttl_seconds,
                )
                return ToolResult(
                    ok=True,
                    server=server,
                    tool=tool,
                    text=text,
                    source="live",
                    truncated=truncated,
                )

        self._record_failure(server)
        return self._degrade(cfg, tool, key, last_code, last_msg)

    async def _call_once(self, cfg: ServerConfig, tool: str, arguments: dict) -> str:
        # anyio's deadline (not asyncio.wait_for): the MCP SDK runs on anyio task groups
        # and cancelling them with a foreign mechanism can leave the session half-closed.
        with anyio.fail_after(cfg.timeout_seconds):
            async with self._session_factory(cfg) as session:
                result = await session.call_tool(tool, arguments)
        return _extract_text(result)

    async def _health_one(self, name: str) -> dict:
        cfg = self.registry[name]
        if not cfg.enabled:
            return {"status": "disabled"}
        started = self._clock()
        try:
            tools = await self.list_tools(name)
        except Exception as exc:
            code, _ = _classify(exc)
            return {
                "status": "down",
                "error_code": code,
                "latency_ms": round((self._clock() - started) * 1000, 1),
                "circuit_open": self._circuit(name).open_until > self._clock(),
            }
        missing = sorted(cfg.allowed_tools - set(tools))
        return {
            "status": "up",
            "latency_ms": round((self._clock() - started) * 1000, 1),
            "tool_count": len(tools),
            # An allowlisted tool the server no longer offers: the server changed under us.
            "missing_allowed_tools": missing,
            "circuit_open": self._circuit(name).open_until > self._clock(),
        }


# ── App-wide instance ────────────────────────────────────────────────────────

_gateway: MCPGateway | None = None


def get_gateway() -> MCPGateway:
    """Shared gateway (created on first use so importing this module costs nothing)."""
    global _gateway
    if _gateway is None:
        _gateway = MCPGateway()
    return _gateway


def reset_gateway() -> None:
    """Drop the shared instance (tests, or after the registry file changes)."""
    global _gateway
    _gateway = None
