"""Logging estruturado (structlog) com allowlist de campos -- nunca segredos/dados sensiveis.

Contextvars carregam request_id/session_id/message_id/execution_id por toda a pilha de
chamadas assincronas, sem precisar passa-los explicitamente por parametro (research.md #9,
Constitution Principio IX).
"""

from __future__ import annotations

import contextvars
import logging

import structlog

from app.config.settings import get_settings

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "session_id", default=None
)
_message_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "message_id", default=None
)
_execution_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "execution_id", default=None
)

# Allowlist explicita -- nunca uma denylist (mais seguro para dados sensiveis).
_LOGGABLE_KEYS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "request_id",
        "session_id",
        "message_id",
        "execution_id",
        "agent",
        "node",
        "tool",
        "status",
        "duration_ms",
        "error_code",
        "prompt_version",
        "agent_version",
        "iteration_count",
        "reason",
        "collection",
        "grounding_score",
        "run_id",
        "proposal_id",
        "trigger_type",
        "action_type",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "trace_id",
        "span_id",
    }
)


def bind_context(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
    execution_id: str | None = None,
) -> None:
    if request_id is not None:
        _request_id_var.set(request_id)
    if session_id is not None:
        _session_id_var.set(session_id)
    if message_id is not None:
        _message_id_var.set(message_id)
    if execution_id is not None:
        _execution_id_var.set(execution_id)


def get_context_ids() -> dict[str, str | None]:
    """Ids de correlacao do contexto corrente (usados por auditoria, spans e logs)."""
    return {
        "request_id": _request_id_var.get(),
        "session_id": _session_id_var.get(),
        "message_id": _message_id_var.get(),
        "execution_id": _execution_id_var.get(),
    }


def _add_trace_ids(_logger: object, _method_name: str, event_dict: dict) -> dict:
    """Injeta trace_id/span_id do span OTel corrente (correlacao logs <-> traces)."""
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def _add_context_ids(_logger: object, _method_name: str, event_dict: dict) -> dict:
    event_dict.setdefault("request_id", _request_id_var.get())
    event_dict.setdefault("session_id", _session_id_var.get())
    event_dict.setdefault("message_id", _message_id_var.get())
    event_dict.setdefault("execution_id", _execution_id_var.get())
    return event_dict


def _allowlist_filter(_logger: object, _method_name: str, event_dict: dict) -> dict:
    return {k: v for k, v in event_dict.items() if k in _LOGGABLE_KEYS and v is not None}


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")

    structlog.configure(
        processors=[
            _add_context_ids,
            _add_trace_ids,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _allowlist_filter,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
