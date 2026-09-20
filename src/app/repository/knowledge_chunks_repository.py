"""KnowledgeChunksRepository -- colecao `knowledge_chunks` (data-model.md)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class KnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    metadata: dict = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)


class KnowledgeChunksRepository(BaseRepository[KnowledgeChunk]):
    collection_name = "knowledge_chunks"
    model = KnowledgeChunk
    id_field = "chunk_id"

    async def list_for_document(self, document_id: str) -> list[KnowledgeChunk]:
        return await self.list(filters={"document_id": document_id}, limit=1000)

    async def delete_for_document(self, document_id: str) -> int:
        result = await self._collection.delete_many({"document_id": document_id})
        return result.deleted_count

    async def all_chunks(self, filters: dict | None = None) -> list[KnowledgeChunk]:
        return await self.list(filters=filters, limit=10_000)
