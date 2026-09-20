"""No de persistencia de sessao/memoria -- grava a resposta final, a execucao e o checkpoint
apos cada etapa (spec FR-022).

A mensagem do usuario e o registro inicial da execucao (status "running") sao persistidos
ANTES do grafo rodar, por `app/services/sessions.py::process_message` -- isso e o que permite
`resume_execution()` (US4, FR-037) recuperar a mensagem original mesmo se o processo cair
antes deste no ser alcancado. Este no so precisa atualizar o que ja existe.
"""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.checkpoints.mongo_checkpointer import MongoCheckpointer
from app.agent.state import GraphState
from app.repository.executions_repository import Execution, ExecutionsRepository
from app.repository.messages_repository import (
    Message,
    MessageSender,
    MessagesRepository,
)
from app.repository.sessions_repository import SessionsRepository
from app.services.memory.episodic import capture_episode


async def persist_node(state: GraphState, db: AsyncIOMotorDatabase) -> GraphState:
    state["node_name"] = "persist"

    messages_repo = MessagesRepository(db)
    executions_repo = ExecutionsRepository(db)
    sessions_repo = SessionsRepository(db)
    checkpointer = MongoCheckpointer(db)

    final_response = state.get("final_response")
    message_id = state.get("message_id") or state["execution_id"]

    await messages_repo.insert(
        Message(
            message_id=str(uuid.uuid4()),
            session_id=state["session_id"],
            sender=MessageSender.SYSTEM,
            payload=final_response.model_dump(mode="json") if final_response else {},
        )
    )

    routing_decision = state.get("routing_decision")
    final_response_dict = final_response.model_dump(mode="json") if final_response else None
    agents_invoked = [r.agent for r in state.get("agent_responses", [])]

    existing_execution = await executions_repo.get(state["execution_id"])
    if existing_execution is None:
        # Caminho normal: a execucao ainda nao foi registrada (nenhuma chamada previa a
        # process_message() para este execution_id) -- cria ja com o resultado final.
        await executions_repo.insert(
            Execution(
                execution_id=state["execution_id"],
                session_id=state["session_id"],
                message_id=message_id,
                routing_decision=(
                    routing_decision.model_dump(mode="json") if routing_decision else None
                ),
                agents_invoked=agents_invoked,
                final_response=final_response_dict,
            )
        )
        await executions_repo.mark_completed(state["execution_id"], final_response_dict or {})
    else:
        # Retomada (US4, FR-037): o registro de execucao ja existia (criado como "running"
        # antes do grafo rodar) -- atualiza em vez de duplicar, sem repetir etapas concluidas.
        await executions_repo.update(
            state["execution_id"],
            {
                "routing_decision": (
                    routing_decision.model_dump(mode="json") if routing_decision else None
                ),
                "agents_invoked": agents_invoked,
            },
        )
        await executions_repo.mark_completed(state["execution_id"], final_response_dict or {})

    await sessions_repo.mark_active(state["session_id"], state["execution_id"])
    await checkpointer.save(state, node_name="persist")

    if final_response is not None:
        # FR-031: captura de memoria episodica ao final de cada execucao (resolucoes, falhas,
        # escalonamentos) -- silenciosamente ignorada se nao atender aos criterios de
        # relevancia/privacidade (FR-033), nunca bloqueia a resposta ao usuario.
        await capture_episode(
            db,
            user_id=state["user_message"].user_id,
            session_id=state["session_id"],
            execution_id=state["execution_id"],
            final_response=final_response,
        )

    return state
