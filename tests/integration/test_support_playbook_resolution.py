"""T081: teste de integracao para resolucao guiada por Playbook usando dados mock (US2,
Acceptance Scenario 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.customer_support.agent import SupportAnswerDraft
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision
from tests.playbook_fixtures import build_device_connection_playbook


async def test_known_device_issue_is_resolved_via_playbook(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    await PlaybooksRepository(test_db).insert(build_device_connection_playbook())

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="DEVICE_CONNECTION_PROBLEM",
    )
    fake_llm[SupportAnswerDraft] = SupportAnswerDraft(
        message="Tente aproximar a maquininha do roteador Wi-Fi."
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Minha maquininha nao conecta", "user_id": "client_xpto"},
    )

    body = response.json()
    assert body["status"] == Status.OK.value
    assert "roteador" in body["message"].lower()
