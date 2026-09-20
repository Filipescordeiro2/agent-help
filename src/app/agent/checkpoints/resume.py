"""Logica de retomada a partir do checkpoint -- reentrada idempotente, sem repetir etapas
concluidas (spec FR-037).

Uma execucao e persistida como "running" (com a mensagem original do usuario ja gravada)
ANTES do grafo comecar a rodar (ver app/services/sessions.py::process_message). Isso permite
duas situacoes distintas ao retomar:

1. A execucao ja chegou a `status == completed` (persist_node ja rodou) -- retomar e
   puramente idempotente: retorna a resposta ja persistida, sem reprocessar nada.
2. A execucao ainda esta "running"/"interrompida" (o processo caiu antes do persist_node) --
   retomar reexecuta o fluxo completo para a mesma mensagem original (reaproveitando o mesmo
   execution_id/message_id, para que persist_node atualize em vez de duplicar o registro).
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.runtime import get_compiled_graph
from app.agent.state import new_state
from app.repository.executions_repository import ExecutionsRepository, ExecutionStatus
from app.repository.messages_repository import MessageSender, MessagesRepository
from app.repository.sessions_repository import SessionsRepository
from app.schemas.agent_response import AgentResponse
from app.schemas.user_message import UserMessageInput


class ExecutionNotFoundError(Exception):
    pass


class OriginalMessageNotFoundError(Exception):
    """A execucao esta pendente, mas a mensagem original nao foi encontrada para reexecutar."""


async def resume_execution(db: AsyncIOMotorDatabase, execution_id: str) -> AgentResponse:
    executions_repo = ExecutionsRepository(db)
    execution = await executions_repo.get(execution_id)
    if execution is None:
        raise ExecutionNotFoundError(execution_id)

    if execution.status == ExecutionStatus.COMPLETED and execution.final_response:
        # Idempotente -- nenhuma etapa ja concluida e repetida.
        return AgentResponse.model_validate(execution.final_response)

    user_message = await _load_original_user_message(db, execution.message_id, execution.session_id)

    state = new_state(
        session_id=execution.session_id,
        execution_id=execution.execution_id,
        request_id=execution.execution_id,
        message_id=execution.message_id,
        user_message=user_message,
    )

    graph = get_compiled_graph(db)
    result_state = await graph.ainvoke(state)

    final_response = result_state.get("final_response")
    if final_response is None:
        raise OriginalMessageNotFoundError(
            f"execucao {execution_id} nao produziu resposta ao ser retomada"
        )
    return final_response


async def _load_original_user_message(
    db: AsyncIOMotorDatabase, message_id: str, session_id: str
) -> UserMessageInput:
    messages_repo = MessagesRepository(db)
    message = await messages_repo.get(message_id)
    if message is not None and message.sender == MessageSender.USER:
        return UserMessageInput.model_validate(message.payload)

    # Fallback defensivo: nenhuma mensagem de usuario com esse message_id foi encontrada --
    # nao ha como reexecutar com seguranca sem inventar o conteudo original (FR-018-like
    # garantia de nao fabricar dado).
    session_repo = SessionsRepository(db)
    if await session_repo.get(session_id) is None:
        raise OriginalMessageNotFoundError(f"sessao {session_id} nao encontrada")
    raise OriginalMessageNotFoundError(
        f"mensagem original {message_id} nao encontrada -- nao e possivel retomar com seguranca"
    )
