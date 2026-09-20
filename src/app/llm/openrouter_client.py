"""Camada UNICA de abstracao sobre o OpenRouter (Constitution Principio VII).

Nenhum outro modulo deve importar `langchain_openai` diretamente -- todo acesso a modelos de
linguagem passa por `get_chat_model()` abaixo. Troca de modelo/provedor e feita apenas por
configuracao (OPENROUTER_MODEL / OPENROUTER_BASE_URL); a chave do OpenRouter vem no cabecalho
X-API-Key-LLM (app/llm/credentials.py). Um nome de modelo alternativo (ex.: modelo mais barato
para grounding) e repassado pelo mesmo cliente -- nunca um cliente paralelo.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import pybreaker
import structlog
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import get_settings
from app.llm.credentials import resolve_api_key
from app.llm.usage import TokenUsageCallback

logger = structlog.get_logger(__name__)

_circuit_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30)

# Respostas deterministicas do modo fake (LLM_PROVIDER=fake), por schema de saida estruturada.
# `factory(user_text)` recebe o ultimo conteudo `user` das mensagens; uma factory que declare um
# segundo parametro recebe tambem o conteudo `system` (util para ecoar o contexto recuperado).
_FAKE_DEFAULTS: dict[type, Callable[..., BaseModel]] = {}


def register_fake_default(schema: type, factory: Callable[[str], BaseModel]) -> None:
    _FAKE_DEFAULTS[schema] = factory


class RetryableLLMError(Exception):
    """Erro transitorio (rate limit, timeout, falha de rede) elegivel para retry."""


def _first_system_text(messages: Any) -> str:
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "system":
                return str(message.get("content", ""))
    return ""


def _call_factory(factory: Callable[..., BaseModel], messages: Any) -> BaseModel:
    if len(inspect.signature(factory).parameters) >= 2:
        return factory(_last_user_text(messages), _first_system_text(messages))
    return factory(_last_user_text(messages))


def _last_user_text(messages: Any) -> str:
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                return str(message.get("content", ""))
    return ""


class FakeChatModel:
    """Modo fake para testes/dev offline (LLM_PROVIDER=fake) -- nunca usado em producao."""

    def __init__(self, canned_response: str = "{}") -> None:
        self._canned_response = canned_response
        self._schema: type | None = None

    def with_structured_output(self, schema: type, **_: Any) -> FakeChatModel:
        # Copia -- o modelo fake e compartilhado (cache) e nao pode guardar o schema da chamada.
        bound = copy.copy(self)
        bound._schema = schema
        return bound

    async def ainvoke(self, messages: Any = None, *_: Any, **__: Any) -> Any:
        if self._schema is not None:
            factory = _FAKE_DEFAULTS.get(self._schema)
            if factory is not None:
                return _call_factory(factory, messages)
            return self._schema.model_construct()
        return self._canned_response


@lru_cache
def _fake_chat_model() -> FakeChatModel:
    from app.llm import fake_defaults  # noqa: F401 -- registra defaults deterministicos

    return FakeChatModel()


def clear_chat_model_cache() -> None:
    """Descarta o modelo fake em cache (usado por testes ao trocar o provedor)."""
    _fake_chat_model.cache_clear()


def get_chat_model(model_name: str | None = None) -> ChatOpenAI | FakeChatModel:
    """Modelo de chat. No modo real NAO ha cache: a chave (X-API-Key-LLM) muda por requisicao e
    nao deve ficar retida em memoria alem dela -- ver `app/llm/credentials.py`."""
    settings = get_settings()
    if settings.llm_provider == "fake":
        return _fake_chat_model()
    extra_body: dict[str, Any] = {}
    if settings.openrouter_reasoning_effort:
        # Parametro de raciocinio no formato do OpenRouter (ignorado por modelos sem raciocinio).
        extra_body["reasoning"] = {"effort": settings.openrouter_reasoning_effort}
    return ChatOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=resolve_api_key(),
        model=model_name or settings.openrouter_model,
        timeout=settings.openrouter_timeout_seconds,
        max_tokens=settings.openrouter_max_tokens,
        extra_body=extra_body or None,
        callbacks=[TokenUsageCallback()],
    )


@_circuit_breaker
@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(RetryableLLMError),
)
async def invoke_chat_model(prompt_messages: list[dict[str, str]], **kwargs: Any) -> Any:
    """Ponto unico de chamada ao LLM -- retries controlados + circuit breaker."""
    model = get_chat_model()
    try:
        return await model.ainvoke(prompt_messages, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- classificamos e relancamos abaixo
        logger.warning("llm_call_failed", error=str(exc))
        raise RetryableLLMError(str(exc)) from exc
