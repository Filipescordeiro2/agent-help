"""Repositorio generico base -- nenhum agente acessa o MongoDB diretamente.

Todo repositorio de dominio herda desta classe, que encapsula as operacoes CRUD basicas
sobre uma unica colecao, usando um schema Pydantic para validar o que entra e sai.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseRepository(Generic[ModelT]):
    collection_name: str
    model: type[ModelT]
    id_field: str = "_id"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._collection = db[self.collection_name]

    async def insert(self, document: ModelT) -> ModelT:
        payload = document.model_dump(mode="json")
        await self._collection.insert_one(payload)
        return document

    async def get(self, id_value: str) -> ModelT | None:
        raw = await self._collection.find_one({self.id_field: id_value})
        if raw is None:
            return None
        raw.pop("_id", None) if self.id_field != "_id" else None
        return self.model.model_validate(raw)

    async def update(self, id_value: str, updates: dict[str, Any]) -> ModelT | None:
        await self._collection.update_one({self.id_field: id_value}, {"$set": updates})
        return await self.get(id_value)

    async def delete(self, id_value: str) -> bool:
        result = await self._collection.delete_one({self.id_field: id_value})
        return result.deleted_count > 0

    async def list(
        self, filters: dict[str, Any] | None = None, limit: int = 50, cursor: int = 0
    ) -> list[ModelT]:
        query = filters or {}
        raw_docs = await self._collection.find(query).skip(cursor).to_list(length=limit)
        return [self.model.model_validate(doc) for doc in raw_docs]
