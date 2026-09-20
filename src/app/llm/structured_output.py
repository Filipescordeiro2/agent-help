"""Helper de saida estruturada -- forca o modelo a retornar uma instancia validada do schema.

Uma unica retentativa em caso de saida invalida; se persistir, o chamador deve escalonar
para AgentResponse(status=ERROR) (research.md #4). Nunca depende de parsing de texto livre.
Todo uso de tokens e registrado (metrica + auditoria) -- ver `app/llm/usage.py`.
"""

from __future__ import annotations

from typing import TypeVar

import openai
import structlog
from pydantic import BaseModel, ValidationError

from app.config.settings import get_settings
from app.llm.openrouter_client import get_chat_model
from app.llm.usage import collect_usage, record_llm_usage
from app.observability.trace import emit_trace

logger = structlog.get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredOutputError(Exception):
    """Levantado quando o modelo nao produz uma saida valida apos a retentativa."""


async def _trace_llm_io(
    schema: type, messages: list[dict[str, str]], model: str | None, *, output=None, error=None
) -> None:
    """Trilha de auditoria: o que foi enviado ao modelo e o que ele devolveu (mascarado)."""
    settings = get_settings()
    if not settings.audit_capture_llm_io:
        return
    await emit_trace(
        "llm_io",
        actor="system",
        actor_name=schema.__name__,
        status="error" if error else "ok",
        error_code=error,
        details={
            "schema": schema.__name__,
            "model": model or settings.openrouter_model,
            "messages": [{"role": m.get("role"), "content": m.get("content")} for m in messages],
            "output": output,
        },
        llm=True,
    )


async def get_structured_output(
    schema: type[SchemaT], messages: list[dict[str, str]], *, model: str | None = None
) -> SchemaT:
    chat_model = get_chat_model(model) if model else get_chat_model()
    structured_model = chat_model.with_structured_output(schema)

    for attempt in range(2):
        with collect_usage() as usages:
            try:
                result = await structured_model.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001
                await record_llm_usage(usages)
                await _trace_llm_io(schema, messages, model, error=type(exc).__name__)
                if isinstance(exc, openai.APIStatusError) and exc.status_code in (401, 402, 403):
                    # Chave invalida / sem saldo / sem permissao: repetir nao adianta e o erro
                    # deve chegar ao cliente com um codigo claro (llm_upstream_error_handler).
                    raise
                logger.warning("structured_output_call_failed", attempt=attempt, error=str(exc))
                if attempt == 1:
                    raise StructuredOutputError(str(exc)) from exc
                continue
        await record_llm_usage(usages)
        await _trace_llm_io(schema, messages, model, output=result)

        if isinstance(result, schema):
            return result
        try:
            return schema.model_validate(result)
        except ValidationError as exc:
            logger.warning("structured_output_invalid", attempt=attempt, error=str(exc))
            if attempt == 1:
                raise StructuredOutputError(str(exc)) from exc

    raise StructuredOutputError("saida estruturada invalida apos retentativa")
