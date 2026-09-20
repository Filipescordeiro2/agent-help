"""Captura de memoria de longo prazo -- preferencias e informacoes persistentes autorizadas
(spec FR-031).

Nenhum agente atual extrai preferencias explicitas da conversa (isso exigiria um passo de
classificacao dedicado, fora do escopo desta versao) -- esta funcao fica disponivel como o
ponto de integracao pronto para uso assim que essa extracao for definida, mantendo o mesmo
criterio de relevancia/privacidade (FR-033) de qualquer outro registro de memoria.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.memory_repository import MemoryRecord
from app.services.memory.service import create_memory_record


async def capture_preference(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    content: str,
    origin: str,
    confidence: float = 1.0,
    session_id: str | None = None,
) -> MemoryRecord:
    return await create_memory_record(
        db,
        user_id=user_id,
        type_="long_term",
        content=content,
        origin=origin,
        confidence=confidence,
        session_id=session_id,
    )
