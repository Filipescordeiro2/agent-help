"""T083: teste unitario do Customer Support Agent -- so executa PlaybookStep autorizado, nunca
fabrica dado, escala ao esgotar passos."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.agent.customer_support.agent as customer_support_module
from app.agent.customer_support.agent import CustomerSupportAgent, SupportAnswerDraft
from app.agent.state import new_state
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import Status
from app.schemas.user_message import UserMessageInput
from tests.playbook_fixtures import build_device_connection_playbook


def _state(message: str, user_id: str = "client_xpto"):
    return new_state(
        session_id="s1",
        execution_id="e1",
        request_id="r1",
        user_message=UserMessageInput(message=message, user_id=user_id),
    )


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


@pytest.fixture(autouse=True)
async def _seed_playbook(db) -> None:
    await PlaybooksRepository(db).insert(build_device_connection_playbook())


async def test_asks_instead_of_escalating_when_no_playbook_matches_and_problem_is_vague(db) -> None:
    agent = CustomerSupportAgent(db)
    response = await agent.handle(_state("preciso de ajuda com uma coisa"))
    # sem Playbook e sem entender o problema: pergunta ao cliente (nunca abre chamado vazio)
    assert response.status == Status.CLARIFICATION_REQUIRED
    assert response.metadata.ticket_id is None


async def test_escalates_when_customer_has_no_offline_device(db) -> None:
    agent = CustomerSupportAgent(db)
    response = await agent.handle(
        _state("minha maquininha nao conecta", user_id="client_without_devices")
    )
    assert response.status == Status.ESCALATION_REQUIRED
    assert response.metadata.ticket_id is not None


async def test_resolves_known_wifi_issue_for_known_device(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get_structured_output(_schema, _messages) -> SupportAnswerDraft:
        return SupportAnswerDraft(message="Tente aproximar a maquininha do roteador Wi-Fi.")

    monkeypatch.setattr(
        customer_support_module, "get_structured_output", fake_get_structured_output
    )

    agent = CustomerSupportAgent(db)
    response = await agent.handle(_state("minha maquininha nao conecta"))

    assert response.status == Status.OK
    assert "roteador" in response.message.lower()


async def test_never_calls_create_ticket_tool_outside_playbook_authorization(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-019: so executa um passo cujo tool esteja em authorized_tools do Playbook."""
    repo = PlaybooksRepository(db)
    playbook = build_device_connection_playbook()
    playbook.authorized_tools = [
        "get_device_status"
    ]  # sem get_device_diagnostics nem create_support_ticket
    await repo.update(playbook.playbook_id, {"authorized_tools": playbook.authorized_tools})

    agent = CustomerSupportAgent(db)
    response = await agent.handle(_state("minha maquininha nao conecta"))

    # Como get_device_diagnostics nao esta autorizado, o agente deve escalar sem executa-lo,
    # e sem ticket_id (create_support_ticket tambem nao esta autorizado).
    assert response.status == Status.ESCALATION_REQUIRED
    assert response.metadata.ticket_id is None
