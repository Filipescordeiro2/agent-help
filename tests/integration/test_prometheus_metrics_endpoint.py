"""T085: `GET /metrics` no formato de exposicao Prometheus (US6)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import app.agent.nodes.grounding_node as grounding_module
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.config.settings import get_settings
from app.llm.fake_defaults import _classification_default
from app.llm.openrouter_client import clear_chat_model_cache
from app.llm.structured_output import get_structured_output
from app.schemas.router import Intent, RouterDecision
from tests.feedback_helpers import classifier_handler, patch_agent_llm


@pytest.fixture
def scraped(client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    """Gera trafego (mensagem com grounding + proposta do Feedback Agent) e devolve o /metrics."""
    monkeypatch.setattr(get_settings(), "llm_provider", "fake")
    clear_chat_model_cache()
    monkeypatch.setattr(grounding_module, "get_structured_output", get_structured_output)
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
    client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )

    patch_agent_llm(
        monkeypatch, classifier_handler(lambda c: _classification_default(f"comentario: {c}"))
    )
    client.post(
        "/api/v1/feedback",
        json={
            "user_id": "client_xpto",
            "session_id": session_id,
            "message_id": "m1",
            "execution_id": "e1",
            "problem_classification": "KNOWLEDGE_GAP",
            "comment": "Nao existe passo a passo para maquininha que nao liga de jeito nenhum",
        },
    )
    client.post("/api/v1/feedback-agent/run")

    response = client.get("/metrics")
    assert response.status_code == 200
    scraped_text = response.text
    clear_chat_model_cache()
    return scraped_text


def test_metrics_endpoint_serves_the_prometheus_text_format(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# HELP " in response.text and "# TYPE " in response.text


def test_contract_series_are_exposed_after_traffic(scraped: str) -> None:
    for series in (
        "getnet_http_requests_total",
        "getnet_http_request_duration_seconds_bucket",
        "getnet_agent_call_duration_seconds_bucket",
        "getnet_llm_tokens_total",
        "getnet_grounding_score_bucket",
        "getnet_feedback_proposals_total",
    ):
        assert series in scraped, series


def test_agent_duration_histogram_carries_agent_and_status_labels(scraped: str) -> None:
    lines = [
        line
        for line in scraped.splitlines()
        if line.startswith("getnet_agent_call_duration_seconds_bucket")
    ]
    assert any('agent="knowledge_agent"' in line and 'status="ok"' in line for line in lines)


def test_http_series_use_the_route_template_not_the_raw_path(scraped: str) -> None:
    endpoints = set(re.findall(r'getnet_http_requests_total\{[^}]*endpoint="([^"]+)"', scraped))
    assert "/api/v1/sessions/{session_id}/messages" in endpoints
    assert not any(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", e) for e in endpoints)  # sem UUIDs


def test_no_high_cardinality_labels_anywhere(scraped: str) -> None:
    for label in ("user_id=", "session_id=", "message_id=", "execution_id=", "request_id="):
        assert label not in scraped, label


def test_grounding_score_buckets_are_the_explicit_zero_to_five(scraped: str) -> None:
    bounds = set(re.findall(r'getnet_grounding_score_bucket\{[^}]*le="([^"]+)"', scraped))
    assert {float(b) for b in bounds} >= {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, float("inf")}
