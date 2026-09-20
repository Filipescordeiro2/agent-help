"""T051: teste unitario do filtro de allowlist de logging."""

from __future__ import annotations

from app.observability.logging import _LOGGABLE_KEYS, _allowlist_filter


def test_allowlist_filter_drops_keys_outside_allowlist() -> None:
    event_dict = {
        "event": "login_attempt",
        "session_id": "sess_1",
        "api_key": "sk-should-never-be-logged",
        "password": "should-never-be-logged",
        "user_full_name": "should-never-be-logged",
    }

    filtered = _allowlist_filter(None, "info", event_dict)

    assert "api_key" not in filtered
    assert "password" not in filtered
    assert "user_full_name" not in filtered
    assert filtered["event"] == "login_attempt"
    assert filtered["session_id"] == "sess_1"


def test_allowlist_filter_drops_none_values() -> None:
    event_dict = {"event": "x", "session_id": None}
    filtered = _allowlist_filter(None, "info", event_dict)
    assert "session_id" not in filtered


def test_sensitive_field_names_are_not_in_allowlist() -> None:
    for sensitive in ("api_key", "password", "senha", "token", "secret"):
        assert sensitive not in _LOGGABLE_KEYS


def test_new_observability_keys_are_loggable_and_secrets_still_dropped() -> None:
    event_dict = {
        "event": "x",
        "grounding_score": 4,
        "run_id": "r",
        "proposal_id": "p",
        "trigger_type": "MANUAL",
        "action_type": "CREATE_SKILL",
        "model": "m",
        "total_tokens": 15,
        "comment": "texto livre do usuario nunca deve ser logado",
        "api_key": "sk-secret",
    }
    filtered = _allowlist_filter(None, "info", event_dict)
    for key in ("grounding_score", "run_id", "proposal_id", "trigger_type", "action_type", "model"):
        assert key in filtered
    assert "comment" not in filtered and "api_key" not in filtered


def test_trace_ids_are_injected_inside_an_active_span() -> None:
    from opentelemetry.sdk.trace import TracerProvider

    from app.observability.logging import _add_trace_ids

    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        enriched = _add_trace_ids(None, "info", {"event": "x"})
        assert enriched["trace_id"] == format(span.get_span_context().trace_id, "032x")
        assert len(enriched["span_id"]) == 16
    assert "trace_id" not in _add_trace_ids(None, "info", {"event": "x"})
    assert {"trace_id", "span_id"} <= _LOGGABLE_KEYS


def test_get_context_ids_reflects_bound_context() -> None:
    from app.observability.logging import bind_context, get_context_ids

    bind_context(request_id="req-1", session_id="sess-1")
    ids = get_context_ids()
    assert ids["request_id"] == "req-1" and ids["session_id"] == "sess-1"
