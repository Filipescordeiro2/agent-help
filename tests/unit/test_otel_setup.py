"""T023: inicializacao do OpenTelemetry -- idempotencia, nomes Prometheus, degradacao graciosa."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from prometheus_client import generate_latest

import app.main as app_main
import app.observability.otel as otel
from app.config.settings import get_settings
from app.observability.metrics import get_instruments


def test_init_telemetry_is_idempotent() -> None:
    otel.init_telemetry()
    provider = otel._meter_provider
    otel.init_telemetry()
    assert otel._meter_provider is provider
    assert otel._initialized is True


def test_init_telemetry_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel, "_initialized", False)
    monkeypatch.setattr(otel, "_meter_provider", None)
    monkeypatch.setattr(get_settings(), "otel_enabled", False)
    otel.init_telemetry()
    assert otel._initialized is False
    assert otel._meter_provider is None


def test_instruments_are_exposed_with_contract_names_and_low_cardinality_labels() -> None:
    otel.init_telemetry()
    instruments = get_instruments()
    instruments.llm_tokens.add(3, {"agent": "a", "model": "m", "token_type": "prompt"})
    instruments.grounding_score.record(4, {"agent": "a"})
    instruments.http_request_duration.record(0.1, {"endpoint": "/x"})
    instruments.agent_call_duration.record(0.2, {"agent": "a", "status": "ok"})
    instruments.feedback_proposals.add(
        1, {"action_type": "CREATE_SKILL", "status": "PENDING_HUMAN_REVIEW"}
    )
    text = generate_latest().decode()

    for name in (
        "getnet_llm_tokens_total",
        "getnet_grounding_score_bucket",
        "getnet_http_request_duration_seconds_bucket",
        "getnet_agent_call_duration_seconds_bucket",
        "getnet_feedback_proposals_total",
    ):
        assert name in text, name
    assert 'le="5.0"' in text  # buckets explicitos 0..5 do score de grounding
    for forbidden in ("user_id=", "session_id=", "message_id="):
        assert forbidden not in text


def test_http_duration_histogram_has_no_agent_label() -> None:
    otel.init_telemetry()
    get_instruments().http_request_duration.record(0.05, {"endpoint": "/only-endpoint"})
    lines = [
        line
        for line in generate_latest().decode().splitlines()
        if line.startswith("getnet_http_request_duration_seconds_bucket")
        and "/only-endpoint" in line
    ]
    assert lines and all("agent=" not in line for line in lines)


def test_unreachable_collector_never_raises_and_is_fast() -> None:
    started = time.monotonic()
    provider = otel.build_tracer_provider(
        Resource.create({"service.name": "t"}), "http://127.0.0.1:9"
    )
    with provider.get_tracer("t").start_as_current_span("s"):
        pass
    provider.force_flush(timeout_millis=500)
    assert time.monotonic() - started < 10


def test_app_starts_normally_with_unreachable_collector(
    test_db, monkeypatch: pytest.MonkeyPatch, internal_token: str
) -> None:
    async def _noop(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(get_settings(), "otel_exporter_otlp_endpoint", "http://127.0.0.1:9")
    monkeypatch.setattr(app_main, "create_indexes", _noop)
    with TestClient(app_main.app) as client:
        assert client.get("/health").status_code == 200
