"""T080: teste de integracao de contrato para POST /api/v1/sessions/{id}/messages com uma
mensagem de problema de dispositivo."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.customer_support.agent import SupportAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_support_message_response_matches_agent_response_contract(
    client: TestClient, fake_llm: dict
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="DEVICE_CONNECTION_PROBLEM",
    )
    fake_llm[SupportAnswerDraft] = SupportAnswerDraft(
        message="Tente aproximar a maquininha do roteador."
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Minha maquininha nao conecta", "user_id": "client_xpto"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(["status", "agent", "message", "sources", "metadata"]).issubset(body.keys())
    assert body["status"] in {s.value for s in Status}
    assert body["agent"] == "customer_support_agent"
