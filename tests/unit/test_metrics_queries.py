"""T079: consultas de metricas operacionais sobre dados semeados (spec FR-027 a FR-034)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import get_settings
from app.observability import metrics_queries as q
from app.repository.audit_events_repository import AuditEvent, AuditEventsRepository
from app.repository.executions_repository import Execution, ExecutionsRepository
from app.repository.sessions_repository import (
    Session,
    SessionsRepository,
    SessionStatus,
)


def ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


async def add_event(
    db,
    event_type: str,
    *,
    actor: str = "system",
    name: str = "x",
    session_id: str | None = None,
    status: str = "ok",
    duration_ms: float | None = None,
    metadata: dict | None = None,
    minutes_ago: float = 1,
) -> None:
    await AuditEventsRepository(db).insert(
        AuditEvent(
            event_id=str(uuid.uuid4()),
            request_id="r",
            session_id=session_id,
            actor=actor,  # type: ignore[arg-type]
            actor_name=name,
            event_type=event_type,
            status=status,
            duration_ms=duration_ms,
            safe_metadata=metadata or {},
            created_at=ago(minutes_ago),
        )
    )


async def add_session(
    db, session_id: str, user_id: str, status=SessionStatus.ACTIVE, minutes_ago: float = 1
) -> None:
    await SessionsRepository(db).insert(
        Session(
            session_id=session_id,
            user_id=user_id,
            status=status,
            updated_at=ago(minutes_ago),
            created_at=ago(minutes_ago),
        )
    )


async def add_execution(db, session_id: str, minutes_ago: float = 1) -> None:
    await ExecutionsRepository(db).insert(
        Execution(
            execution_id=str(uuid.uuid4()),
            session_id=session_id,
            message_id="m",
            started_at=ago(minutes_ago),
        )
    )


async def add_llm_call(db, session_id: str | None, tokens: int, minutes_ago: float = 1) -> None:
    await add_event(
        db,
        "llm_call",
        session_id=session_id,
        metadata={"total_tokens": tokens, "prompt_tokens": tokens - 1, "completion_tokens": 1},
        minutes_ago=minutes_ago,
    )


def test_percentile_uses_nearest_rank() -> None:
    values = [float(v) for v in range(10, 101, 10)]
    assert q.percentile(values, 50) == 50.0
    assert q.percentile(values, 95) == 100.0
    assert q.percentile(values, 99) == 100.0
    assert q.percentile([7.0], 99) == 7.0
    assert q.percentile([], 50) == 0.0
    assert q.percentile([5.0, 1.0, 3.0], 50) == 3.0


async def test_tpm_overall_and_by_user_respect_the_window(test_db) -> None:
    await add_session(test_db, "s1", "u1")
    await add_session(test_db, "s2", "u2")
    await add_llm_call(test_db, "s1", 30)
    await add_llm_call(test_db, "s1", 30)
    await add_llm_call(test_db, "s2", 60)
    await add_llm_call(test_db, "s1", 9999, minutes_ago=120)  # fora da janela

    metric = await q.tpm(test_db, window_minutes=60)

    assert metric.window_minutes == 60
    assert metric.overall_tpm == pytest.approx(2.0)
    assert metric.by_user == {"u1": pytest.approx(1.0), "u2": pytest.approx(1.0)}


async def test_tpm_filtered_by_user_omits_the_breakdown(test_db) -> None:
    await add_session(test_db, "s1", "u1")
    await add_session(test_db, "s2", "u2")
    await add_llm_call(test_db, "s1", 60)
    await add_llm_call(test_db, "s2", 120)

    metric = await q.tpm(test_db, window_minutes=60, user_id="u1")

    assert metric.overall_tpm == pytest.approx(1.0) and metric.by_user is None


async def test_rpm_counts_executions_per_window_and_user(test_db) -> None:
    await add_session(test_db, "s1", "u1")
    await add_session(test_db, "s2", "u2")
    for _ in range(3):
        await add_execution(test_db, "s1")
    await add_execution(test_db, "s2")
    await add_execution(test_db, "s1", minutes_ago=500)

    metric = await q.rpm(test_db, window_minutes=10)

    assert metric.overall_rpm == pytest.approx(0.4)
    assert metric.by_user == {"u1": pytest.approx(0.3), "u2": pytest.approx(0.1)}
    assert (await q.rpm(test_db, window_minutes=10, user_id="u2")).overall_rpm == pytest.approx(0.1)


async def test_latency_percentiles_by_agent_and_endpoint_with_error_rates(test_db) -> None:
    for duration in (100, 200, 300, 400):
        await add_event(test_db, "agent_call", actor="agent", name="agent_a", duration_ms=duration)
    await add_event(
        test_db, "agent_call", actor="agent", name="agent_a", duration_ms=500, status="error"
    )
    await add_event(test_db, "agent_call", actor="agent", name="agent_b", duration_ms=50)
    for duration in (10, 20, 30):
        await add_event(test_db, "http_request", name="/api/v1/x", duration_ms=duration)
    await add_event(test_db, "http_request", name="/api/v1/y", duration_ms=1000)
    await add_event(test_db, "tool_call", actor="tool", name="tool_t", status="error")
    await add_event(test_db, "tool_call", actor="tool", name="tool_t")

    overall = await q.latency(test_db, window_minutes=60)

    assert overall.by_agent["agent_a"].p50_ms == 300.0
    assert overall.by_agent["agent_a"].p99_ms == 500.0
    assert overall.by_agent["agent_b"].p50_ms == 50.0
    assert overall.by_endpoint["/api/v1/x"].p95_ms == 30.0
    assert overall.p99_ms == 1000.0  # cabecalho geral: todas as requisicoes HTTP
    assert overall.error_rate_by_agent == {"agent_a": pytest.approx(0.2), "agent_b": 0.0}
    assert overall.error_rate_by_tool == {"tool_t": pytest.approx(0.5)}

    only_agent = await q.latency(test_db, window_minutes=60, agent="agent_a")
    assert only_agent.by_agent is None and only_agent.p50_ms == 300.0
    assert set(only_agent.error_rate_by_agent) == {"agent_a"}

    only_endpoint = await q.latency(test_db, window_minutes=60, endpoint="/api/v1/x")
    assert only_endpoint.by_endpoint is None and only_endpoint.p50_ms == 20.0


async def test_sessions_per_user_counts_only_open_or_active_sessions_in_the_window(test_db) -> None:
    await add_session(test_db, "a", "u1", SessionStatus.ACTIVE)
    await add_session(test_db, "b", "u1", SessionStatus.OPEN)
    await add_session(test_db, "c", "u1", SessionStatus.CLOSED)
    await add_session(test_db, "d", "u2", SessionStatus.ACTIVE)
    await add_session(test_db, "e", "u2", SessionStatus.ACTIVE, minutes_ago=600)

    rows = await q.sessions_per_user(test_db, window_minutes=60)

    assert {r.user_id: r.active_session_count for r in rows} == {"u1": 2, "u2": 1}


async def test_tokens_per_user_session_with_filters(test_db) -> None:
    await add_session(test_db, "s1", "u1")
    await add_session(test_db, "s2", "u1")
    await add_session(test_db, "s3", "u2")
    for session, tokens in (("s1", 10), ("s1", 5), ("s2", 7), ("s3", 100)):
        await add_llm_call(test_db, session, tokens)

    every = await q.tokens_per_user_session(test_db, window_minutes=60)
    assert {(r.user_id, r.session_id): r.total_tokens for r in every} == {
        ("u1", "s1"): 15,
        ("u1", "s2"): 7,
        ("u2", "s3"): 100,
    }
    assert all(r.window_minutes == 60 for r in every)
    assert len(await q.tokens_per_user_session(test_db, 60, user_id="u1")) == 2
    only = await q.tokens_per_user_session(test_db, 60, session_id="s3")
    assert [(r.user_id, r.total_tokens) for r in only] == [("u2", 100)]


async def test_grounding_distribution_has_all_scores_and_counts_below_threshold(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "grounding_min_score", 3)
    for score in (5, 5, 4, 3, 2, 0):
        await add_event(test_db, "grounding_evaluated", name="grounding", metadata={"score": score})
    await add_event(
        test_db, "grounding_evaluated", name="grounding", metadata={"score": 1}, minutes_ago=999
    )

    metric = await q.grounding_scores(test_db, window_minutes=60)

    assert list(metric.distribution) == ["0", "1", "2", "3", "4", "5"]
    assert metric.distribution == {"0": 1, "1": 0, "2": 1, "3": 1, "4": 1, "5": 2}
    assert metric.total_evaluated == 6 and metric.below_threshold_count == 2


async def test_empty_windows_return_zeros_never_errors(test_db) -> None:
    assert (await q.tpm(test_db)).overall_tpm == 0.0
    assert (await q.rpm(test_db)).overall_rpm == 0.0
    latency = await q.latency(test_db)
    assert latency.p50_ms == 0.0 and latency.by_agent == {} and latency.error_rate_by_tool == {}
    assert await q.sessions_per_user(test_db) == []
    assert await q.tokens_per_user_session(test_db) == []
    grounding = await q.grounding_scores(test_db)
    assert grounding.total_evaluated == 0 and grounding.distribution["5"] == 0
