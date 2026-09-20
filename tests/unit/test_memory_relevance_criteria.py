"""T121: teste unitario dos criterios de relevancia/privacidade/retencao aplicados antes de
persistir um registro de memoria (spec FR-033)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.services.memory.service as memory_service
from app.repository.memory_repository import (
    MemoryRecordsRepository,
    MemoryType,
    SemanticMemoriesRepository,
)
from app.services.memory.service import (
    MemoryValidationError,
    create_memory_record,
    meets_relevance_and_privacy_criteria,
)


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_service, "embed_text", lambda text: [1.0, 0.0])


def test_empty_content_is_never_relevant() -> None:
    assert meets_relevance_and_privacy_criteria("", confidence=0.9) is False
    assert meets_relevance_and_privacy_criteria("   ", confidence=0.9) is False


def test_low_confidence_content_is_not_relevant() -> None:
    assert meets_relevance_and_privacy_criteria("algo relevante", confidence=0.1) is False


def test_content_above_max_length_is_rejected() -> None:
    huge_content = "x" * 5000
    assert meets_relevance_and_privacy_criteria(huge_content, confidence=0.9) is False


def test_normal_content_with_sufficient_confidence_is_relevant() -> None:
    assert (
        meets_relevance_and_privacy_criteria("cliente resolveu o problema", confidence=0.9) is True
    )


async def test_create_memory_record_raises_when_criteria_not_met(db) -> None:
    with pytest.raises(MemoryValidationError):
        await create_memory_record(
            db, user_id="u", type_="episodic", content="", origin="test", confidence=0.9
        )


async def test_create_memory_record_applies_default_retention_by_type(db) -> None:
    record = await create_memory_record(
        db, user_id="u", type_="episodic", content="evento relevante", origin="test", confidence=0.9
    )
    assert record.retention_policy.ttl_days == memory_service._DEFAULT_TTL_DAYS[MemoryType.EPISODIC]

    stored = await MemoryRecordsRepository(db).get(record.memory_id)
    assert stored is not None


async def test_create_semantic_memory_record_includes_embedding(db) -> None:
    record = await create_memory_record(
        db,
        user_id="u",
        type_="semantic",
        content="fato relevante sobre o cliente",
        origin="test",
        confidence=0.9,
    )
    assert record.embedding

    stored = await SemanticMemoriesRepository(db).get(record.memory_id)
    assert stored is not None
    assert stored.embedding
