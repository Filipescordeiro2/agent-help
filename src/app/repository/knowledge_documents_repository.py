"""KnowledgeDocumentsRepository -- colecao `knowledge_documents` (data-model.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class KnowledgeDocumentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class KnowledgeDocument(BaseModel):
    document_id: str
    title: str
    source: str
    source_version: str = "1"
    product: str | None = None
    region: str | None = None
    status: KnowledgeDocumentStatus = KnowledgeDocumentStatus.ACTIVE
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict)


class KnowledgeDocumentsRepository(BaseRepository[KnowledgeDocument]):
    collection_name = "knowledge_documents"
    model = KnowledgeDocument
    id_field = "document_id"
