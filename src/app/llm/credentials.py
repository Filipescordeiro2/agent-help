"""Credencial do LLM por requisicao (cabecalho `X-API-Key-LLM`) -- a chave do OpenRouter nao mora
no `.env`, entao trocar de chave nao exige reiniciar nem recompilar o servico.

A chave fica em um `ContextVar` durante a requisicao (ou seja, visivel para o grafo, os agentes e
o cliente de embeddings que rodam dentro dela) e e descartada ao final. Ela NUNCA e logada,
auditada, colocada em metrica/span nem devolvida em respostas. Fallback opcional para
`OPENROUTER_API_KEY` do ambiente (uso headless, ex.: rotina agendada do Feedback Agent) -- vazio
por padrao.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.config.settings import get_settings

LLM_API_KEY_HEADER = "X-API-Key-LLM"
_MAX_KEY_LENGTH = 512

_request_api_key: ContextVar[str | None] = ContextVar("request_llm_api_key", default=None)


class LLMApiKeyMissingError(Exception):
    """Nenhuma chave do OpenRouter disponivel (nem no cabecalho da requisicao, nem no ambiente)."""


def normalize_api_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > _MAX_KEY_LENGTH:
        return None
    return value


def set_request_api_key(raw: str | None) -> Token[str | None]:
    return _request_api_key.set(normalize_api_key(raw))


def reset_request_api_key(token: Token[str | None]) -> None:
    _request_api_key.reset(token)


def _env_api_key() -> str | None:
    return normalize_api_key(get_settings().openrouter_api_key)


def has_api_key() -> bool:
    return _request_api_key.get() is not None or _env_api_key() is not None


def resolve_api_key() -> str:
    """Chave da requisicao corrente, ou a do ambiente como fallback; senao levanta erro."""
    key = _request_api_key.get() or _env_api_key()
    if key is None:
        raise LLMApiKeyMissingError(
            f"Chave do OpenRouter ausente: envie o cabecalho {LLM_API_KEY_HEADER}."
        )
    return key
