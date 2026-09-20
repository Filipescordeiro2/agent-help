"""T086: `/metrics` sob o boundary de confianca e nenhum dado sensivel nos tres sinais (FR-037)."""

from __future__ import annotations

import json

import pytest
import structlog
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.observability import otel
from app.schemas.router import Intent, RouterDecision
from tests.conftest import _build_test_app

_SECRET = "sk-test-SECRET123"
_CPF = "123.456.789-09"


def test_metrics_requires_the_internal_token(test_db, internal_token: str, fake_embeddings) -> None:
    anonymous = TestClient(_build_test_app())

    assert anonymous.get("/metrics").status_code == 403
    assert (
        anonymous.get("/metrics", headers={"X-Internal-Service-Token": "errado"}).status_code == 403
    )
    ok = anonymous.get("/metrics", headers={"X-Internal-Service-Token": internal_token})
    assert ok.status_code == 200


def test_health_and_ready_stay_public(test_db, internal_token: str, fake_embeddings) -> None:
    anonymous = TestClient(_build_test_app())
    assert anonymous.get("/health").status_code == 200
    assert anonymous.get("/ready").status_code in (200, 503)  # sem token: nunca 403


def test_secrets_and_pii_never_reach_metrics_logs_spans_or_audit_events(
    client: TestClient, fake_llm: dict, test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    otel.init_telemetry()
    exporter = InMemorySpanExporter()
    provider = otel.get_tracer_provider_instance()
    assert provider is not None
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha e 1,99% no credito.",
            "product": "maquininha",
        },
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa e 1,99% no credito.", grounded_in_sources=True
    )
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]

    with structlog.testing.capture_logs() as logs:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={
                "message": f"Qual a taxa da maquininha? Meu CPF e {_CPF} e minha chave {_SECRET}",
                "user_id": "client_xpto",
            },
        )
    assert response.status_code == 200

    metrics_text = client.get("/metrics").text
    span_dump = json.dumps(
        [
            {"name": s.name, "attributes": dict(s.attributes or {})}
            for s in exporter.get_finished_spans()
        ],
        default=str,
    )
    log_dump = json.dumps(logs, default=str)
    audit_dump = json.dumps(_collect(test_db), default=str)

    assert exporter.get_finished_spans(), "esperava spans de nos/agentes/ferramentas"
    for name, dump in (
        ("metrics", metrics_text),
        ("spans", span_dump),
        ("logs", log_dump),
        ("audit", audit_dump),
    ):
        assert _SECRET not in dump, name
        assert _CPF not in dump, name
        assert "SECRET123" not in dump, name


def _collect(db) -> list[dict]:
    import asyncio

    async def _run() -> list[dict]:
        return await db["audit_events"].find({}).to_list(length=10_000)

    return asyncio.run(_run())


def test_audit_events_only_carry_allowlisted_metadata_keys(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    from app.repository.audit_events_repository import SAFE_METADATA_ALLOWLIST

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CLARIFICATION_REQUIRED,
        confidence=0.4,
        requires_clarification=True,
        reason_code="AMBIGUOUS",
    )
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": f"texto {_SECRET}", "user_id": "client_xpto"},
    )

    events = _collect(test_db)
    assert events
    for event in events:
        assert set(event["safe_metadata"]) <= SAFE_METADATA_ALLOWLIST
        assert _SECRET not in json.dumps(event, default=str)
