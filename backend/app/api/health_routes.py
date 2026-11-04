"""Health endpoints.

GET /api/health/mcp reports whether each registered MCP server is reachable.
It always answers 200: MCP is enrichment only, so a server being down is a warning
for operators, not a reason to fail the app's own liveness checks (see GET /health).

The report never includes server URLs, keys or error text — only status, latency and
counts — and it is cached briefly so repeated calls cannot hammer the MCP servers.
"""

import logging
import time
from typing import Optional, Tuple

from fastapi import APIRouter

from app.mcp.config import RegistryError
from app.mcp.gateway import get_gateway

logger = logging.getLogger(__name__)

router = APIRouter()

HEALTH_CACHE_SECONDS = 10.0
_cached: Optional[Tuple[float, dict]] = None


def reset_health_cache() -> None:
    """Forget the cached report (used by tests)."""
    global _cached
    _cached = None


def overall_status(servers: dict) -> str:
    """ok = every enabled server is up; degraded = at least one is not."""
    enabled = [s for s in servers.values() if s.get("status") != "disabled"]
    if not enabled:
        return "no_servers_enabled"
    if all(s.get("status") == "up" for s in enabled):
        return "ok"
    return "degraded"


@router.get("/mcp")
async def mcp_health():
    global _cached
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < HEALTH_CACHE_SECONDS:
        return _cached[1]

    try:
        gateway = get_gateway()
    except RegistryError:
        # Details go to the log, not to the caller.
        logger.exception("MCP registry could not be loaded")
        return {"status": "misconfigured", "servers": {}}

    servers = await gateway.health()
    payload = {"status": overall_status(servers), "servers": servers}
    _cached = (now, payload)
    return payload
