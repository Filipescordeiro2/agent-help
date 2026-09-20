"""T037: grounding em sequencias multiagente (spec FR-020, US1 AC7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.customer_support.agent import SupportAnswerDraft
from app.agent.customer_support.case_flow import SupportCaseFlow
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision
from app.tools.mocks import customer_data
from tests.playbook_fixtures import build_device_connection_playbook


def evaluation(score: int, reasoning: str = "r") -> GroundingEvaluation:
    return GroundingEvaluation(
        score=score,
        reasoning=reasoning,
        adheres_to_question=True,
        uses_context_correctly=True,
        no_unsupported_claims=True,
        is_complete=True,
    )


def _multi_router() -> RouterDecision:
    return RouterDecision(
        intent=Intent.MULTI_AGENT,
        target_sequence=["knowledge_agent", "customer_support_agent"],
        confidence=0.9,
        requires_clarification=False,
        reason_code="MIXED_DOMAIN_REQUEST",
    )


def _ingest(client: TestClient) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha e 1,99% no credito.",
        },
    )


async def _support_escalates(self, state, case) -> AgentResponse:
    """Suporte guiado que encaminha a um atendente (o que estes testes precisam da parte de suporte)."""
    return AgentResponse(
        status=Status.ESCALATION_REQUIRED,
        agent="customer_support_agent",
        message="Nao consegui resolver isso automaticamente. Vou encaminhar seu caso.",
        metadata=ResponseMetadata(
            execution_id=state["execution_id"], confidence=0.5, ticket_id="TKT-000001"
        ),
    )


@pytest.fixture(autouse=True)
def _support_part_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estes testes exercitam o grounding da sequencia multiagente com a parte de suporte
    escalando; o suporte guiado tem os proprios testes (test_support_guided_flow)."""
    monkeypatch.setattr(SupportCaseFlow, "run", _support_escalates)


def _ask(client: TestClient, message: str) -> dict:
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    return client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": message, "user_id": "client_xpto"},
    ).json()


# Sem Playbook cadastrado o Customer Support Agent escala (ESCALATION_REQUIRED).
_MIXED_MESSAGE = "Qual a taxa da maquininha e preciso de ajuda com outra coisa"


def test_knowledge_ok_plus_support_escalation_evaluates_only_the_knowledge_part(
    client: TestClient, fake_llm: dict
) -> None:
    _ingest(client)
    fake_llm[RouterDecision] = _multi_router()
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa da maquininha e 1,99% no credito.", grounded_in_sources=True
    )
    calls: list[int] = []

    def evaluate() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(4, "boa")

    fake_llm[GroundingEvaluation] = evaluate

    body = _ask(client, _MIXED_MESSAGE)

    assert body["agent"] == "multi_agent"
    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert calls == [1]
    assert body["metadata"]["grounding_score"] == 4
    assert "1,99%" in body["message"]


def test_rejected_knowledge_part_is_never_delivered_and_retry_runs_once_without_duplicate_tickets(
    client: TestClient, fake_llm: dict
) -> None:
    _ingest(client)
    fake_llm[RouterDecision] = _multi_router()
    draft_calls: list[int] = []

    def draft() -> KnowledgeAnswerDraft:
        draft_calls.append(1)
        return KnowledgeAnswerDraft(message="taxa inventada de 99%", grounded_in_sources=False)

    fake_llm[KnowledgeAnswerDraft] = draft
    fake_llm[GroundingEvaluation] = evaluation(1, "sem suporte")
    tickets_before = len(customer_data._MOCK_TICKETS["client_xpto"])

    body = _ask(client, _MIXED_MESSAGE)

    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert "inventada" not in body["message"]
    assert len(draft_calls) == 2  # uma tentativa + exatamente um retry
    assert body["metadata"]["grounding_score"] == 1
    # create_ticket e idempotente por (user_id, subject): o retry nao abre um segundo chamado.
    assert len(customer_data._MOCK_TICKETS["client_xpto"]) - tickets_before <= 1


async def test_both_parts_ok_are_evaluated_and_composite_score_is_the_minimum(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    await PlaybooksRepository(test_db).insert(build_device_connection_playbook())
    _ingest(client)
    fake_llm[RouterDecision] = _multi_router()
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa da maquininha e 1,99% no credito.", grounded_in_sources=True
    )
    fake_llm[SupportAnswerDraft] = SupportAnswerDraft(
        message="Tente aproximar a maquininha do roteador Wi-Fi."
    )
    scores = iter([5, 3])
    fake_llm[GroundingEvaluation] = lambda: evaluation(next(scores))

    body = _ask(client, "Qual a taxa da maquininha e por que ela nao conecta?")

    assert body["status"] == Status.OK.value
    assert body["metadata"]["grounding_score"] == 3


def test_both_parts_procedural_makes_no_llm_call_and_score_is_null(
    client: TestClient, fake_llm: dict
) -> None:
    calls: list[int] = []

    def evaluate() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(5)

    fake_llm[RouterDecision] = _multi_router()
    fake_llm[GroundingEvaluation] = evaluate

    body = _ask(client, "Cashback em Marte e outra coisa qualquer")  # base vazia, sem Playbook

    assert body["status"] in (
        Status.ESCALATION_REQUIRED.value,
        Status.INSUFFICIENT_CONTEXT.value,
    )
    assert calls == []
    assert body["metadata"]["grounding_score"] is None
