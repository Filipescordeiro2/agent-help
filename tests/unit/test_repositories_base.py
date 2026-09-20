"""T055: teste unitario do repositorio base (CRUD via mongomock-motor)."""

from __future__ import annotations

from mongomock_motor import AsyncMongoMockClient
from pydantic import BaseModel

from app.repository.base import BaseRepository


class _Widget(BaseModel):
    widget_id: str
    name: str


class _WidgetsRepository(BaseRepository[_Widget]):
    collection_name = "widgets"
    model = _Widget
    id_field = "widget_id"


def _repo() -> _WidgetsRepository:
    db = AsyncMongoMockClient()["getnet_test"]
    return _WidgetsRepository(db)


async def test_insert_and_get_round_trip() -> None:
    repo = _repo()
    await repo.insert(_Widget(widget_id="w1", name="Widget 1"))

    result = await repo.get("w1")

    assert result is not None
    assert result.name == "Widget 1"


async def test_get_returns_none_when_missing() -> None:
    repo = _repo()
    assert await repo.get("does-not-exist") is None


async def test_update_changes_fields() -> None:
    repo = _repo()
    await repo.insert(_Widget(widget_id="w1", name="Original"))

    updated = await repo.update("w1", {"name": "Updated"})

    assert updated is not None
    assert updated.name == "Updated"


async def test_delete_removes_document() -> None:
    repo = _repo()
    await repo.insert(_Widget(widget_id="w1", name="To delete"))

    deleted = await repo.delete("w1")

    assert deleted is True
    assert await repo.get("w1") is None


async def test_delete_returns_false_when_missing() -> None:
    repo = _repo()
    assert await repo.delete("does-not-exist") is False


async def test_list_respects_filters_and_limit() -> None:
    repo = _repo()
    for i in range(5):
        await repo.insert(_Widget(widget_id=f"w{i}", name="matching" if i < 3 else "other"))

    results = await repo.list(filters={"name": "matching"}, limit=10)

    assert len(results) == 3
