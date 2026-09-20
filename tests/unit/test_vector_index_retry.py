"""O startup espera o servico de busca vetorial (mongot) ficar pronto em vez de desistir."""

from __future__ import annotations

import pytest
from pymongo.errors import OperationFailure

from app.repository.mongodb import indexes

_NOT_READY = "Error connecting to Search Index Management service."


class _FakeDb:
    def __init__(self, failures: int, message: str = _NOT_READY) -> None:
        self.failures = failures
        self.message = message
        self.calls: list[str] = []

    async def command(self, cmd: dict):
        self.calls.append(cmd["createSearchIndexes"])
        if self.failures > 0:
            self.failures -= 1
            raise OperationFailure(self.message, code=125)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(indexes.asyncio, "sleep", _instant)


async def test_retries_until_the_search_service_is_ready() -> None:
    db = _FakeDb(failures=3)

    await indexes._create_vector_indexes(db, 8)

    assert db.calls[:4] == ["knowledge_chunks"] * 4  # 3 falhas + sucesso, mesma collection
    assert set(db.calls) == set(indexes._VECTOR_INDEX_COLLECTIONS)


async def test_gives_up_once_and_does_not_repeat_the_wait_for_every_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indexes, "_VECTOR_INDEX_MAX_ATTEMPTS", 3)
    db = _FakeDb(failures=10_000)

    await indexes._create_vector_indexes(db, 8)

    assert db.calls == ["knowledge_chunks"] * 3


async def test_other_errors_such_as_already_exists_are_not_retried() -> None:
    db = _FakeDb(failures=1, message="Duplicate Index")

    await indexes._create_vector_indexes(db, 8)

    assert db.calls.count("knowledge_chunks") == 1
