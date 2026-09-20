"""Tool de acesso a paginas web -- ver `app/rag/web_context.py`.

Herda de `BaseTool`: schema tipado, timeout, auditoria (`tool_call`) e metricas. O agente nunca
escolhe URLs livremente: consulta as citadas pelo usuario na mensagem e as **fontes web
cadastradas** (`web_sources`, gerenciadas pela API), sempre com as protecoes de
`app/rag/loaders/web.py` (SSRF, limites, scanner).

A tool so deve ser chamada quando a base de conhecimento NAO respondeu a pergunta (o
`KnowledgeAgent` consulta a base primeiro). Ao acessar as paginas, ela grava na base vetorial apenas
os trechos relevantes ao tema, devolve esses trechos com os ids reais da base e um relatorio de cada
fonte consultada (usado na auditoria e nas estatisticas das fontes).
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.rag.web_context import build_web_context, persist_web_findings
from app.repository.web_sources_repository import WebSourcesRepository
from app.tools.base import BaseTool


class WebSearchInput(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.25
    use_configured_sources: bool = True


class WebSearchOutput(BaseModel):
    results: list[dict]
    errors: list[dict]
    consulted: list[dict] = []  # uma entrada por pagina consultada (fonte, status, trechos...)


class SearchWebPagesTool(BaseTool[WebSearchInput, WebSearchOutput]):
    """Consulta as paginas web (citadas ou fontes cadastradas) e salva so os trechos relevantes."""

    name = "search_web_pages"
    # Ate `web_max_sources_per_question` fontes em paralelo (+ links seguidos) + embeddings.
    timeout_seconds = 90.0

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._sources_repo = WebSourcesRepository(db)

    async def _run(self, input_data: WebSearchInput) -> WebSearchOutput:
        sources = (
            await self._sources_repo.list_enabled() if input_data.use_configured_sources else []
        )
        consulted: list[dict] = []
        chunks, errors = await build_web_context(
            input_data.query,
            top_k=input_data.top_k,
            min_score=input_data.min_score,
            sources=sources,
            report=consulted,
        )
        saved = await persist_web_findings(self._db, chunks) if chunks else []
        used_urls = {c.url for c in saved}
        for entry in consulted:
            source_id = entry.get("source_id")
            entry["used_in_answer"] = entry["url"] in used_urls or any(
                link["url"] in used_urls for link in entry.get("followed_links", [])
            )
            if source_id:
                await self._sources_repo.record_consultation(
                    source_id, useful=entry["used_in_answer"], status=entry["status"]
                )
        return WebSearchOutput(
            results=[
                {
                    "document_id": c.document_id,
                    "chunk_id": c.chunk_id,
                    "url": c.url,
                    "title": c.title,
                    "content": c.content,
                    "score": c.score,
                }
                for c in saved
            ],
            errors=[{"code": e.code, "message": e.message} for e in errors],
            consulted=consulted,
        )
