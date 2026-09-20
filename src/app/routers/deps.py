"""Dependencias compartilhadas das rotas FastAPI."""

from __future__ import annotations

from fastapi import Security
from fastapi.security import APIKeyHeader
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.settings import get_settings
from app.llm.credentials import LLM_API_KEY_HEADER, resolve_api_key
from app.repository.mongodb.client import get_database


def get_db() -> AsyncIOMotorDatabase:
    return get_database()


# Declara o cabecalho no OpenAPI (Swagger: botao Authorize) so nas rotas que usam esta dependencia.
# A leitura real da chave e feita pelo middleware (app/llm/credentials.py).
_llm_api_key_header = APIKeyHeader(
    name=LLM_API_KEY_HEADER,
    scheme_name="LLMApiKey",
    auto_error=False,
    description="Chave do OpenRouter (por requisicao; nao fica no .env).",
)


def require_llm_api_key(_llm_api_key: str | None = Security(_llm_api_key_header)) -> None:
    """Falha cedo (400 LLM_API_KEY_REQUIRED) nas rotas que chamam LLM/embeddings quando nao ha chave
    do OpenRouter -- nem no cabecalho X-API-Key-LLM, nem no ambiente. No modo fake nao exige."""
    if get_settings().llm_provider == "fake":
        return
    resolve_api_key()
