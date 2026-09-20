"""T047: teste unitario do carregamento de configuracao."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)


def test_settings_fails_when_required_vars_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_loads_from_explicit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    settings = Settings(
        openrouter_api_key="key",
        openrouter_model="model",
        embedding_model="embed-model",
        embedding_dimensions=1536,
        internal_service_token="token",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.embedding_dimensions == 1536
    assert settings.log_level == "INFO"
    assert settings.max_graph_iterations > 0


def test_settings_has_no_hardcoded_secret_defaults() -> None:
    for field_name in ("openrouter_model", "embedding_model", "internal_service_token"):
        field = Settings.model_fields[field_name]
        assert field.is_required(), f"{field_name} nao deveria ter default"


def _valid(**overrides) -> Settings:
    return Settings(
        openrouter_api_key="key",
        openrouter_model="model",
        embedding_model="embed-model",
        embedding_dimensions=8,
        internal_service_token="token",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,
    )


def test_new_feature_settings_have_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GROUNDING_MIN_SCORE",
        "OPENROUTER_MODEL_GROUNDING",
        "FEEDBACK_AGENT_INTERVAL_MINUTES",
        "FEEDBACK_AGENT_SCHEDULE_ENABLED",
        "FEEDBACK_AGENT_MAX_BATCH_SIZE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = _valid()
    assert settings.grounding_min_score == 3
    assert settings.openrouter_model_grounding is None
    assert settings.feedback_agent_interval_minutes == 1440
    assert settings.feedback_agent_schedule_enabled is True
    assert settings.feedback_agent_max_batch_size == 100
    assert settings.otel_exporter_otlp_endpoint is None and settings.otel_enabled is True


@pytest.mark.parametrize("score", [0, 3, 5])
def test_grounding_min_score_accepts_the_whole_zero_to_five_range(score: int) -> None:
    assert _valid(grounding_min_score=score).grounding_min_score == score


@pytest.mark.parametrize("score", [-1, 6, 100])
def test_grounding_min_score_outside_zero_to_five_fails_at_startup(score: int) -> None:
    with pytest.raises(ValidationError):
        _valid(grounding_min_score=score)


@pytest.mark.parametrize(
    "field", ["feedback_agent_interval_minutes", "feedback_agent_max_batch_size"]
)
def test_feedback_agent_positive_settings_reject_zero_and_negative(field: str) -> None:
    for value in (0, -5):
        with pytest.raises(ValidationError):
            _valid(**{field: value})
