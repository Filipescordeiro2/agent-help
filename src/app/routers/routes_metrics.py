"""Endpoints JSON de metricas operacionais (spec FR-027 a FR-034) e `GET /metrics` (Prometheus).

Rotas finas: delegam a `app/observability/metrics_queries.py`. Todas exigem o token interno.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from prometheus_client import generate_latest

from app.observability import metrics_queries as queries
from app.routers.deps import get_db
from app.schemas.metrics import (
    GroundingScoreDistribution,
    LatencyMetric,
    RpmMetric,
    SessionsPerUserMetric,
    TokensPerUserSessionMetric,
    TpmMetric,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

_WINDOW = Query(default=60, ge=1, le=10_080, description="janela em minutos")


@router.get("/tpm", response_model=TpmMetric, response_model_exclude_none=True)
async def get_tpm(
    user_id: str | None = None,
    window_minutes: int = _WINDOW,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> TpmMetric:
    return await queries.tpm(db, window_minutes, user_id)


@router.get("/rpm", response_model=RpmMetric, response_model_exclude_none=True)
async def get_rpm(
    user_id: str | None = None,
    window_minutes: int = _WINDOW,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> RpmMetric:
    return await queries.rpm(db, window_minutes, user_id)


@router.get("/latency", response_model=LatencyMetric, response_model_exclude_none=True)
async def get_latency(
    agent: str | None = None,
    endpoint: str | None = None,
    window_minutes: int = _WINDOW,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> LatencyMetric:
    return await queries.latency(db, window_minutes, agent, endpoint)


@router.get("/sessions-per-user", response_model=list[SessionsPerUserMetric])
async def get_sessions_per_user(
    window_minutes: int = _WINDOW, db: AsyncIOMotorDatabase = Depends(get_db)
) -> list[SessionsPerUserMetric]:
    return await queries.sessions_per_user(db, window_minutes)


@router.get("/tokens-per-user-session", response_model=list[TokensPerUserSessionMetric])
async def get_tokens_per_user_session(
    user_id: str | None = None,
    session_id: str | None = None,
    window_minutes: int = _WINDOW,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[TokensPerUserSessionMetric]:
    return await queries.tokens_per_user_session(db, window_minutes, user_id, session_id)


@router.get("/grounding-scores", response_model=GroundingScoreDistribution)
async def get_grounding_scores(
    window_minutes: int = _WINDOW, db: AsyncIOMotorDatabase = Depends(get_db)
) -> GroundingScoreDistribution:
    return await queries.grounding_scores(db, window_minutes)


# `GET /metrics` fica FORA de /api/v1 (convencao de scraping do Prometheus), mas sob o mesmo
# boundary de confianca interna: `internal_trust_boundary_middleware` exige o token (research #11).
prometheus_router = APIRouter(tags=["metrics"])

# Formato de texto classico (0.0.4), aceito por qualquer versao do Prometheus (contracts/api.md).
_PROMETHEUS_TEXT_FORMAT = "text/plain; version=0.0.4; charset=utf-8"


@prometheus_router.get(
    "/metrics",
    summary="Metricas no formato Prometheus (scraping)",
    response_class=Response,
)
async def prometheus_metrics() -> Response:
    return Response(content=generate_latest(), media_type=_PROMETHEUS_TEXT_FORMAT)
