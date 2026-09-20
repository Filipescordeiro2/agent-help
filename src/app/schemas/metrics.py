"""Schemas de resposta dos endpoints JSON de metricas (contracts/schemas.md, FR-027 a FR-034)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TpmMetric(BaseModel):
    window_minutes: int
    overall_tpm: float
    by_user: dict[str, float] | None = Field(
        default=None, description="omitido quando `user_id` e informado na query"
    )


class RpmMetric(BaseModel):
    window_minutes: int
    overall_rpm: float
    by_user: dict[str, float] | None = Field(
        default=None, description="omitido quando `user_id` e informado na query"
    )


class LatencyPercentiles(BaseModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float


class LatencyMetric(BaseModel):
    window_minutes: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    by_agent: dict[str, LatencyPercentiles] | None = None
    by_endpoint: dict[str, LatencyPercentiles] | None = None
    error_rate_by_agent: dict[str, float] = Field(default_factory=dict)
    error_rate_by_tool: dict[str, float] = Field(default_factory=dict)


class SessionsPerUserMetric(BaseModel):
    user_id: str
    active_session_count: int


class TokensPerUserSessionMetric(BaseModel):
    user_id: str
    session_id: str
    total_tokens: int
    window_minutes: int


class GroundingScoreDistribution(BaseModel):
    window_minutes: int
    distribution: dict[str, int] = Field(description='chaves "0" a "5"')
    below_threshold_count: int
    total_evaluated: int
