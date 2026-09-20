"""RAG sobre paginas web: busca a pagina, escolhe o que importa para a pergunta e SALVA na base.

Fluxo (a base semantica sempre e consultada primeiro -- o site so e acessado quando a base nao
responde; ver `KnowledgeAgent`):

1. `build_web_context`: consulta, nesta ordem, (a) as URLs citadas na pergunta e, se nada
   relevante veio delas, (b) as **fontes web cadastradas** (`web_sources`) -- TODAS as
   habilitadas, em paralelo, ate `WEB_MAX_SOURCES_PER_QUESTION` -- para ver qual de fato tem a
   informacao. Cada pagina e dividida em chunks, vetorizada e ranqueada por similaridade com a
   pergunta. Se a pagina inicial de um site nao traz nada relevante, segue ate
   `WEB_MAX_LINKED_PAGES` links do MESMO site cujo texto combina com a pergunta.
2. `persist_web_findings`: grava na base vetorial (`knowledge_documents` + `knowledge_chunks`)
   APENAS os trechos relevantes -- nunca o site inteiro --, com a URL de origem e a data da
   consulta. Na proxima pergunta sobre o tema a resposta sai direto da base, sem ir ao site.

Um documento por URL: novas consultas acrescentam apenas trechos ainda nao salvos (dedupe por
conteudo). Os trechos passam pelo scanner de seguranca (dentro de `fetch_web_page`) antes de
qualquer gravacao e continuam sendo tratados como dado nao confiavel pelos agentes.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.settings import get_settings
from app.llm.embeddings_client import embed_text
from app.rag.embeddings import embed_chunks
from app.rag.loaders.web import (
    WebFetchError,
    extract_urls,
    fetch_web_page,
    select_relevant_links,
    strip_urls,
    tokens,
)
from app.rag.retrievers import cosine_similarity
from app.rag.splitters import split_text
from app.repository.knowledge_chunks_repository import KnowledgeChunk, KnowledgeChunksRepository
from app.repository.knowledge_documents_repository import (
    KnowledgeDocument,
    KnowledgeDocumentsRepository,
)
from app.repository.web_sources_repository import WebSource

logger = structlog.get_logger(__name__)

_MAX_CHUNKS_PER_PAGE = 60


@dataclass
class WebContextChunk:
    url: str
    title: str
    content: str
    score: float  # similaridade cosseno com a pergunta, em [0, 1]
    embedding: list[float] = field(default_factory=list, repr=False)


@dataclass
class SavedWebChunk:
    """Trecho ja gravado na base (ids reais de `knowledge_documents`/`knowledge_chunks`)."""

    document_id: str
    chunk_id: str
    url: str
    title: str
    content: str
    score: float


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def rank_sources(question: str, sources: list[WebSource], limit: int) -> list[WebSource]:
    """Ate `limit` fontes: se cabem todas, todas (ordem de prioridade); senao as mais aderentes a
    pergunta (palavras em comum com nome/descricao/uso/temas, ponderadas pela raridade),
    desempatando pela prioridade."""
    if len(sources) <= limit:
        return sources
    wanted = tokens(question)
    bags = {
        s.source_id: tokens(" ".join([s.name, s.description, s.usage, *s.topics])) for s in sources
    }
    # Peso por raridade (IDF): palavras que aparecem em quase todas as fontes ("aplicativo",
    # "maquininha") valem pouco; as que distinguem uma fonte ("senha", "estorno", "pix") valem mais.
    frequency = Counter(word for bag in bags.values() for word in bag)

    def relevance(source: WebSource) -> float:
        return sum(
            math.log(1 + len(sources) / frequency[word]) for word in wanted & bags[source.source_id]
        )

    return sorted(sources, key=lambda s: (-relevance(s), s.priority))[:limit]


async def build_web_context(
    query: str,
    *,
    top_k: int,
    min_score: float,
    sources: list[WebSource] | None = None,
    report: list[dict] | None = None,
) -> tuple[list[WebContextChunk], list[WebFetchError]]:
    """Trechos mais relevantes das paginas web para `query`. Retorna (chunks, erros).

    `sources`: fontes cadastradas a consultar quando as URLs da propria pergunta nao trazem nada
    relevante. `report` (opcional) recebe um registro por pagina consultada -- fonte, status,
    quantos trechos relevantes, links seguidos -- para a auditoria e as estatisticas das fontes.

    Falhas de uma URL (bloqueada, fora do ar, conteudo nao suportado...) nunca derrubam o
    atendimento: viram erros e o agente segue com o que tiver."""
    settings = get_settings()
    if not settings.web_access_enabled:
        return [], []
    urls = extract_urls(query, limit=settings.web_max_urls_per_message)
    candidates = [s for s in (sources or []) if s.enabled and s.url not in urls]
    if not urls and not candidates:
        return [], []

    # As proprias URLs diluiriam a similaridade: embeda so o texto da pergunta.
    question = strip_urls(query) or query
    query_embedding = await asyncio.to_thread(embed_text, question)
    errors: list[WebFetchError] = []
    entries = report if report is not None else []

    async def scored_chunks(url: str, title: str, text: str) -> list[WebContextChunk]:
        texts = split_text(text)[:_MAX_CHUNKS_PER_PAGE]
        embeddings = await asyncio.to_thread(embed_chunks, texts)
        found = [
            WebContextChunk(
                url=url,
                title=title,
                content=chunk,
                score=cosine_similarity(query_embedding, embedding),
                embedding=embedding,
            )
            for chunk, embedding in zip(texts, embeddings, strict=True)
        ]
        return [c for c in found if c.score >= min_score]

    async def collect(url: str, source: WebSource | None) -> list[WebContextChunk]:
        entry: dict = {
            "url": url,
            "source_id": source.source_id if source else None,
            "source_name": source.name if source else None,
            "status": "ok",
            "relevant_chunks": 0,
            "followed_links": [],
        }
        entries.append(entry)
        try:
            page = await fetch_web_page(url)
        except WebFetchError as exc:
            logger.info("web_context_url_skipped", error_code=exc.code)
            errors.append(exc)
            entry["status"] = exc.code
            return []
        entry.update(title=page.title, truncated=page.truncated, links_found=len(page.links))
        relevant = await scored_chunks(url, page.title, page.text)
        if not relevant and settings.web_follow_links:
            # A pagina inicial costuma ser so vitrine/menu: segue links do mesmo site que combinem
            # com a pergunta (nunca navega as cegas: exige palavras em comum).
            for link, anchor in select_relevant_links(
                page, question, limit=settings.web_max_linked_pages
            ):
                followed = {
                    "url": link,
                    "anchor": anchor[:80],
                    "status": "ok",
                    "relevant_chunks": 0,
                }
                entry["followed_links"].append(followed)
                try:
                    sub = await fetch_web_page(link)
                except WebFetchError as exc:
                    errors.append(exc)
                    followed["status"] = exc.code
                    continue
                sub_relevant = await scored_chunks(link, sub.title, sub.text)
                followed["relevant_chunks"] = len(sub_relevant)
                relevant.extend(sub_relevant)
        entry["relevant_chunks"] = len(relevant)
        return relevant

    relevant: list[WebContextChunk] = []
    for url in urls:
        relevant.extend(await collect(url, None))
    if not relevant and candidates:
        chosen = rank_sources(question, candidates, settings.web_max_sources_per_question)
        # "Nao sabe? bate em todas e ve qual de fato tem": consulta em paralelo.
        results = await asyncio.gather(*(collect(s.url, s) for s in chosen))
        for found in results:
            relevant.extend(found)
    relevant.sort(key=lambda c: c.score, reverse=True)
    return relevant[:top_k], errors


async def persist_web_findings(
    db: AsyncIOMotorDatabase,
    chunks: list[WebContextChunk],
    *,
    product: str | None = None,
    region: str | None = None,
) -> list[SavedWebChunk]:
    """Grava na base vetorial so os trechos relevantes (com URL de origem e data da consulta).

    Reaproveita o documento da URL, se existir, e nao duplica trechos ja salvos."""
    documents_repo = KnowledgeDocumentsRepository(db)
    chunks_repo = KnowledgeChunksRepository(db)
    fetched_at = datetime.now(UTC).isoformat()
    saved: list[SavedWebChunk] = []

    for url in dict.fromkeys(c.url for c in chunks):
        url_chunks = [c for c in chunks if c.url == url]
        existing = await documents_repo.list(filters={"source": url}, limit=1)
        if existing:
            document = existing[0]
            known = {
                _content_hash(c.content): c.chunk_id
                for c in await chunks_repo.list_for_document(document.document_id)
            }
        else:
            document = KnowledgeDocument(
                document_id=str(uuid.uuid4()),
                title=url_chunks[0].title,
                source=url,
                product=product,
                region=region,
                metadata={"doc_type": "web", "fetched_at": fetched_at, "origin": "web_rag"},
            )
            await documents_repo.insert(document)
            known = {}

        for chunk in url_chunks:
            key = _content_hash(chunk.content)
            chunk_id = known.get(key)
            if chunk_id is None:
                chunk_id = str(uuid.uuid4())
                await chunks_repo.insert(
                    KnowledgeChunk(
                        chunk_id=chunk_id,
                        document_id=document.document_id,
                        content=chunk.content,
                        metadata={
                            "source": url,
                            "product": document.product,
                            "region": document.region,
                            "doc_type": "web",
                            "fetched_at": fetched_at,
                        },
                        embedding=chunk.embedding,
                    )
                )
                known[key] = chunk_id
            saved.append(
                SavedWebChunk(
                    document_id=document.document_id,
                    chunk_id=chunk_id,
                    url=url,
                    title=document.title,
                    content=chunk.content,
                    score=chunk.score,
                )
            )
        await documents_repo.update(document.document_id, {"updated_at": datetime.now(UTC)})
    return saved
