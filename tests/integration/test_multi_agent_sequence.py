"""T094: teste de integracao para sequencia MULTI_AGENT cobrindo Knowledge e Customer Support
(US3, Acceptance Scenario 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.customer_support.agent import SupportAnswerDraft
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision
from tests.playbook_fixtures import build_device_connection_playbook


async def test_multi_domain_message_triggers_both_agents_and_combines_response(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    await PlaybooksRepository(test_db).insert(build_device_connection_playbook())

    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Taxas",
            "source": "manual",
            "content": "A taxa da maquininha e 1,99% no credito.",
        },
    )

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.MULTI_AGENT,
        target_agent=None,
        target_sequence=["knowledge_agent", "customer_support_agent"],
        confidence=0.9,
        requires_clarification=False,
        reason_code="MIXED_DOMAIN_REQUEST",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa da maquininha e 1,99% no credito.", grounded_in_sources=True
    )
    fake_llm[SupportAnswerDraft] = SupportAnswerDraft(
        message="Tente aproximar a maquininha do roteador Wi-Fi."
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "message": "Qual a taxa da maquininha e por que ela nao conecta?",
            "user_id": "client_xpto",
        },
    )

    body = response.json()
    assert body["status"] == Status.OK.value
    assert body["agent"] == "multi_agent"
    assert "knowledge_agent" in body["message"]
    assert "customer_support_agent" in body["message"]
    assert "1,99%" in body["message"]
    assert "roteador" in body["message"].lower()
