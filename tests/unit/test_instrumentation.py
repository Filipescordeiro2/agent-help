"""T024: instrumentacao de nos, agentes e ferramentas (span, metrica, auditoria, log)."""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

import app.observability.audit as audit_module
from app.observability import otel
from app.observability.instrumentation import (
    instrument_node,
    instrument_tool_call,
    run_instrumented_agent,
)
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata


@pytest.fixture(autouse=True)
def _telemetry() -> None:
    otel.init_telemetry()


async def _events(db, event_type: str) -> list[dict]:
    return await db["audit_events"].find({"event_type": event_type}).to_list(length=50)


class _Agent:
    def __init__(self, name: str, status: Status, error_code: str | None = None) -> None:
        self.name = name
        self._status = status
        self._error_code = error_code

    async def handle(self, _state) -> AgentResponse:
        return AgentResponse(
            status=self._status,
            agent=self.name,
            message="m",
            metadata=ResponseMetadata(
                execution_id="e", confidence=0.5, error_code=self._error_code
            ),
        )


async def test_instrument_node_emits_audit_event_and_binds_state_ids(test_db) -> None:
    async def node(state):
        state["node_name"] = "x"
        return state

    wrapped = instrument_node("unit_node", node)
    state = {"session_id": "s1", "execution_id": "e1", "request_id": "r1", "message_id": "m1"}
    result = await wrapped(state)

    assert result is state
    events = await _events(test_db, "node_call")
    assert len(events) == 1
    event = events[0]
    assert event["actor"] == "system" and event["actor_name"] == "unit_node"
    assert event["node_name"] == "unit_node" and event["status"] == "ok"
    assert event["session_id"] == "s1" and event["execution_id"] == "e1"
    assert event["duration_ms"] >= 0


async def test_instrument_node_propagates_exception_with_error_status(test_db) -> None:
    async def node(_state):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await instrument_node("failing_node", node)({"session_id": "s"})

    event = (await _events(test_db, "node_call"))[0]
    assert event["status"] == "error" and event["error_code"] == "ValueError"


async def test_audit_failure_never_propagates(test_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(audit_module, "emit_audit_event", broken)

    async def node(state):
        return state

    state = {"session_id": "s"}
    assert await instrument_node("n", node)(state) is state


async def test_run_instrumented_agent_records_duration_and_error_status(test_db) -> None:
    ok = await run_instrumented_agent(_Agent("agent_ok", Status.OK), {"session_id": "s"})
    err = await run_instrumented_agent(
        _Agent("agent_err", Status.ERROR, "SOME_ERROR"), {"session_id": "s"}
    )

    assert ok.status == Status.OK and err.status == Status.ERROR
    events = {e["actor_name"]: e for e in await _events(test_db, "agent_call")}
    assert events["agent_ok"]["status"] == "ok" and events["agent_ok"]["actor"] == "agent"
    assert events["agent_err"]["status"] == "error"
    assert events["agent_err"]["error_code"] == "SOME_ERROR"
    assert events["agent_ok"]["safe_metadata"]["status"] == "OK"

    text = generate_latest().decode()
    assert 'getnet_agent_call_duration_seconds_bucket{agent="agent_ok"' in text
    assert 'status="error"' in text
    assert 'getnet_agent_errors_total{agent="agent_err",error_code="SOME_ERROR"' in text


async def test_run_instrumented_agent_propagates_exceptions(test_db) -> None:
    class Boom:
        name = "boom_agent"

        async def handle(self, _state):
            raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await run_instrumented_agent(Boom(), {"session_id": "s"})
    event = next(e for e in await _events(test_db, "agent_call") if e["actor_name"] == "boom_agent")
    assert event["status"] == "error" and event["error_code"] == "RuntimeError"


async def test_instrument_tool_call_success_and_failure(test_db) -> None:
    async def fine():
        return 42

    async def bad():
        raise TimeoutError("slow")

    assert await instrument_tool_call("tool_x", fine) == 42
    with pytest.raises(TimeoutError):
        await instrument_tool_call("tool_y", bad)

    events = {e["actor_name"]: e for e in await _events(test_db, "tool_call")}
    assert events["tool_x"]["status"] == "ok" and events["tool_x"]["actor"] == "tool"
    assert (
        events["tool_y"]["status"] == "error" and events["tool_y"]["error_code"] == "TimeoutError"
    )
    tool_lines = [
        line
        for line in generate_latest().decode().splitlines()
        if line.startswith("getnet_tool_errors_total") and 'tool="tool_y"' in line
    ]
    assert tool_lines and 'error_code="TimeoutError"' in tool_lines[0]


async def test_no_database_connection_skips_auditing_silently() -> None:
    async def node(state):
        return state

    state = {"session_id": "s"}
    assert await instrument_node("n", node)(state) is state
