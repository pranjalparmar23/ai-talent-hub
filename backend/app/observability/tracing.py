"""Tracing conventions for LangSmith (see docs/architecture/tracing.md).

Small helpers. They read environment variables and never raise: a tracing problem
must not break a request. Only `build_client` touches LangSmith, and only because
the SDK contacts it in the background when a client is created.

Nothing here is wired into the agents yet (Phase 7). The rules they enforce:

- run names look like `<area>.<name>` (for example `candidate.resume_parser`);
- tags and metadata come from a fixed vocabulary, so no user text can end up there;
- users appear only as a keyed hash, never as an id, name or email;
- text that does reach a trace can be passed through `redact_text` first.
"""

import hashlib
import hmac
import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

# ── Environments and projects ────────────────────────────────

PROJECT_NAMES = {
    "dev": "talent-hub-dev",
    "ci": "talent-hub-ci",
    "prod": "talent-hub-prod",
}

_ENV_ALIASES = {
    "development": "dev",
    "dev": "dev",
    "local": "dev",
    "ci": "ci",
    "test": "ci",
    "testing": "ci",
    "production": "prod",
    "prod": "prod",
}

# Areas that get an `area:` and an `agent:` tag. Other areas (graph, mcp, rag)
# only appear in the run name.
AGENT_AREAS = ("candidate", "recruiter", "shared")
RUN_AREAS = AGENT_AREAS + ("graph", "mcp", "rag")
ROLE_FAMILIES = ("technical", "non_technical")

# The only metadata keys that may appear in a trace. The installed SDK cannot mask
# metadata, so anything outside this list is dropped.
ALLOWED_METADATA_KEYS = frozenset(
    {
        "user_hash",
        "request_id",
        "app_version",
        "prompt_version",
        "rubric_version",
        "model",
        "collection",
        "top_k",
        "mcp_server",
        "mcp_tool",
        "mcp_ok",
        "mcp_error_code",
        "duration_ms",
    }
)

_MAX_VALUE_CHARS = 100
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._/\-]{0,63}$")


# ── Environment helpers ──────────────────────────────────────


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _ls_var(name: str, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Read LANGSMITH_<name>, then LANGCHAIN_<name> (the order the SDK uses)."""
    env = _env(environ)
    for prefix in ("LANGSMITH", "LANGCHAIN"):
        value = env.get(f"{prefix}_{name}")
        if value is not None:
            return value
    return None


def _slug(value: Any) -> Optional[str]:
    """Lower-case a label and accept it only if it is a safe, short slug."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _SLUG.match(candidate) else None


def environment_tag(environ: Optional[Mapping[str, str]] = None) -> str:
    """Map APP_ENV to `dev`, `ci` or `prod` (anything unknown counts as `dev`)."""
    raw = _env(environ).get("APP_ENV", "").strip().lower()
    return _ENV_ALIASES.get(raw, "dev")


def tracing_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when the SDK would trace: TRACING_V2 or TRACING is `true`."""
    value = _ls_var("TRACING_V2", environ)
    if value is None:
        value = _ls_var("TRACING", environ)
    return (value or "").strip().lower() == "true"


def content_hidden(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when both inputs and outputs are hidden from traces."""
    hide_in = (_ls_var("HIDE_INPUTS", environ) or "").strip().lower() == "true"
    hide_out = (_ls_var("HIDE_OUTPUTS", environ) or "").strip().lower() == "true"
    return hide_in and hide_out


def _parse_rate(raw: str) -> Optional[float]:
    """A number from 0 to 1, or None (the SDK rejects anything else)."""
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0.0 <= value <= 1.0 else None  # NaN fails this test too


def sample_rate(environ: Optional[Mapping[str, str]] = None) -> float:
    """Share of traces to keep, 0 to 1. Unset means 1.0; a bad value means 0.0."""
    raw = _ls_var("TRACING_SAMPLING_RATE", environ)
    if raw is None or raw.strip() == "":
        return 1.0
    parsed = _parse_rate(raw)
    return 0.0 if parsed is None else parsed


# ── Hashed user ids ──────────────────────────────────────────


def hash_user_id(
    user_id: Any, environ: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """Keyed hash (HMAC-SHA256, 16 hex chars) so traces never carry a real id.

    Returns None when there is no user. Without TRACE_HASH_SECRET it returns
    `anon`, never an unkeyed hash that someone could reverse by guessing ids.
    """
    if user_id is None or str(user_id) == "":
        return None
    secret = _env(environ).get("TRACE_HASH_SECRET", "")
    if not secret:
        return "anon"
    digest = hmac.new(
        secret.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256
    )
    return digest.hexdigest()[:16]


# ── Redaction ────────────────────────────────────────────────


def _phone_or_keep(match: "re.Match[str]") -> str:
    """Replace long digit groups; keep short ones such as `2020 - 2024`."""
    digits = sum(ch.isdigit() for ch in match.group(0))
    return "[phone]" if digits >= 9 else match.group(0)


_REDACTIONS: List[Any] = [
    (
        re.compile(r"\b(?:sk|gsk|ls|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"),
        "[api-key]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [token]"),
    (
        re.compile(
            r"(?i)([?&](?:token|access_token|api_key|apikey|key|secret|password"
            r"|sig|signature)=)[^&\s#]+"
        ),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
        ),
        "[email]",
    ),
    (re.compile(r"(?<!\w)\+?\d[\d\s().\-]{7,}\d(?!\w)"), _phone_or_keep),
    (re.compile(r"\b\d{9,}\b"), "[number]"),
]


def redact_text(text: Any) -> Any:
    """Mask emails, phone numbers, long numbers, API keys and URL secrets.

    A second layer only: regexes are not exhaustive, and the rule is still that
    real personal data stays out of development traces. It errs on the side of
    removing too much. Non-strings are returned unchanged. Never raises.
    """
    if not isinstance(text, str):
        return text
    try:
        for pattern, replacement in _REDACTIONS:
            text = pattern.sub(replacement, text)
    except Exception:  # pragma: no cover - defensive
        return "[redaction-failed]"
    return text


def build_anonymizer() -> Optional[Callable[[Any], Any]]:
    """An anonymizer for `langsmith.Client(anonymizer=...)`, or None if unavailable."""
    try:
        from langsmith.anonymizer import create_anonymizer
    except Exception:  # pragma: no cover - langsmith is optional
        return None
    return create_anonymizer(redact_text)


def build_client(environ: Optional[Mapping[str, str]] = None) -> Any:
    """A LangSmith client that masks text, or None when tracing is off or unkeyed.

    Creating the client makes the SDK contact LangSmith in the background, so tests
    must not build one. Phase 7 must check how to attach it to LangChain's tracer.
    """
    api_key = _ls_var("API_KEY", environ)
    if not tracing_enabled(environ) or not api_key:
        return None
    try:
        from langsmith import Client
    except Exception:  # pragma: no cover - langsmith is optional
        return None
    return Client(api_key=api_key, anonymizer=build_anonymizer())


# ── Run configuration ────────────────────────────────────────


def _clean_value(value: Any) -> Any:
    """Keep simple scalars only, redacted and shortened."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = redact_text(value.replace("\n", " ").strip())
        return cleaned[:_MAX_VALUE_CHARS]
    return None


def clean_metadata(items: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Drop keys outside the allowlist and any value that is not a plain scalar."""
    result: Dict[str, Any] = {}
    for key, value in (items or {}).items():
        if key not in ALLOWED_METADATA_KEYS:
            continue
        cleaned = _clean_value(value)
        if cleaned is not None and cleaned != "":
            result[key] = cleaned
    return result


def build_run_config(
    area: str,
    name: str,
    *,
    user_id: Any = None,
    request_id: Optional[str] = None,
    prompt_version: Optional[str] = None,
    rubric_version: Optional[str] = None,
    model: Optional[str] = None,
    role_family: Optional[str] = None,
    eval_run: bool = False,
    extra_metadata: Optional[Mapping[str, Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """The `config` dict for `chain.ainvoke(..., config=...)`.

    Returns `{"run_name": ..., "tags": [...], "metadata": {...}}`. Bad labels are
    dropped rather than raised, so a typo never breaks a request.
    """
    try:
        area_slug = _slug(area)
        name_slug = _slug(name)
        if area_slug in RUN_AREAS and name_slug:
            run_name = f"{area_slug}.{name_slug}"
        else:
            run_name = "unknown"

        tags: List[str] = []
        if area_slug in AGENT_AREAS and name_slug:
            tags += [f"area:{area_slug}", f"agent:{name_slug}"]
        tags.append(f"env:{environment_tag(environ)}")
        for prefix, value in (("prompt", prompt_version), ("model", model)):
            slug = _slug(value)
            if slug:
                tags.append(f"{prefix}:{slug}")
        if role_family in ROLE_FAMILIES:
            tags.append(f"role_family:{role_family}")
        if eval_run:
            tags.append("eval")

        metadata: Dict[str, Any] = {}
        user_hash = hash_user_id(user_id, environ)
        if user_hash:
            metadata["user_hash"] = user_hash
        for key, value in (
            ("request_id", request_id),
            ("prompt_version", prompt_version),
            ("rubric_version", rubric_version),
            ("model", model),
            ("app_version", _env(environ).get("APP_VERSION")),
        ):
            if value:
                metadata[key] = value
        metadata.update(extra_metadata or {})
        return {
            "run_name": run_name,
            "tags": tags,
            "metadata": clean_metadata(metadata),
        }
    except Exception:  # pragma: no cover - defensive
        return {"run_name": "unknown", "tags": [], "metadata": {}}


def mcp_run_metadata(
    server: str,
    tool: str,
    ok: bool,
    error_code: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Metadata for an MCP call: what ran and how it went, never the result text."""
    return clean_metadata(
        {
            "mcp_server": server,
            "mcp_tool": tool,
            "mcp_ok": ok,
            "mcp_error_code": error_code,
            "duration_ms": None if duration_ms is None else round(duration_ms, 1),
        }
    )


# ── Configuration check ──────────────────────────────────────


def config_warnings(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """Plain-language problems with the tracing setup. Empty means fine.

    Meant for a startup log line in Phase 7. It never reads or returns key values.
    """
    if not tracing_enabled(environ):
        return []
    warnings: List[str] = []
    env = environment_tag(environ)
    if not _ls_var("API_KEY", environ):
        warnings.append("tracing is on but no LangSmith API key is set")
    project = (_ls_var("PROJECT", environ) or "").strip()
    expected = PROJECT_NAMES[env]
    if project != expected:
        warnings.append(f"project is '{project or 'unset'}', expected '{expected}'")
    raw_rate = (_ls_var("TRACING_SAMPLING_RATE", environ) or "").strip()
    if raw_rate and _parse_rate(raw_rate) is None:
        warnings.append("sampling rate is not a number from 0 to 1, tracing is dropped")
    if env == "prod":
        if not content_hidden(environ):
            warnings.append(
                "production traces would include inputs or outputs "
                "(set LANGSMITH_HIDE_INPUTS and LANGSMITH_HIDE_OUTPUTS to true)"
            )
        if not _env(environ).get("TRACE_HASH_SECRET"):
            warnings.append(
                "TRACE_HASH_SECRET is not set, user ids will show as 'anon'"
            )
    return warnings
