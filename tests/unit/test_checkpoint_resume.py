"""T102: teste unitario da logica de retomada idempotente a partir do checkpoint (spec FR-037)
-- nenhuma etapa concluida e repetida."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.agent.router.agent as router_agent_module
import app.agent.runtime as graph_runtime
from app.agent.checkpoints.resume import (
    ExecutionNotFoundError,
    OriginalMessageNotFoundError,
    resume_execution,
)
from app.agent.graph import AGENT_HANDLERS
from app.repository.executions_repository import (
    Execution,
    ExecutionsRepository,
    ExecutionStatus,
)
from app.repository.messages_repository import (
    Message,
    MessageSender,
    MessagesRepository,
)
from app.repository.sessions_repository import Session, SessionsRepository
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


@pytest.fixture(autouse=True)
def _reset_registry():
    AGENT_HANDLERS.clear()
    graph_runtime.reset_compiled_graph()
    yield
    AGENT_HANDLERS.clear()
    graph_runtime.reset_compiled_graph()


async def test_resume_of_completed_execution_returns_stored_response_without_recompute(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("nao deveria reexecutar uma execucao ja concluida (FR-037)")

    monkeypatch.setattr(router_agent_module, "get_structured_output", fail_if_called)

    stored_response = {
        "status": "OK",
        "agent": "knowledge_agent",
        "message": "resposta ja concluida",
        "sources": [],
        "metadata": {"execution_id": "exec_1", "confidence": 0.9},
    }
    await ExecutionsRepository(db).insert(
        Execution(
            execution_id="exec_1",
            session_id="sess_1",
            message_id="msg_1",
            status=ExecutionStatus.COMPLETED,
            final_response=stored_response,
        )
    )

    response = await resume_execution(db, "exec_1")

    assert response.status == Status.OK
    assert response.message == "resposta ja concluida"


async def test_resume_of_running_execution_reexecutes_from_original_message(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await SessionsRepository(db).insert(Session(session_id="sess_2", user_id="client_xpto"))
    await MessagesRepository(db).insert(
        Message(
            message_id="msg_2",
            session_id="sess_2",
            sender=MessageSender.USER,
            payload={"message": "pergunta original", "user_id": "client_xpto"},
        )
    )
    await ExecutionsRepository(db).insert(
        Execution(
            execution_id="exec_2",
            session_id="sess_2",
            message_id="msg_2",
            status=ExecutionStatus.RUNNING,
        )
    )

    async def fake_get_structured_output(_schema, _messages) -> RouterDecision:
        return RouterDecision(
            intent=Intent.UNKNOWN,
            target_agent=None,
            confidence=0.9,
            requires_clarification=False,
            reason_code="OUT_OF_SCOPE",
        )

    monkeypatch.setattr(router_agent_module, "get_structured_output", fake_get_structured_output)

    response = await resume_execution(db, "exec_2")

    assert response.status == Status.CLARIFICATION_REQUIRED

    updated = await ExecutionsRepository(db).get("exec_2")
    assert updated.status == ExecutionStatus.COMPLETED


async def test_resume_of_nonexistent_execution_raises() -> None:
    db = AsyncMongoMockClient()["getnet_test"]
    with pytest.raises(ExecutionNotFoundError):
        await resume_execution(db, "does-not-exist")


async def test_resume_without_original_message_raises(db) -> None:
    await ExecutionsRepository(db).insert(
        Execution(
            execution_id="exec_3",
            session_id="sess_3",
            message_id="msg_missing",
            status=ExecutionStatus.RUNNING,
        )
    )

    with pytest.raises(OriginalMessageNotFoundError):
        await resume_execution(db, "exec_3")
