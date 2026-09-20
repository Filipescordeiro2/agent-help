"""Job de expiracao automatica de memoria por retencao (spec FR-034, research.md #13).

Varre `memory_records`/`semantic_memories` e marca `status=expired` todo registro cujo
`created_at + retention_policy.ttl_days` ja passou -- complementa a exclusao sob demanda do
titular (ja implementada em app/services/memory/service.py::delete_memory_record). Idempotente:
registros ja expirados sao simplesmente ignorados em execucoes seguintes. Executavel por
agendamento externo (cron, scheduled task) ou manualmente via chamada direta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.memory_repository import (
    MemoryRecordsRepository,
    MemoryStatus,
    SemanticMemoriesRepository,
)


async def _expire_overdue_records(
    repo: MemoryRecordsRepository | SemanticMemoriesRepository,
) -> int:
    active_records = await repo.list(filters={"status": MemoryStatus.ACTIVE.value}, limit=10_000)
    now = datetime.now(UTC)
    expired_count = 0

    for record in active_records:
        deadline = record.created_at + timedelta(days=record.retention_policy.ttl_days)
        if now >= deadline:
            await repo.update(record.memory_id, {"status": MemoryStatus.EXPIRED.value})
            expired_count += 1

    return expired_count


async def run_retention_job(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """Executa a expiracao para ambas as colecoes de memoria de longo prazo/semantica/
    episodica. Retorna quantos registros foram expirados em cada uma (idempotente -- rodar de
    novo sem novos registros vencidos expira zero)."""
    memory_records_expired = await _expire_overdue_records(MemoryRecordsRepository(db))
    semantic_memories_expired = await _expire_overdue_records(SemanticMemoriesRepository(db))
    return {
        "memory_records_expired": memory_records_expired,
        "semantic_memories_expired": semantic_memories_expired,
    }
