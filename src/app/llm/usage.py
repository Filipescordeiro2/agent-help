"""Captura de uso de tokens de chamadas de LLM (base de TPM e tokens por usuario/sessao).

O callback e anexado ao modelo no momento da construcao (`get_chat_model`), de modo que a
assinatura `ainvoke(messages)` permanece inalterada. Ele acumula o uso numa lista guardada em um
contextvar (`collect_usage`); `record_llm_usage` incrementa a metrica de baixa cardinalidade
(`getnet_llm_tokens`) e emite um evento de auditoria `llm_call` (fonte dos endpoints JSON, com
`session_id` vindo do contexto). Nunca propaga falha de observabilidade.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.config.settings import get_settings
from app.observability.audit import safe_emit_audit_event
from app.observability.instrumentation import current_agent_var, current_node_var
from app.observability.metrics import get_instruments

logger = structlog.get_logger(__name__)

FAKE_USAGE_PROMPT_TOKENS = 10
FAKE_USAGE_COMPLETION_TOKENS = 5


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


_usage_collector_var: contextvars.ContextVar[list[TokenUsage] | None] = contextvars.ContextVar(
    "llm_usage_collector", default=None
)


@contextmanager
def collect_usage() -> Iterator[list[TokenUsage]]:
    usages: list[TokenUsage] = []
    token = _usage_collector_var.set(usages)
    try:
        yield usages
    finally:
        _usage_collector_var.reset(token)


def _extract_usage(response: LLMResult) -> TokenUsage | None:
    model = "unknown"
    if response.llm_output:
        model = str(response.llm_output.get("model_name") or model)
        raw = response.llm_output.get("token_usage")
        if isinstance(raw, dict) and raw.get("total_tokens") is not None:
            return TokenUsage(
                int(raw.get("prompt_tokens", 0)),
                int(raw.get("completion_tokens", 0)),
                int(raw["total_tokens"]),
                model,
            )
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata: Any = getattr(message, "usage_metadata", None)
            if metadata:
                prompt = int(metadata.get("input_tokens", 0))
                completion = int(metadata.get("output_tokens", 0))
                response_metadata = getattr(message, "response_metadata", {}) or {}
                model = str(response_metadata.get("model_name") or model)
                total = int(metadata.get("total_tokens", prompt + completion))
                return TokenUsage(prompt, completion, total, model)
    return None


class TokenUsageCallback(BaseCallbackHandler):
    """Acumula o uso de tokens no coletor do contexto corrente (se houver)."""

    run_inline = True

    def on_llm_end(self, response: LLMResult, **_kwargs: Any) -> None:
        collector = _usage_collector_var.get()
        if collector is None:
            return
        usage = _extract_usage(response)
        if usage is not None:
            collector.append(usage)


def fake_usage() -> TokenUsage:
    return TokenUsage(
        FAKE_USAGE_PROMPT_TOKENS,
        FAKE_USAGE_COMPLETION_TOKENS,
        FAKE_USAGE_PROMPT_TOKENS + FAKE_USAGE_COMPLETION_TOKENS,
        "fake",
    )


async def record_llm_usage(usages: list[TokenUsage]) -> None:
    """Registra metrica + evento de auditoria para cada uso coletado; nunca levanta excecao."""
    try:
        settings = get_settings()
        if not usages and settings.llm_provider == "fake":
            usages = [fake_usage()]
        agent = current_agent_var.get() or current_node_var.get() or "unknown"
        for usage in usages:
            counter = get_instruments().llm_tokens
            counter.add(
                usage.prompt_tokens, {"agent": agent, "model": usage.model, "token_type": "prompt"}
            )
            counter.add(
                usage.completion_tokens,
                {"agent": agent, "model": usage.model, "token_type": "completion"},
            )
            await safe_emit_audit_event(
                actor="system",
                actor_name=agent,
                event_type="llm_call",
                status="ok",
                safe_metadata={
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "model": usage.model,
                },
            )
    except Exception:  # noqa: BLE001 -- observabilidade nunca derruba a chamada de LLM
        logger.warning("llm_usage_record_failed")
