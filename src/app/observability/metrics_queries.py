"""Consultas de metricas operacionais (spec FR-027 a FR-034, research.md #7 e #14).

Calculadas sob demanda sobre dados ja persistidos -- `audit_events` (`llm_call`, `agent_call`,
`tool_call`, `http_request`, `grounding_evaluated`), `agent_executions` e `sessions` -- nunca
reprocessando logs brutos. A granularidade por usuario/sessao vive AQUI (alta cardinalidade) e
nunca como label do Prometheus (research.md #6). A agregacao e feita em Python sobre `find`
(escala de uso interno; compativel com mongomock).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.settings import get_settings
from app.repository.sessions_repository import SessionStatus
from app.schemas.metrics import (
    GroundingScoreDistribution,
    LatencyMetric,
    LatencyPercentiles,
    RpmMetric,
    SessionsPerUserMetric,
    TokensPerUserSessionMetric,
    TpmMetric,
)

_MAX_ROWS = 200_000
_UNKNOWN_USER = "unknown"


def _cutoff_iso(window_minutes: int) -> str:
    """Limite inferior no mesmo formato `...Z` que `model_dump(mode="json")` grava em
    `created_at`/`started_at` (comparacao lexicografica de strings ISO-8601 UTC)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def percentile(values: list[float], p: float) -> float:
    """Percentil pelo metodo nearest-rank (p em 0-100); 0.0 para uma lista vazia."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def _percentiles(values: list[float]) -> LatencyPercentiles:
    return LatencyPercentiles(
        p50_ms=percentile(values, 50), p95_ms=percentile(values, 95), p99_ms=percentile(values, 99)
    )


async def _events(db: AsyncIOMotorDatabase, event_type: str, window_minutes: int) -> list[dict]:
    return (
        await db["audit_events"]
        .find({"event_type": event_type, "created_at": {"$gte": _cutoff_iso(window_minutes)}})
        .to_list(length=_MAX_ROWS)
    )


async def _session_users(db: AsyncIOMotorDatabase, session_ids: set[str]) -> dict[str, str]:
    if not session_ids:
        return {}
    docs = (
        await db["sessions"]
        .find({"session_id": {"$in": sorted(session_ids)}})
        .to_list(length=_MAX_ROWS)
    )
    return {d["session_id"]: d["user_id"] for d in docs}


def _by_user_rate(
    totals_by_session: dict[str | None, float],
    users: dict[str, str],
    window_minutes: int,
    user_id: str | None,
) -> tuple[float, dict[str, float] | None]:
    per_user: dict[str, float] = defaultdict(float)
    overall = 0.0
    for session_id, amount in totals_by_session.items():
        owner = users.get(session_id or "", _UNKNOWN_USER)
        if user_id is not None and owner != user_id:
            continue
        overall += amount
        per_user[owner] += amount
    rates = {user: total / window_minutes for user, total in sorted(per_user.items())}
    return overall / window_minutes, (None if user_id is not None else rates)


async def tpm(
    db: AsyncIOMotorDatabase, window_minutes: int = 60, user_id: str | None = None
) -> TpmMetric:
    events = await _events(db, "llm_call", window_minutes)
    totals: dict[str | None, float] = defaultdict(float)
    for event in events:
        tokens = (event.get("safe_metadata") or {}).get("total_tokens", 0)
        totals[event.get("session_id")] += float(tokens)
    users = await _session_users(db, {s for s in totals if s})
    overall, by_user = _by_user_rate(totals, users, window_minutes, user_id)
    return TpmMetric(window_minutes=window_minutes, overall_tpm=overall, by_user=by_user)


async def rpm(
    db: AsyncIOMotorDatabase, window_minutes: int = 60, user_id: str | None = None
) -> RpmMetric:
    executions = (
        await db["agent_executions"]
        .find({"started_at": {"$gte": _cutoff_iso(window_minutes)}})
        .to_list(length=_MAX_ROWS)
    )
    totals: dict[str | None, float] = defaultdict(float)
    for execution in executions:
        totals[execution.get("session_id")] += 1.0
    users = await _session_users(db, {s for s in totals if s})
    overall, by_user = _by_user_rate(totals, users, window_minutes, user_id)
    return RpmMetric(window_minutes=window_minutes, overall_rpm=overall, by_user=by_user)


def _error_rates(events: list[dict]) -> dict[str, float]:
    totals: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    for event in events:
        name = event.get("actor_name", "unknown")
        totals[name] += 1
        if event.get("status") == "error":
            errors[name] += 1
    return {name: errors[name] / total for name, total in sorted(totals.items())}


def _durations_by(events: list[dict]) -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for event in events:
        if event.get("duration_ms") is not None:
            grouped[event.get("actor_name", "unknown")].append(float(event["duration_ms"]))
    return grouped


async def latency(
    db: AsyncIOMotorDatabase,
    window_minutes: int = 60,
    agent: str | None = None,
    endpoint: str | None = None,
) -> LatencyMetric:
    agent_events = await _events(db, "agent_call", window_minutes)
    http_events = await _events(db, "http_request", window_minutes)
    tool_events = await _events(db, "tool_call", window_minutes)

    if agent is not None:
        agent_events = [e for e in agent_events if e.get("actor_name") == agent]
    if endpoint is not None:
        http_events = [e for e in http_events if e.get("actor_name") == endpoint]

    by_agent = _durations_by(agent_events)
    by_endpoint = _durations_by(http_events)

    if agent is not None:
        headline = by_agent.get(agent, [])
    elif endpoint is not None:
        headline = by_endpoint.get(endpoint, [])
    else:
        headline = [d for values in by_endpoint.values() for d in values]
    overall = _percentiles(headline)

    return LatencyMetric(
        window_minutes=window_minutes,
        p50_ms=overall.p50_ms,
        p95_ms=overall.p95_ms,
        p99_ms=overall.p99_ms,
        by_agent=None if agent is not None else {k: _percentiles(v) for k, v in by_agent.items()},
        by_endpoint=(
            None if endpoint is not None else {k: _percentiles(v) for k, v in by_endpoint.items()}
        ),
        error_rate_by_agent=_error_rates(agent_events),
        error_rate_by_tool=_error_rates(tool_events),
    )


async def sessions_per_user(
    db: AsyncIOMotorDatabase, window_minutes: int = 60
) -> list[SessionsPerUserMetric]:
    """Sessoes nao encerradas com atividade dentro da janela, por usuario."""
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
    docs = (
        await db["sessions"]
        .find({"status": {"$ne": SessionStatus.CLOSED.value}})
        .to_list(length=_MAX_ROWS)
    )
    counts: dict[str, int] = defaultdict(int)
    for doc in docs:
        updated = _parse(doc.get("updated_at")) or _parse(doc.get("created_at"))
        if updated is not None and updated >= cutoff:
            counts[doc["user_id"]] += 1
    return [
        SessionsPerUserMetric(user_id=user, active_session_count=count)
        for user, count in sorted(counts.items())
    ]


async def tokens_per_user_session(
    db: AsyncIOMotorDatabase,
    window_minutes: int = 60,
    user_id: str | None = None,
    session_id: str | None = None,
) -> list[TokensPerUserSessionMetric]:
    events = await _events(db, "llm_call", window_minutes)
    totals: dict[str, int] = defaultdict(int)
    for event in events:
        session = event.get("session_id")
        if not session or (session_id is not None and session != session_id):
            continue
        totals[session] += int((event.get("safe_metadata") or {}).get("total_tokens", 0))
    users = await _session_users(db, set(totals))
    rows = [
        TokensPerUserSessionMetric(
            user_id=users.get(session, _UNKNOWN_USER),
            session_id=session,
            total_tokens=total,
            window_minutes=window_minutes,
        )
        for session, total in sorted(totals.items())
    ]
    if user_id is not None:
        rows = [r for r in rows if r.user_id == user_id]
    return rows


async def grounding_scores(
    db: AsyncIOMotorDatabase, window_minutes: int = 60
) -> GroundingScoreDistribution:
    events = await _events(db, "grounding_evaluated", window_minutes)
    distribution = {str(score): 0 for score in range(6)}
    threshold = get_settings().grounding_min_score
    below = 0
    total = 0
    for event in events:
        score = (event.get("safe_metadata") or {}).get("score")
        if score is None or str(int(score)) not in distribution:
            continue
        distribution[str(int(score))] += 1
        total += 1
        if int(score) < threshold:
            below += 1
    return GroundingScoreDistribution(
        window_minutes=window_minutes,
        distribution=distribution,
        below_threshold_count=below,
        total_evaluated=total,
    )
