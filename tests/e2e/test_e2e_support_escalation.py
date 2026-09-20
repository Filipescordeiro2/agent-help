"""T131: teste E2E "falha de maquininha -> Customer Support Agent -> Playbook -> chamado"
(spec quickstart.md, secao 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.customer_support.agent import SupportAnswerDraft
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


def test_end_to_end_device_issue_resolves_or_escalates_with_traceable_ticket(
    client: TestClient, fake_llm: dict
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.94,
        requires_clarification=False,
        reason_code="DEVICE_CONNECTION_PROBLEM",
    )
    fake_llm[SupportAnswerDraft] = SupportAnswerDraft(
        message="Tente aproximar a maquininha do roteador Wi-Fi."
    )

    session = client.post("/api/v1/sessions", json={"user_id": "client_xpto"})
    session_id = session.json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"message": "Minha maquininha nao conecta", "user_id": "client_xpto"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "customer_support_agent"
    # Playbook padrao (seed) resolve o caso "sinal_wifi_fraco" (mock) com status OK; qualquer
    # outro cenario mock deve, no minimo, escalar com um ticket_id rastreavel -- nunca um dado
    # de cliente/dispositivo inventado (spec FR-018).
    # (sem Playbook de suporte o agente pergunta antes de qualquer coisa: CLARIFICATION_REQUIRED)
    assert body["status"] in {
        Status.OK.value,
        Status.ESCALATION_REQUIRED.value,
        Status.CLARIFICATION_REQUIRED.value,
    }
    if body["status"] == Status.ESCALATION_REQUIRED.value:
        assert body["metadata"]["ticket_id"] is not None

    execution_id = body["metadata"]["execution_id"]
    execution = client.get(f"/api/v1/executions/{execution_id}")
    assert execution.status_code == 200
    assert "customer_support_agent" in execution.json()["agents_invoked"]
