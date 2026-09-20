"""GET /health e GET /ready -- unicas excecoes ao envelope AgentResponse completo, mas ainda
JSON estruturado e tipado (nunca uma string solta). Ficam fora do middleware de fronteira de
confianca (T045) pois sao usadas por healthchecks de infraestrutura."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config.settings import get_settings
from app.repository.mongodb.client import ping

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    dependencies: dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="OK")


@router.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    mongodb_ok = await ping()
    settings = get_settings()
    # A chave do OpenRouter vem por requisicao (X-API-Key-LLM); aqui so o modelo configurado conta.
    openrouter_ok = bool(settings.openrouter_model)
    return ReadyResponse(
        status="OK" if mongodb_ok and openrouter_ok else "ERROR",
        dependencies={
            "mongodb": "OK" if mongodb_ok else "ERROR",
            "openrouter": "OK" if openrouter_ok else "ERROR",
        },
    )
