import pytest

from app.mcp.config import RegistryError, load_registry, parse_registry


def _server(**over):
    base = {
        "transport": "http",
        "url": "https://mcp.example.com/mcp",
        "allowed_tools": ["search"],
    }
    base.update(over)
    return base


def test_valid_server_gets_defaults():
    reg = parse_registry({"servers": {"a": _server()}}, environ={})
    cfg = reg["a"]
    assert cfg.allowed_tools == frozenset({"search"})
    assert cfg.timeout_seconds == 10.0
    assert cfg.max_retries == 2
    assert cfg.enabled is True
    assert cfg.internal is False


def test_plain_http_requires_internal():
    with pytest.raises(RegistryError, match="internal"):
        parse_registry(
            {"servers": {"a": _server(url="http://mcp.example.com/mcp")}}, {}
        )


def test_plain_http_allowed_for_internal_servers():
    raw = _server(url="http://mcp-demo:8100/mcp", internal=True)
    assert parse_registry({"servers": {"a": raw}}, {})["a"].internal is True


def test_api_key_env_must_be_a_variable_name_not_a_key():
    with pytest.raises(RegistryError, match="NAME"):
        parse_registry({"servers": {"a": _server(api_key_env="sk-live-abc123")}}, {})
    assert parse_registry({"servers": {"a": _server(api_key_env="ONET_API_KEY")}}, {})


def test_allowed_tools_is_required():
    raw = _server()
    del raw["allowed_tools"]
    with pytest.raises(RegistryError, match="allowed_tools"):
        parse_registry({"servers": {"a": raw}}, {})


def test_unknown_transport_rejected():
    with pytest.raises(RegistryError, match="transport"):
        parse_registry({"servers": {"a": _server(transport="websocket")}}, {})


def test_stdio_needs_command():
    raw = {"transport": "stdio", "allowed_tools": ["x"]}
    with pytest.raises(RegistryError, match="command"):
        parse_registry({"servers": {"a": raw}}, {})
    raw["command"] = ["python", "server.py"]
    assert parse_registry({"servers": {"a": raw}}, {})["a"].command == (
        "python",
        "server.py",
    )


def test_url_env_overrides_url():
    raw = _server(url_env="MCP_X_URL")
    reg = parse_registry(
        {"servers": {"a": raw}}, {"MCP_X_URL": "https://other.example.org/mcp"}
    )
    assert reg["a"].url == "https://other.example.org/mcp"


def test_bad_limits_rejected():
    with pytest.raises(RegistryError):
        parse_registry({"servers": {"a": _server(timeout_seconds=0)}}, {})
    with pytest.raises(RegistryError):
        parse_registry({"servers": {"a": _server(rate_limit_per_minute=0)}}, {})


def test_missing_servers_key_rejected():
    with pytest.raises(RegistryError, match="servers"):
        parse_registry({}, {})


def test_allowed_url_domains_are_normalised():
    raw = _server(allowed_url_domains=[".Example.com", "docs.python.org"])
    cfg = parse_registry({"servers": {"a": raw}}, {})["a"]
    assert cfg.allowed_url_domains == frozenset({"example.com", "docs.python.org"})


def test_missing_file_raises_registry_error(tmp_path):
    with pytest.raises(RegistryError, match="cannot read"):
        load_registry(tmp_path / "nope.yaml")


def test_invalid_yaml_raises_registry_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("servers: [unclosed", encoding="utf-8")
    with pytest.raises(RegistryError, match="invalid YAML"):
        load_registry(bad)


def test_bundled_registry_loads_and_nothing_untrusted_is_enabled_by_default():
    reg = load_registry()
    assert "demo_reference" in reg
    # Third-party servers must stay disabled until they pass the vetting checklist.
    assert all(not cfg.enabled for cfg in reg.values())
