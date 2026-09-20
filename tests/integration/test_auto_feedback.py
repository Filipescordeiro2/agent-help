"""Feedback automatico: nota de grounding entre 0 e 3 vira Feedback para o Feedback Agent."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.config.settings import get_settings
from app.schemas.agent_response import Status
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision


def _evaluation(score: int, **overrides) -> GroundingEvaluation:
    data = {
        "score": score,
        "reasoning": "Resposta nao cobre o codigo de erro pedido.",
        "adheres_to_question": True,
        "uses_context_correctly": True,
        "no_unsupported_claims": True,
        "is_complete": True,
    }
    return GroundingEvaluation(**{**data, **overrides})


@pytest.fixture
def knowledge_flow(client: TestClient, fake_llm: dict) -> TestClient:
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": "A taxa da maquininha e 1,99%."},
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="TAXA",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa da maquininha e 1,99%.", grounded_in_sources=True
    )
    return client


def _ask(client: TestClient) -> dict:
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]
    return client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "Qual a taxa da maquininha? meu telefone 11 99999-8888", "user_id": "u1"},
    ).json()


def _auto(client: TestClient) -> list[dict]:
    return [f for f in client.get("/api/v1/feedback").json() if f["source"] == "auto_grounding"]


@pytest.mark.parametrize("score", [0, 1, 2, 3])
def test_low_score_creates_an_automatic_feedback(
    knowledge_flow: TestClient, fake_llm: dict, score: int
) -> None:
    fake_llm[GroundingEvaluation] = _evaluation(score)

    _ask(knowledge_flow)

    (feedback,) = _auto(knowledge_flow)  # um so por execucao, mesmo com a retentativa
    assert feedback["rating"] == float(score)
    assert feedback["agent_invoked"] == "knowledge_agent"
    assert f"[AUTO grounding {score}/5]" in feedback["comment"]
    assert "Resposta nao cobre o codigo de erro pedido." in feedback["comment"]
    assert "99999-8888" not in feedback["comment"]  # pergunta mascarada
    assert feedback["problem_classification"] == "INCOMPLETE_RESPONSE"


def test_score_three_still_delivers_the_answer(knowledge_flow: TestClient, fake_llm: dict) -> None:
    fake_llm[GroundingEvaluation] = _evaluation(3)

    body = _ask(knowledge_flow)

    assert body["status"] == Status.OK.value and body["metadata"]["grounding_score"] == 3
    assert len(_auto(knowledge_flow)) == 1


def test_good_score_creates_no_feedback(knowledge_flow: TestClient, fake_llm: dict) -> None:
    fake_llm[GroundingEvaluation] = _evaluation(4)

    _ask(knowledge_flow)

    assert _auto(knowledge_flow) == []


def test_classification_follows_the_evaluator_criteria(
    knowledge_flow: TestClient, fake_llm: dict
) -> None:
    fake_llm[GroundingEvaluation] = _evaluation(1, uses_context_correctly=False)
    _ask(knowledge_flow)
    assert _auto(knowledge_flow)[0]["problem_classification"] == "RETRIEVAL_FAILURE"


def test_feature_can_be_turned_off(
    knowledge_flow: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auto_feedback_enabled", False)
    fake_llm[GroundingEvaluation] = _evaluation(1)

    _ask(knowledge_flow)

    assert _auto(knowledge_flow) == []


def test_auto_feedback_appears_in_the_audit_explanation(
    knowledge_flow: TestClient, fake_llm: dict
) -> None:
    fake_llm[GroundingEvaluation] = _evaluation(2)
    sid = knowledge_flow.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]
    knowledge_flow.post(
        f"/api/v1/sessions/{sid}/messages", json={"message": "Qual a taxa?", "user_id": "u1"}
    )

    audit = knowledge_flow.get(f"/api/v1/audit/sessions/{sid}").json()

    assert any("feedback automatico" in line for line in audit["turns"][0]["explanation"])
