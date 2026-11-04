"""MCP server registry — loads and validates mcp_servers.yaml.

The registry is the single list of external MCP servers the app may talk to.
Nothing outside this file's output can be called: the gateway refuses unknown
servers and any tool that is not in a server's `allowed_tools`.

Secrets are never stored in the YAML. A server names the *environment variable*
that holds its key (`api_key_env`); the gateway reads it at call time.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("mcp_servers.yaml")

VALID_TRANSPORTS = {"http", "stdio"}


class RegistryError(ValueError):
    """The registry file is missing, malformed, or breaks a rule."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: str  # "http" (streamable HTTP) or "stdio" (local development only)
    allowed_tools: frozenset[str]
    url: str | None = None
    command: tuple[str, ...] = ()
    api_key_env: str | None = None
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    timeout_seconds: float = 10.0
    max_retries: int = 2
    rate_limit_per_minute: int = 30
    cache_ttl_seconds: int = 3600
    stale_ttl_seconds: int = 86400
    max_result_chars: int = 20000
    # Hosts a URL *argument* may point at (e.g. a fetch tool). Empty = no URL arguments allowed.
    allowed_url_domains: frozenset[str] = frozenset()
    # True for servers we run ourselves inside the Docker network (private address is expected).
    internal: bool = False
    enabled: bool = True


def _require(raw: dict, key: str, name: str):
    if key not in raw or raw[key] in (None, "", []):
        raise RegistryError(f"server '{name}': '{key}' is required")
    return raw[key]


def _parse_server(name: str, raw: dict, environ: Mapping[str, str]) -> ServerConfig:
    if not isinstance(raw, dict):
        raise RegistryError(f"server '{name}': entry must be a mapping")

    transport = _require(raw, "transport", name)
    if transport not in VALID_TRANSPORTS:
        raise RegistryError(
            f"server '{name}': transport must be one of {sorted(VALID_TRANSPORTS)}"
        )

    tools = _require(raw, "allowed_tools", name)
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise RegistryError(f"server '{name}': allowed_tools must be a list of names")

    url = raw.get("url")
    command: tuple[str, ...] = ()
    if transport == "http":
        # `url_env` lets docker-compose point the same registry at a different host.
        if raw.get("url_env") and environ.get(raw["url_env"]):
            url = environ[raw["url_env"]]
        url = _require({"url": url}, "url", name)
        parsed = urlparse(url)
        internal = bool(raw.get("internal", False))
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise RegistryError(f"server '{name}': url must be http(s) with a host")
        # Plain http is only acceptable for servers inside our own network.
        if parsed.scheme == "http" and not internal:
            raise RegistryError(
                f"server '{name}': plain http needs 'internal: true' (self-hosted only)"
            )
    else:
        cmd = _require(raw, "command", name)
        if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
            raise RegistryError(f"server '{name}': command must be a list of strings")
        command = tuple(cmd)

    api_key_env = raw.get("api_key_env")
    if api_key_env is not None and not re.fullmatch(
        r"[A-Z][A-Z0-9_]*", str(api_key_env)
    ):
        raise RegistryError(
            f"server '{name}': api_key_env must be an environment variable NAME, "
            "in UPPER_SNAKE_CASE, not the key itself"
        )

    cfg = ServerConfig(
        name=name,
        transport=transport,
        allowed_tools=frozenset(tools),
        url=url,
        command=command,
        api_key_env=api_key_env,
        api_key_header=raw.get("api_key_header", "Authorization"),
        api_key_prefix=raw.get("api_key_prefix", "Bearer "),
        timeout_seconds=float(raw.get("timeout_seconds", 10)),
        max_retries=int(raw.get("max_retries", 2)),
        rate_limit_per_minute=int(raw.get("rate_limit_per_minute", 30)),
        cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 3600)),
        stale_ttl_seconds=int(raw.get("stale_ttl_seconds", 86400)),
        max_result_chars=int(raw.get("max_result_chars", 20000)),
        allowed_url_domains=frozenset(
            d.lower().lstrip(".") for d in raw.get("allowed_url_domains", [])
        ),
        internal=bool(raw.get("internal", False)),
        enabled=bool(raw.get("enabled", True)),
    )

    if cfg.timeout_seconds <= 0 or cfg.max_retries < 0 or cfg.max_result_chars <= 0:
        raise RegistryError(
            f"server '{name}': timeout / retries / size limits are invalid"
        )
    if cfg.rate_limit_per_minute <= 0:
        raise RegistryError(f"server '{name}': rate_limit_per_minute must be positive")
    return cfg


def parse_registry(
    data: dict | None, environ: Mapping[str, str] | None = None
) -> dict[str, ServerConfig]:
    """Validate an already-loaded YAML mapping and return {name: ServerConfig}."""
    if not data or "servers" not in data:
        raise RegistryError("registry must have a top-level 'servers' mapping")
    servers = data["servers"]
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise RegistryError("'servers' must be a mapping of name -> settings")
    env = environ if environ is not None else os.environ
    return {name: _parse_server(name, raw, env) for name, raw in servers.items()}


def load_registry(path: str | Path | None = None) -> dict[str, ServerConfig]:
    """Load the registry from `path`, else MCP_REGISTRY_PATH, else the bundled file."""
    chosen = Path(path or os.getenv("MCP_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
    try:
        text = chosen.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read MCP registry at {chosen}: {exc}") from exc
    try:
        data = yaml.safe_load(text)  # safe_load: never construct arbitrary objects
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid YAML in {chosen}: {exc}") from exc
    return parse_registry(data)
