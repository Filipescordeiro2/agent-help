"""CRUD de fontes web (sites que o agente consulta quando a base nao responde) + teste da fonte.

Rota fina -- delega a app/services/web_sources.py. Nao usa LLM (nao exige X-API-Key-LLM)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.rag.loaders.web import WebFetchError, fetch_web_page
from app.repository.web_sources_repository import WebSource, WebSourcesRepository
from app.routers.deps import get_db
from app.routers.errors import structured_error
from app.services import web_sources as service

router = APIRouter(prefix="/api/v1/web-sources", tags=["web-sources"])


class WebSourceInput(BaseModel):
    name: str = Field(min_length=1, description="Nome curto da fonte")
    url: str = Field(min_length=1, description="URL da pagina/site (http/https, porta 80/443)")
    description: str = Field(
        min_length=1, description="PRINCIPAL: o que e este site / do que ele trata"
    )
    usage: str = Field(min_length=1, description="USO: quando e para que o agente deve consulta-lo")
    topics: list[str] = Field(default_factory=list, description="Temas/palavras-chave da fonte")
    priority: int = Field(default=100, description="Menor numero = consultada primeiro")
    enabled: bool = True


class WebSourceTestResult(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str | None = None
    title: str | None = None
    text_chars: int = 0
    links_found: int = 0
    truncated: bool = False
    preview: str | None = None


def _error(exc: WebFetchError):
    return structured_error(400, exc.code, exc.message)


def _conflict():
    return structured_error(409, "WEB_SOURCE_ALREADY_EXISTS", "Ja existe uma fonte com esta URL.")


@router.post("", response_model=WebSource)
async def create_web_source(payload: WebSourceInput, db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        return await service.create_web_source(db, **payload.model_dump())
    except WebFetchError as exc:
        return _error(exc)
    except service.WebSourceConflictError:
        return _conflict()


@router.get("", response_model=list[WebSource])
async def list_web_sources(
    enabled: bool | None = None, db: AsyncIOMotorDatabase = Depends(get_db)
) -> list[WebSource]:
    sources = await WebSourcesRepository(db).list_all()
    return [s for s in sources if enabled is None or s.enabled == enabled]


@router.get("/{source_id}", response_model=WebSource)
async def get_web_source(source_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> WebSource:
    try:
        return await service.get_web_source(db, source_id)
    except service.WebSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="fonte web nao encontrada") from exc


@router.put("/{source_id}", response_model=WebSource)
async def update_web_source(
    source_id: str, payload: WebSourceInput, db: AsyncIOMotorDatabase = Depends(get_db)
):
    try:
        return await service.update_web_source(db, source_id, **payload.model_dump())
    except service.WebSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="fonte web nao encontrada") from exc
    except WebFetchError as exc:
        return _error(exc)
    except service.WebSourceConflictError:
        return _conflict()


@router.delete("/{source_id}")
async def delete_web_source(source_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    if not await service.delete_web_source(db, source_id):
        raise HTTPException(status_code=404, detail="fonte web nao encontrada")
    return {"status": "OK"}


@router.post("/{source_id}/test", response_model=WebSourceTestResult)
async def test_web_source(
    source_id: str, db: AsyncIOMotorDatabase = Depends(get_db)
) -> WebSourceTestResult:
    """Acessa a fonte agora (mesmas protecoes do agente) e mostra o extraido; nada e salvo."""
    try:
        source = await service.get_web_source(db, source_id)
    except service.WebSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="fonte web nao encontrada") from exc
    try:
        page = await fetch_web_page(source.url)
    except WebFetchError as exc:
        return WebSourceTestResult(ok=False, error_code=exc.code, message=exc.message)
    return WebSourceTestResult(
        ok=True,
        title=page.title,
        text_chars=len(page.text),
        links_found=len(page.links),
        truncated=page.truncated,
        preview=page.text[:400],
    )
