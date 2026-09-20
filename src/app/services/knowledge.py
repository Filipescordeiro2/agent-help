"""Servico de gestao de documentos de conhecimento (ingest/embed/reindex) -- spec FR-043."""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.settings import get_settings
from app.rag.embeddings import embed_chunks
from app.rag.loaders import load_from_text
from app.rag.loaders.web import WebFetchError, fetch_web_page, strip_urls
from app.rag.splitters import split_text
from app.rag.web_context import build_web_context, persist_web_findings
from app.repository.knowledge_chunks_repository import (
    KnowledgeChunk,
    KnowledgeChunksRepository,
)
from app.repository.knowledge_documents_repository import (
    KnowledgeDocument,
    KnowledgeDocumentsRepository,
)


class KnowledgeDocumentNotFoundError(Exception):
    pass


async def ingest_document(
    db: AsyncIOMotorDatabase,
    *,
    title: str,
    source: str,
    content: str,
    product: str | None = None,
    region: str | None = None,
    doc_type: str | None = None,
) -> KnowledgeDocument:
    documents_repo = KnowledgeDocumentsRepository(db)
    loaded = load_from_text(
        title=title, source=source, content=content, product=product, region=region
    )

    document = KnowledgeDocument(
        document_id=str(uuid.uuid4()),
        title=loaded.title,
        source=loaded.source,
        product=loaded.product,
        region=loaded.region,
        metadata={"doc_type": doc_type} if doc_type else {},
    )
    await documents_repo.insert(document)
    await reembed_document(db, document.document_id, content=content, doc_type=doc_type)
    return document


async def ingest_url(
    db: AsyncIOMotorDatabase,
    *,
    url: str,
    query: str | None = None,
    product: str | None = None,
    region: str | None = None,
) -> KnowledgeDocument:
    """Busca uma pagina web publica e a grava na base de conhecimento (RAG persistente).

    - Com `query`: grava SO os trechos da pagina relevantes para o tema (o que o agente faz ao
      responder), acrescentando-os ao documento da URL sem duplicar.
    - Sem `query`: grava a pagina inteira (limitada por `WEB_MAX_CHARS`); repetir a URL atualiza
      o documento existente (`source == url`) em vez de duplica-lo.

    Levanta `WebFetchError` se a URL nao puder ser consultada (ver `app/rag/loaders/web.py`)."""
    if query and strip_urls(query):
        settings = get_settings()
        chunks, errors = await build_web_context(
            f"{strip_urls(query)} {url}",
            top_k=settings.knowledge_default_top_k,
            min_score=settings.web_min_score,
        )
        if not chunks:
            raise (
                errors[0]
                if errors
                else WebFetchError(
                    "WEB_CONTENT_NOT_RELEVANT", "A pagina nao tem trechos relevantes para o tema."
                )
            )
        saved = await persist_web_findings(db, chunks, product=product, region=region)
        document = await KnowledgeDocumentsRepository(db).get(saved[0].document_id)
        assert document is not None
        return document

    page = await fetch_web_page(url)
    existing = await KnowledgeDocumentsRepository(db).list(filters={"source": page.url}, limit=1)
    if existing:
        return await update_document(
            db,
            existing[0].document_id,
            title=page.title,
            source=page.url,
            content=page.text,
            product=product or existing[0].product,
            region=region or existing[0].region,
            doc_type="web",
        )
    return await ingest_document(
        db,
        title=page.title,
        source=page.url,
        content=page.text,
        product=product,
        region=region,
        doc_type="web",
    )


async def update_document(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    title: str,
    source: str,
    content: str,
    product: str | None = None,
    region: str | None = None,
    doc_type: str | None = None,
) -> KnowledgeDocument:
    """Atualiza metadados e re-embeda o conteudo (usado por PUT /documents/{id} e pelo Feedback
    Agent apos aprovacao humana)."""
    repo = KnowledgeDocumentsRepository(db)
    if await repo.get(document_id) is None:
        raise KnowledgeDocumentNotFoundError(document_id)
    updated = await repo.update(
        document_id,
        {"title": title, "source": source, "product": product, "region": region},
    )
    await reembed_document(db, document_id, content=content, doc_type=doc_type)
    assert updated is not None
    return updated


async def reembed_document(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    content: str | None = None,
    doc_type: str | None = None,
) -> int:
    documents_repo = KnowledgeDocumentsRepository(db)
    chunks_repo = KnowledgeChunksRepository(db)

    document = await documents_repo.get(document_id)
    if document is None:
        raise KnowledgeDocumentNotFoundError(document_id)

    if content is None:
        existing_chunks = await chunks_repo.list_for_document(document_id)
        content = "\n".join(c.content for c in existing_chunks)

    await chunks_repo.delete_for_document(document_id)

    chunk_texts = split_text(content)
    embeddings = embed_chunks(chunk_texts)

    metadata = {
        "source": document.source,
        "product": document.product,
        "region": document.region,
        "doc_type": doc_type or document.metadata.get("doc_type"),
    }

    for text, embedding in zip(chunk_texts, embeddings, strict=True):
        await chunks_repo.insert(
            KnowledgeChunk(
                chunk_id=str(uuid.uuid4()),
                document_id=document_id,
                content=text,
                metadata=metadata,
                embedding=embedding,
            )
        )
    return len(chunk_texts)


async def reindex_all(db: AsyncIOMotorDatabase) -> dict[str, int]:
    documents_repo = KnowledgeDocumentsRepository(db)
    documents = await documents_repo.list(limit=10_000)
    counts: dict[str, int] = {}
    for document in documents:
        counts[document.document_id] = await reembed_document(db, document.document_id)
    return counts
