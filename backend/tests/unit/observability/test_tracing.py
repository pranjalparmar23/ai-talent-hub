"""Unit tests for the tracing helpers. No network, no LangSmith key needed."""

import pytest

from app.observability import tracing as t

DEV = {"APP_ENV": "development", "TRACE_HASH_SECRET": "s3cret"}
PROD = {"APP_ENV": "production", "TRACE_HASH_SECRET": "s3cret"}


# ── environment ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "app_env,expected",
    [
        ("development", "dev"),
        ("Production", "prod"),
        ("prod", "prod"),
        ("ci", "ci"),
        ("testing", "ci"),
        ("", "dev"),
        ("staging-eu", "dev"),
    ],
)
def test_environment_tag(app_env, expected):
    assert t.environment_tag({"APP_ENV": app_env}) == expected


def test_environment_tag_unset_is_dev():
    assert t.environment_tag({}) == "dev"


def test_tracing_enabled_reads_both_prefixes_and_names():
    assert t.tracing_enabled({"LANGCHAIN_TRACING_V2": "true"})
    assert t.tracing_enabled({"LANGSMITH_TRACING": "true"})
    assert t.tracing_enabled({"LANGSMITH_TRACING_V2": " TRUE "})
    assert not t.tracing_enabled({"LANGCHAIN_TRACING_V2": "false"})
    assert not t.tracing_enabled({})


def test_content_hidden_needs_both_inputs_and_outputs():
    assert t.content_hidden(
        {"LANGSMITH_HIDE_INPUTS": "true", "LANGSMITH_HIDE_OUTPUTS": "true"}
    )
    assert not t.content_hidden({"LANGSMITH_HIDE_INPUTS": "true"})
    assert not t.content_hidden({})


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 1.0),
        ("", 1.0),
        ("0.1", 0.1),
        ("0", 0.0),
        ("1", 1.0),
        ("1.5", 0.0),
        ("-0.2", 0.0),
        ("abc", 0.0),
        ("nan", 0.0),
    ],
)
def test_sample_rate(raw, expected):
    environ = {} if raw is None else {"LANGSMITH_TRACING_SAMPLING_RATE": raw}
    assert t.sample_rate(environ) == expected


# ── hashed user ids ──────────────────────────────────────────


def test_hash_user_id_is_stable_short_and_hides_the_id():
    first = t.hash_user_id("jane-doe", DEV)
    assert first == t.hash_user_id("jane-doe", DEV)
    assert len(first) == 16
    assert "jane" not in first


def test_hash_user_id_treats_numbers_and_strings_alike():
    assert t.hash_user_id(42, DEV) == t.hash_user_id("42", DEV)


def test_hash_user_id_depends_on_secret_and_user():
    assert t.hash_user_id(1, DEV) != t.hash_user_id(2, DEV)
    assert t.hash_user_id(1, DEV) != t.hash_user_id(1, {"TRACE_HASH_SECRET": "other"})


def test_hash_user_id_without_secret_is_anon_not_a_plain_hash():
    assert t.hash_user_id(7, {}) == "anon"


@pytest.mark.parametrize("user_id", [None, ""])
def test_hash_user_id_no_user(user_id):
    assert t.hash_user_id(user_id, DEV) is None


# ── redaction ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,gone",
    [
        ("mail me at jane.doe+jobs@example.co.uk please", "jane.doe"),
        ("call +91 98765 43210 today", "98765"),
        ("call (555) 123-4567 today", "123-4567"),
        ("id 1234567890123", "1234567890"),
        ("key gsk_abcdefghijklmnopqrstuvwxyz123456", "abcdefghijkl"),
        ("key ls__abcdefghijklmnopqrstuvwxyz123456", "abcdefghijkl"),
        ("Authorization: Bearer abcdefghijklmnop1234", "abcdefghijklmnop1234"),
        ("https://x.test/a?token=SECRETVALUE&page=2", "SECRETVALUE"),
    ],
)
def test_redact_text_removes_sensitive_values(text, gone):
    assert gone not in t.redact_text(text)


def test_redact_text_keeps_the_rest_of_the_url_query():
    out = t.redact_text("https://x.test/a?token=SECRETVALUE&page=2")
    assert "page=2" in out
    assert "token=[redacted]" in out


def test_redact_text_keeps_year_ranges_and_plain_text():
    text = "Worked at Acme 2020 - 2024 as a data analyst."
    assert t.redact_text(text) == text


def test_redact_text_leaves_non_strings_alone():
    assert t.redact_text(5) == 5
    assert t.redact_text(None) is None
    assert t.redact_text({"a": 1}) == {"a": 1}


def test_anonymizer_masks_nested_payloads():
    anonymizer = t.build_anonymizer()
    assert anonymizer is not None
    data = {"inputs": {"resume": "write to jane@example.com", "n": [1, "ok"]}}
    out = anonymizer(data)
    assert "jane@example.com" not in str(out)
    assert "[email]" in out["inputs"]["resume"]
    assert out["inputs"]["n"] == [1, "ok"]


# ── metadata and run config ──────────────────────────────────


def test_clean_metadata_drops_unknown_keys_and_non_scalars():
    out = t.clean_metadata(
        {
            "model": "openai/gpt-oss-120b",
            "resume_text": "secret resume",
            "email": "a@b.co",
            "top_k": 5,
            "request_id": {"nested": "x"},
            "collection": "",
        }
    )
    assert out == {"model": "openai/gpt-oss-120b", "top_k": 5}


def test_clean_metadata_redacts_and_shortens_strings():
    out = t.clean_metadata({"request_id": "a@b.co " + "x" * 300})
    assert "a@b.co" not in out["request_id"]
    assert len(out["request_id"]) <= 100


def test_clean_metadata_handles_none():
    assert t.clean_metadata(None) == {}


def test_build_run_config_for_an_agent():
    cfg = t.build_run_config(
        "candidate",
        "resume_parser",
        user_id="jane-doe",
        request_id="req-1",
        prompt_version="rubric-v1",
        model="openai/gpt-oss-120b",
        role_family="technical",
        environ=DEV,
    )
    assert cfg["run_name"] == "candidate.resume_parser"
    assert cfg["tags"] == [
        "area:candidate",
        "agent:resume_parser",
        "env:dev",
        "prompt:rubric-v1",
        "model:openai/gpt-oss-120b",
        "role_family:technical",
    ]
    md = cfg["metadata"]
    assert md["user_hash"] == t.hash_user_id("jane-doe", DEV)
    assert md["request_id"] == "req-1"
    assert md["prompt_version"] == "rubric-v1"
    assert "jane-doe" not in str(cfg)


def test_build_run_config_marks_eval_and_production():
    cfg = t.build_run_config("shared", "evaluation", eval_run=True, environ=PROD)
    assert "eval" in cfg["tags"]
    assert "env:prod" in cfg["tags"]


def test_build_run_config_for_graph_has_no_agent_tags():
    cfg = t.build_run_config("graph", "interview", environ=DEV)
    assert cfg["run_name"] == "graph.interview"
    assert cfg["tags"] == ["env:dev"]


def test_build_run_config_drops_bad_labels_instead_of_raising():
    cfg = t.build_run_config(
        "candidate",
        "resume_parser",
        prompt_version="Jane Doe's resume!",
        model="jane@example.com",
        role_family="made-up",
        environ=DEV,
    )
    assert cfg["tags"] == ["area:candidate", "agent:resume_parser", "env:dev"]
    assert "jane" not in str(cfg["tags"]).lower()


@pytest.mark.parametrize(
    "area,name",
    [("nonsense", "x"), ("candidate", "Has Spaces"), ("candidate", ""), ("", "x")],
)
def test_build_run_config_unknown_names_become_unknown(area, name):
    assert t.build_run_config(area, name, environ=DEV)["run_name"] == "unknown"


def test_build_run_config_ignores_disallowed_extra_metadata():
    cfg = t.build_run_config(
        "rag",
        "retrieve.behavioral_questions",
        extra_metadata={"collection": "behavioral_questions", "query": "my SSN"},
        environ=DEV,
    )
    assert cfg["metadata"] == {"collection": "behavioral_questions"}


def test_build_run_config_without_user_has_no_user_hash():
    cfg = t.build_run_config("shared", "evaluation", environ=DEV)
    assert "user_hash" not in cfg["metadata"]


def test_mcp_run_metadata_never_holds_result_text():
    md = t.mcp_run_metadata("brave", "web_search", False, "timeout", 1234.567)
    assert md == {
        "mcp_server": "brave",
        "mcp_tool": "web_search",
        "mcp_ok": False,
        "mcp_error_code": "timeout",
        "duration_ms": 1234.6,
    }


def test_mcp_run_metadata_skips_missing_values():
    md = t.mcp_run_metadata("brave", "web_search", True)
    assert md == {"mcp_server": "brave", "mcp_tool": "web_search", "mcp_ok": True}


# ── client and configuration warnings ────────────────────────


def test_build_client_is_none_when_tracing_off_or_unkeyed():
    assert t.build_client({"LANGCHAIN_TRACING_V2": "false"}) is None
    assert t.build_client({"LANGCHAIN_TRACING_V2": "true"}) is None


def test_config_warnings_empty_when_tracing_is_off():
    assert t.config_warnings({"APP_ENV": "production"}) == []


def test_config_warnings_for_a_good_dev_setup():
    environ = {
        **DEV,
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "present",
        "LANGCHAIN_PROJECT": "talent-hub-development",
    }
    assert t.config_warnings(environ) == []


def test_config_warnings_flag_the_old_project_name_and_missing_key():
    warnings = t.config_warnings({**DEV, "LANGCHAIN_TRACING_V2": "true"})
    assert any("no LangSmith API key" in w for w in warnings)
    assert any("expected 'talent-hub-development'" in w for w in warnings)


def test_config_warnings_flag_visible_content_in_production():
    environ = {
        **PROD,
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "present",
        "LANGCHAIN_PROJECT": "talent-hub-production",
    }
    warnings = t.config_warnings(environ)
    assert len(warnings) == 1
    assert "inputs or outputs" in warnings[0]


def test_config_warnings_clean_production_setup():
    environ = {
        **PROD,
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "present",
        "LANGCHAIN_PROJECT": "talent-hub-production",
        "LANGSMITH_HIDE_INPUTS": "true",
        "LANGSMITH_HIDE_OUTPUTS": "true",
        "LANGSMITH_TRACING_SAMPLING_RATE": "0.1",
    }
    assert t.config_warnings(environ) == []


def test_config_warnings_flag_missing_hash_secret_and_bad_rate():
    environ = {
        "APP_ENV": "production",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "present",
        "LANGCHAIN_PROJECT": "talent-hub-production",
        "LANGSMITH_HIDE_INPUTS": "true",
        "LANGSMITH_HIDE_OUTPUTS": "true",
        "LANGSMITH_TRACING_SAMPLING_RATE": "ten percent",
    }
    warnings = t.config_warnings(environ)
    assert any("TRACE_HASH_SECRET" in w for w in warnings)
    assert any("not a number" in w for w in warnings)


def test_config_warnings_never_contain_the_key_value():
    environ = {
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "ls__TOPSECRETVALUE1234567890",
    }
    assert "TOPSECRET" not in " ".join(t.config_warnings(environ))
