"""T107: teste de integracao para um novo Playbook cadastrado sendo seguido pelo Customer
Support Agent sem alteracao de codigo (US5, Acceptance Scenario 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


async def test_newly_registered_playbook_is_picked_up_without_code_change(
    client: TestClient, fake_llm: dict
) -> None:
    playbook_response = client.post(
        "/api/v1/playbooks",
        json={
            "name": "Cartao recusado repetidamente",
            "objective": "Investigar recusas repetidas de cartao",
            "symptoms": ["cartao recusado", "transacao recusada"],
            "prerequisites": [],
            "steps": [
                {"step_id": "s1", "instruction": "consultar status da transacao", "tool": None}
            ],
            "decision_points": [],
            "authorized_tools": ["get_transaction_status", "create_support_ticket"],
            "exceptions": [],
            "escalation_rules": ["Escalar se nao houver causa clara"],
            "success_criteria": [],
            "closure_criteria": [],
        },
    )
    assert playbook_response.status_code == 200

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="TRANSACTION_DECLINED",
    )

    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "meu cartao recusado varias vezes hoje", "user_id": "client_xpto"},
    )

    body = response.json()
    # O novo Playbook foi encontrado (nao caiu no fallback "sem Playbook aplicavel") -- ainda
    # assim escala (sem dispositivo offline mock associado a este cenario), mas com ticket_id
    # rastreavel, confirmando que create_support_ticket (autorizado pelo novo Playbook) rodou.
    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert body["metadata"]["ticket_id"] is not None
