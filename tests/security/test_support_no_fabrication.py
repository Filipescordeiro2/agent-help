"""T083: teste de seguranca garantindo que o Customer Support Agent nunca retorna dado de
cliente/dispositivo fora de fonte real ou mock identificada -- spec FR-018."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.agent.customer_support.agent as customer_support_module
from app.agent.customer_support.agent import CustomerSupportAgent, SupportAnswerDraft
from app.agent.state import new_state
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.agent_response import Status
from app.schemas.user_message import UserMessageInput
from app.tools.mocks.customer_data import _MOCK_DEVICES, _MOCK_TICKETS
from tests.playbook_fixtures import build_device_connection_playbook


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


@pytest.fixture(autouse=True)
async def _seed(db) -> None:
    await PlaybooksRepository(db).insert(build_device_connection_playbook())


async def test_escalation_ticket_id_is_always_traceable_to_the_mock_store(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Todo ticket_id retornado ao usuario deve existir no armazenamento mock -- nunca um
    identificador inventado pelo LLM ou pelo agente."""
    agent = CustomerSupportAgent(db)
    state = new_state(
        session_id="s1",
        execution_id="e1",
        request_id="r1",
        user_message=UserMessageInput(
            message="minha maquininha nao conecta", user_id="client_without_devices"
        ),
    )

    response = await agent.handle(state)

    assert response.status == Status.ESCALATION_REQUIRED
    ticket_id = response.metadata.ticket_id
    assert ticket_id is not None
    all_ticket_ids = {t["ticket_id"] for tickets in _MOCK_TICKETS.values() for t in tickets}
    assert ticket_id in all_ticket_ids


async def test_device_referenced_in_resolution_exists_in_mock_store(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O device_id usado para resolver o caso deve vir do armazenamento mock -- nunca inventado."""
    captured_context: list[str] = []

    async def fake_get_structured_output(_schema, messages) -> SupportAnswerDraft:
        captured_context.append(messages[0]["content"])
        return SupportAnswerDraft(message="Tente reiniciar o dispositivo.")

    monkeypatch.setattr(
        customer_support_module, "get_structured_output", fake_get_structured_output
    )

    agent = CustomerSupportAgent(db)
    state = new_state(
        session_id="s1",
        execution_id="e1",
        request_id="r1",
        user_message=UserMessageInput(
            message="minha maquininha nao conecta", user_id="client_xpto"
        ),
    )

    response = await agent.handle(state)

    assert response.status == Status.OK
    known_device_ids = {d["device_id"] for devices in _MOCK_DEVICES.values() for d in devices}
    assert any(device_id in captured_context[0] for device_id in known_device_ids)


async def test_unknown_user_never_gets_fabricated_device_data(db) -> None:
    agent = CustomerSupportAgent(db)
    state = new_state(
        session_id="s1",
        execution_id="e1",
        request_id="r1",
        user_message=UserMessageInput(
            message="minha maquininha nao conecta", user_id="user_that_does_not_exist"
        ),
    )

    response = await agent.handle(state)

    # Sem dispositivo mock para esse usuario -> deve escalar, nunca inventar um device_id.
    assert response.status == Status.ESCALATION_REQUIRED
