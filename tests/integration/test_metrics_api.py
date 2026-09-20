"""T080: endpoints JSON de metricas (US5) alimentados por trafego real em modo fake."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.agent.nodes.grounding_node as grounding_module
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.config.settings import get_settings
from app.llm.openrouter_client import clear_chat_model_cache
from app.llm.structured_output import get_structured_output
from app.schemas.router import Intent, RouterDecision
from tests.conftest import _build_test_app

_ENDPOINTS = (
    "tpm",
    "rpm",
    "latency",
    "sessions-per-user",
    "tokens-per-user-session",
    "grounding-scores",
)


@pytest.fixture
def traffic(client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Uma mensagem real pelo grafo; o grounding usa o LLM fake de verdade (gera uso de tokens)."""
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
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Qual a taxa da maquininha?", "user_id": "client_xpto"},
    )
    assert response.json()["status"] == "OK"
    client.session_id = session_id  # type: ignore[attr-defined]
    yield client
    clear_chat_model_cache()


def test_every_endpoint_requires_the_internal_token(
    test_db, internal_token: str, fake_embeddings
) -> None:
    anonymous = TestClient(_build_test_app())
    for endpoint in _ENDPOINTS:
        assert anonymous.get(f"/api/v1/metrics/{endpoint}").status_code == 403


def test_window_minutes_is_validated(client: TestClient) -> None:
    assert client.get("/api/v1/metrics/tpm", params={"window_minutes": 0}).status_code == 422
    assert client.get("/api/v1/metrics/tpm", params={"window_minutes": 15}).status_code == 200


def test_tpm_reflects_the_traffic_overall_and_by_user(traffic: TestClient) -> None:
    body = traffic.get("/api/v1/metrics/tpm", params={"window_minutes": 15}).json()

    assert body["window_minutes"] == 15 and body["overall_tpm"] > 0
    assert body["by_user"]["client_xpto"] == pytest.approx(body["overall_tpm"])

    filtered = traffic.get("/api/v1/metrics/tpm", params={"user_id": "client_xpto"}).json()
    assert "by_user" not in filtered and filtered["overall_tpm"] > 0


def test_rpm_counts_the_execution_per_user(traffic: TestClient) -> None:
    body = traffic.get("/api/v1/metrics/rpm", params={"window_minutes": 10}).json()
    assert body["overall_rpm"] == pytest.approx(0.1)
    assert body["by_user"] == {"client_xpto": pytest.approx(0.1)}


def test_latency_reports_agents_endpoints_and_error_rates(traffic: TestClient) -> None:
    body = traffic.get("/api/v1/metrics/latency").json()

    assert set(body) >= {"p50_ms", "p95_ms", "p99_ms", "by_agent", "by_endpoint"}
    assert "knowledge_agent" in body["by_agent"]
    assert body["by_agent"]["knowledge_agent"]["p95_ms"] >= 0
    assert "/api/v1/sessions/{session_id}/messages" in body["by_endpoint"]  # template, nao path
    assert body["error_rate_by_agent"]["knowledge_agent"] == 0.0
    assert isinstance(body["error_rate_by_tool"], dict)

    only_agent = traffic.get("/api/v1/metrics/latency", params={"agent": "knowledge_agent"}).json()
    assert "by_agent" not in only_agent and only_agent["by_endpoint"]


def test_sessions_per_user_lists_the_active_session(traffic: TestClient) -> None:
    rows = traffic.get("/api/v1/metrics/sessions-per-user").json()
    assert rows == [{"user_id": "client_xpto", "active_session_count": 1}]


def test_tokens_per_user_session_links_tokens_to_user_and_session(traffic: TestClient) -> None:
    rows = traffic.get("/api/v1/metrics/tokens-per-user-session").json()

    assert len(rows) == 1
    assert rows[0]["user_id"] == "client_xpto" and rows[0]["session_id"] == traffic.session_id
    assert rows[0]["total_tokens"] > 0 and rows[0]["window_minutes"] == 60
    assert (
        traffic.get("/api/v1/metrics/tokens-per-user-session", params={"user_id": "outro"}).json()
        == []
    )


def test_grounding_scores_report_the_evaluation_of_the_traffic(traffic: TestClient) -> None:
    body = traffic.get("/api/v1/metrics/grounding-scores").json()

    assert body["total_evaluated"] >= 1 and body["below_threshold_count"] == 0
    assert list(body["distribution"]) == ["0", "1", "2", "3", "4", "5"]
    assert body["distribution"]["5"] >= 1  # o avaliador fake atribui score 5
