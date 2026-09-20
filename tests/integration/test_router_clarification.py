"""T093: teste de integracao para CLARIFICATION_REQUIRED em mensagem ambigua (US3, Acceptance
Scenario 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_ambiguous_message_returns_clarification_required(
    client: TestClient, fake_llm: dict
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CLARIFICATION_REQUIRED,
        target_agent=None,
        confidence=0.3,
        requires_clarification=True,
        reason_code="AMBIGUOUS_MESSAGE",
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "isso", "user_id": "client_xpto"},
    )

    body = response.json()
    assert body["status"] == Status.CLARIFICATION_REQUIRED.value
    assert body["agent"] == "router_agent"
    assert body["message"]
