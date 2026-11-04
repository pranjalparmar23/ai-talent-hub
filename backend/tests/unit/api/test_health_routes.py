import pytest

from app.api import health_routes
from app.mcp.config import RegistryError, parse_registry
from app.mcp.gateway import MCPGateway


class FakeGateway:
    """Stands in for MCPGateway: returns a fixed report and counts how often it is asked."""

    def __init__(self, report):
        self.report = report
        self.calls = 0

    async def health(self):
        self.calls += 1
        return self.report


UP = {"status": "up", "latency_ms": 12.0, "tool_count": 2, "missing_allowed_tools": []}
DOWN = {"status": "down", "error_code": "unavailable", "latency_ms": 5.0}
OFF = {"status": "disabled"}


@pytest.fixture(autouse=True)
def fresh_cache():
    health_routes.reset_health_cache()
    yield
    health_routes.reset_health_cache()


def use_gateway(monkeypatch, gateway):
    monkeypatch.setattr(health_routes, "get_gateway", lambda: gateway)


async def test_all_enabled_servers_up_is_ok(client, monkeypatch):
    use_gateway(monkeypatch, FakeGateway({"a": UP, "b": OFF}))
    resp = await client.get("/api/health/mcp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["servers"]["a"]["status"] == "up"


async def test_one_server_down_is_degraded_but_still_200(client, monkeypatch):
    use_gateway(monkeypatch, FakeGateway({"a": UP, "b": DOWN}))
    resp = await client.get("/api/health/mcp")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


async def test_no_enabled_servers(client, monkeypatch):
    use_gateway(monkeypatch, FakeGateway({"a": OFF}))
    resp = await client.get("/api/health/mcp")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_servers_enabled"


async def test_bad_registry_reports_misconfigured_without_details(client, monkeypatch):
    def broken():
        raise RegistryError("invalid YAML in /secret/path/mcp_servers.yaml")

    monkeypatch.setattr(health_routes, "get_gateway", broken)
    resp = await client.get("/api/health/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"status": "misconfigured", "servers": {}}
    assert "secret" not in resp.text


async def test_report_is_cached_briefly(client, monkeypatch):
    gateway = FakeGateway({"a": UP})
    use_gateway(monkeypatch, gateway)
    await client.get("/api/health/mcp")
    await client.get("/api/health/mcp")
    assert gateway.calls == 1


async def test_cache_expires(client, monkeypatch):
    gateway = FakeGateway({"a": UP})
    use_gateway(monkeypatch, gateway)
    monkeypatch.setattr(health_routes, "HEALTH_CACHE_SECONDS", 0)
    await client.get("/api/health/mcp")
    await client.get("/api/health/mcp")
    assert gateway.calls == 2


async def test_real_gateway_reports_unreachable_server_without_leaking_its_url(
    client, monkeypatch
):
    # Port 9 on localhost refuses connections immediately: no external network needed.
    registry = parse_registry(
        {
            "servers": {
                "unreachable": {
                    "transport": "http",
                    "url": "http://127.0.0.1:9/mcp",
                    "internal": True,
                    "allowed_tools": ["x"],
                    "timeout_seconds": 2,
                    "max_retries": 0,
                },
                "off": {
                    "transport": "http",
                    "url": "https://off.example.com/mcp",
                    "allowed_tools": ["x"],
                    "enabled": False,
                },
            }
        },
        {},
    )
    use_gateway(monkeypatch, MCPGateway(registry, environ={}))
    resp = await client.get("/api/health/mcp")
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["servers"]["unreachable"]["status"] == "down"
    assert body["servers"]["off"] == {"status": "disabled"}
    assert "127.0.0.1" not in resp.text and "example.com" not in resp.text
