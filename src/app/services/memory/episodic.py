"""Captura de memoria episodica ao final de cada execucao (spec FR-031 -- eventos relevantes
anteriores, resolucoes, resultados de Playbook, falhas/escalonamentos)."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.memory_repository import MemoryRecord
from app.schemas.agent_response import AgentResponse
from app.services.memory.service import MemoryValidationError, create_memory_record


async def capture_episode(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    execution_id: str,
    final_response: AgentResponse,
) -> MemoryRecord | None:
    """Registra um evento episodico resumindo o desfecho da execucao. Retorna `None` quando o
    conteudo nao atende aos criterios de relevancia/privacidade (FR-033) -- silenciosamente,
    pois nem toda execucao precisa virar memoria de longo prazo."""
    summary = (f"[{final_response.status.value}] {final_response.agent}: {final_response.message}")[
        :500
    ]

    try:
        return await create_memory_record(
            db,
            user_id=user_id,
            type_="episodic",
            content=summary,
            origin=f"execution:{execution_id}",
            confidence=final_response.metadata.confidence,
            session_id=session_id,
            interaction_id=execution_id,
        )
    except MemoryValidationError:
        return None
