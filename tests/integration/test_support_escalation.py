"""T082: teste de integracao para escalacao com ticket_id quando o Playbook se esgota (US2,
Acceptance Scenario 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision
from tests.playbook_fixtures import build_device_connection_playbook


async def test_escalation_produces_traceable_ticket_id(
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

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_without_devices"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Minha maquininha nao conecta", "user_id": "client_without_devices"},
    )

    body = response.json()
    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert body["metadata"]["ticket_id"] is not None
