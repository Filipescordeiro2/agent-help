"""T122: teste de integracao para expiracao automatica de memoria -- spec FR-034,
research.md #13."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.repository.memory_repository import (
    MemoryRecord,
    MemoryRecordsRepository,
    MemoryStatus,
    MemoryType,
    RetentionPolicy,
    SemanticMemoriesRepository,
    SemanticMemory,
)
from app.services.memory.retention_job import run_retention_job


async def test_overdue_record_is_marked_expired_without_manual_deletion(test_db) -> None:
    repo = MemoryRecordsRepository(test_db)
    overdue_record = MemoryRecord(
        memory_id="mem_overdue",
        user_id="client_xpto",
        type=MemoryType.EPISODIC,
        content="evento antigo",
        origin="test",
        created_at=datetime.now(UTC) - timedelta(days=800),
        retention_policy=RetentionPolicy(ttl_days=730),
    )
    await repo.insert(overdue_record)

    result = await run_retention_job(test_db)

    assert result["memory_records_expired"] == 1
    updated = await repo.get("mem_overdue")
    assert updated.status == MemoryStatus.EXPIRED


async def test_record_within_retention_window_is_not_expired(test_db) -> None:
    repo = MemoryRecordsRepository(test_db)
    fresh_record = MemoryRecord(
        memory_id="mem_fresh",
        user_id="client_xpto",
        type=MemoryType.EPISODIC,
        content="evento recente",
        origin="test",
        created_at=datetime.now(UTC) - timedelta(days=5),
        retention_policy=RetentionPolicy(ttl_days=730),
    )
    await repo.insert(fresh_record)

    result = await run_retention_job(test_db)

    assert result["memory_records_expired"] == 0
    updated = await repo.get("mem_fresh")
    assert updated.status == MemoryStatus.ACTIVE


async def test_retention_job_is_idempotent(test_db) -> None:
    repo = MemoryRecordsRepository(test_db)
    await repo.insert(
        MemoryRecord(
            memory_id="mem_overdue2",
            user_id="u",
            type=MemoryType.EPISODIC,
            content="x",
            origin="test",
            created_at=datetime.now(UTC) - timedelta(days=800),
            retention_policy=RetentionPolicy(ttl_days=730),
        )
    )

    first_run = await run_retention_job(test_db)
    second_run = await run_retention_job(test_db)

    assert first_run["memory_records_expired"] == 1
    assert second_run["memory_records_expired"] == 0  # ja expirado, nao reprocessado


async def test_semantic_memories_are_also_expired(test_db) -> None:
    repo = SemanticMemoriesRepository(test_db)
    await repo.insert(
        SemanticMemory(
            memory_id="sem_overdue",
            user_id="u",
            type=MemoryType.SEMANTIC,
            content="fato antigo",
            origin="test",
            created_at=datetime.now(UTC) - timedelta(days=800),
            retention_policy=RetentionPolicy(ttl_days=730),
            embedding=[1.0, 0.0],
        )
    )

    result = await run_retention_job(test_db)

    assert result["semantic_memories_expired"] == 1
