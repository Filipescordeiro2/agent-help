"""CRUD de documentos de conhecimento + /ingest, /embed, /reindex -- spec FR-043.

Rota fina -- delega a app/services/knowledge.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.rag.loaders.web import WebFetchError
from app.repository.knowledge_documents_repository import (
    KnowledgeDocument,
    KnowledgeDocumentsRepository,
)
from app.routers.deps import get_db, require_llm_api_key
from app.routers.errors import structured_error
from app.services import knowledge as service

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class IngestRequest(BaseModel):
    title: str
    source: str
    content: str
    product: str | None = None
    region: str | None = None
    doc_type: str | None = None


class EmbedRequest(BaseModel):
    document_id: str


class ReindexRequest(BaseModel):
    document_id: str | None = None


@router.post(
    "/documents",
    response_model=KnowledgeDocument,
    dependencies=[Depends(require_llm_api_key)],
)
async def create_document(
    payload: IngestRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> KnowledgeDocument:
    return await service.ingest_document(db, **payload.model_dump())


@router.get("/documents", response_model=list[KnowledgeDocument])
async def list_documents(db: AsyncIOMotorDatabase = Depends(get_db)) -> list[KnowledgeDocument]:
    return await KnowledgeDocumentsRepository(db).list(limit=200)


@router.get("/documents/{document_id}", response_model=KnowledgeDocument)
async def get_document(
    document_id: str, db: AsyncIOMotorDatabase = Depends(get_db)
) -> KnowledgeDocument:
    document = await KnowledgeDocumentsRepository(db).get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return document


@router.put(
    "/documents/{document_id}",
    response_model=KnowledgeDocument,
    dependencies=[Depends(require_llm_api_key)],
)
async def update_document(
    document_id: str, payload: IngestRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> KnowledgeDocument:
    try:
        return await service.update_document(db, document_id, **payload.model_dump())
    except service.KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="documento nao encontrado") from exc


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    deleted = await KnowledgeDocumentsRepository(db).delete(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="documento nao encontrado")
    return {"status": "OK"}


class IngestUrlRequest(BaseModel):
    url: str
    query: str | None = None  # com tema: salva so os trechos relevantes
    product: str | None = None
    region: str | None = None


@router.post(
    "/ingest-url",
    response_model=KnowledgeDocument,
    dependencies=[Depends(require_llm_api_key)],
)
async def ingest_url(payload: IngestUrlRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Busca uma pagina web publica e a adiciona a base de conhecimento (RAG).

    Com `query` (tema), grava apenas os trechos relevantes; sem `query`, a pagina inteira
    (limitada). Repetir a mesma URL nao duplica. Protegida contra SSRF; falhas: 400 `WEB_*`."""
    try:
        return await service.ingest_url(db, **payload.model_dump())
    except WebFetchError as exc:
        return structured_error(400, exc.code, exc.message)


@router.post(
    "/ingest",
    response_model=KnowledgeDocument,
    dependencies=[Depends(require_llm_api_key)],
)
async def ingest(
    payload: IngestRequest, db: AsyncIOMotorDatabase = Depends(get_db)
) -> KnowledgeDocument:
    return await service.ingest_document(db, **payload.model_dump())


@router.post("/embed", dependencies=[Depends(require_llm_api_key)])
async def embed(payload: EmbedRequest, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    try:
        count = await service.reembed_document(db, payload.document_id)
    except service.KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="documento nao encontrado") from exc
    return {"status": "OK", "chunks": count}


@router.post("/reindex", dependencies=[Depends(require_llm_api_key)])
async def reindex(payload: ReindexRequest, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    if payload.document_id:
        try:
            count = await service.reembed_document(db, payload.document_id)
        except service.KnowledgeDocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="documento nao encontrado") from exc
        return {"status": "OK", "chunks": {payload.document_id: count}}
    counts = await service.reindex_all(db)
    return {"status": "OK", "chunks": counts}
