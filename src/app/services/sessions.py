"""Servico de sessoes -- logica de negocio das rotas /api/v1/sessions (rotas ficam finas)."""

from __future__ import annotations

import uuid

from app.agent.runtime import get_compiled_graph
from app.agent.state import new_state
from app.observability.logging import bind_context
from app.observability.trace import emit_trace, summarize_response
from app.repository.executions_repository import Execution, ExecutionsRepository
from app.repository.messages_repository import (
    Message,
    MessageSender,
    MessagesRepository,
)
from app.repository.mongodb.client import get_database
from app.repository.sessions_repository import Session, SessionsRepository
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.user_message import UserMessageInput
from app.services.session_concurrency import acquire_session_lock


class SessionNotFoundError(Exception):
    pass


class SessionClosedError(Exception):
    pass


async def create_session(user_id: str, channel: str | None = None) -> Session:
    repo = SessionsRepository(get_database())
    session = Session(session_id=str(uuid.uuid4()), user_id=user_id, channel=channel)
    return await repo.insert(session)


async def get_session(session_id: str) -> Session:
    repo = SessionsRepository(get_database())
    session = await repo.get(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)
    return session


async def close_session(session_id: str) -> Session:
    session = await get_session(session_id)
    repo = SessionsRepository(get_database())
    closed = await repo.close(session.session_id)
    assert closed is not None
    return closed


async def process_message(session_id: str, user_message: UserMessageInput) -> AgentResponse:
    session = await get_session(session_id)
    if session.status.value == "closed":
        raise SessionClosedError(session_id)

    # FR-038: serializa o processamento de mensagens da MESMA sessao -- duas mensagens
    # concorrentes na mesma sessao nunca rodam o grafo em paralelo (evita estado/checkpoint
    # corrompido por escrita concorrente). Sessoes diferentes continuam paralelas entre si.
    async with acquire_session_lock(session_id):
        return await _process_message_locked(session, user_message)


async def _process_message_locked(
    session: Session, user_message: UserMessageInput
) -> AgentResponse:
    execution_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    state = new_state(
        session_id=session.session_id,
        execution_id=execution_id,
        request_id=request_id,
        message_id=message_id,
        user_message=user_message,
    )

    # Registrados ANTES de invocar o grafo -- se o processo cair no meio do fluxo, estes
    # registros sao o que permite `resume_execution()` (US4, FR-037) retomar depois em vez de
    # perder a solicitacao silenciosamente (o grafo em si nao tem checkpoint intermediario
    # granular; a mensagem original persistida e o que possibilita reexecutar).
    db = get_database()
    await ExecutionsRepository(db).insert(
        Execution(execution_id=execution_id, session_id=session.session_id, message_id=message_id)
    )
    await MessagesRepository(db).insert(
        Message(
            message_id=message_id,
            session_id=session.session_id,
            sender=MessageSender.USER,
            payload=user_message.model_dump(mode="json"),
        )
    )

    # Trilha de auditoria: a entrada do usuario (mascarada) abre o turno.
    bind_context(
        request_id=request_id,
        session_id=session.session_id,
        message_id=message_id,
        execution_id=execution_id,
    )
    await emit_trace(
        "user_input",
        actor="system",
        actor_name="user",
        details={
            "message": user_message.message,
            "user_id": user_message.user_id,
            "channel": session.channel,
            "characters": len(user_message.message),
        },
    )

    graph = get_compiled_graph()
    result_state = await graph.ainvoke(state)

    final_response = result_state.get("final_response")
    await emit_trace(
        "final_response",
        actor="system",
        actor_name="orchestrator",
        status=final_response.status.value if final_response else "NO_RESPONSE",
        details=summarize_response(final_response),
    )
    if final_response is None:
        return AgentResponse(
            status=Status.ERROR,
            agent="orchestrator",
            message="Nao foi possivel processar sua solicitacao no momento.",
            metadata=ResponseMetadata(
                execution_id=execution_id, confidence=0.0, error_code="NO_RESPONSE_PRODUCED"
            ),
        )
    return final_response
