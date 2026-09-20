"""Instrumentacao de nos do grafo, agentes e ferramentas (Constitution Principio XI, spec FR-036).

Cada execucao emite, de forma padronizada: span (traces), metrica (duracao/erros), log
estruturado (allowlist, sem dados sensiveis) e evento de auditoria (fonte dos endpoints JSON de
metricas). Nenhuma falha de observabilidade propaga para o caminho de atendimento.
"""

from __future__ import annotations

import contextvars
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from app.agent.state import GraphState
from app.observability.audit import safe_emit_audit_event
from app.observability.logging import bind_context
from app.observability.metrics import get_instruments
from app.observability.trace import prepare_details, summarize_node, summarize_response
from app.schemas.agent_response import AgentResponse, Status

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("getnet.multiagent")

current_agent_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_agent", default=None
)
current_node_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_node", default=None
)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _safe_metric(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 -- metrica nunca derruba o fluxo
        logger.warning("metric_emit_failed")


def _bind_state_ids(state: Any) -> None:
    if isinstance(state, dict):
        bind_context(
            request_id=state.get("request_id"),
            session_id=state.get("session_id"),
            message_id=state.get("message_id"),
            execution_id=state.get("execution_id"),
        )


def instrument_node(
    name: str, fn: Callable[[GraphState], Awaitable[GraphState]]
) -> Callable[[GraphState], Awaitable[GraphState]]:
    """Envolve um no do grafo com span, log e evento de auditoria (`event_type="node_call"`)."""

    @functools.wraps(fn)
    async def wrapper(state: GraphState) -> GraphState:
        _bind_state_ids(state)
        node_token = current_node_var.set(name)
        start = time.perf_counter()
        status = "ok"
        error_code: str | None = None
        result: Any = None
        with _tracer.start_as_current_span(f"node.{name}") as span:
            try:
                result = await fn(state)
                return result
            except Exception as exc:
                status = "error"
                error_code = type(exc).__name__
                span.set_status(StatusCode.ERROR)
                span.set_attribute("error.type", error_code)
                raise
            finally:
                duration_ms = _elapsed_ms(start)
                logger.info("node_completed", node=name, status=status, duration_ms=duration_ms)
                await safe_emit_audit_event(
                    actor="system",
                    actor_name=name,
                    event_type="node_call",
                    status=status,
                    node_name=name,
                    duration_ms=duration_ms,
                    error_code=error_code,
                    details=prepare_details(summarize_node(name, result)),
                )
                current_node_var.reset(node_token)

    return wrapper


async def run_instrumented_agent(handler: Any, state: GraphState) -> AgentResponse:
    """Executa `handler.handle(state)` com span, duracao por agente, erro e auditoria."""
    _bind_state_ids(state)
    agent_name = handler.name
    agent_token = current_agent_var.set(agent_name)
    start = time.perf_counter()
    status = "ok"
    error_code: str | None = None
    response: AgentResponse | None = None
    with _tracer.start_as_current_span(f"agent.{agent_name}") as span:
        try:
            response = await handler.handle(state)
            if response.status == Status.ERROR:
                status = "error"
                error_code = response.metadata.error_code or "AGENT_ERROR"
            return response
        except Exception as exc:
            status = "error"
            error_code = type(exc).__name__
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", error_code)
            raise
        finally:
            duration_s = time.perf_counter() - start
            instruments = get_instruments()
            _safe_metric(
                lambda: instruments.agent_call_duration.record(
                    duration_s, {"agent": agent_name, "status": status}
                )
            )
            if status == "error":
                _safe_metric(
                    lambda: instruments.agent_errors.add(
                        1, {"agent": agent_name, "error_code": error_code or "AGENT_ERROR"}
                    )
                )
            logger.info(
                "agent_completed", agent=agent_name, status=status, duration_ms=duration_s * 1000
            )
            await safe_emit_audit_event(
                actor="agent",
                actor_name=agent_name,
                event_type="agent_call",
                status=status,
                duration_ms=duration_s * 1000,
                error_code=error_code,
                safe_metadata={"status": response.status.value} if response else None,
                details=prepare_details(summarize_response(response)),
            )
            current_agent_var.reset(agent_token)


async def instrument_tool_call(
    tool_name: str, call: Callable[[], Awaitable[Any]], input_data: Any = None
) -> Any:
    """Executa uma chamada de ferramenta com span, erro e evento `event_type="tool_call"`
    (com a entrada e o retorno da ferramenta, mascarados, em `details`)."""
    start = time.perf_counter()
    status = "ok"
    error_code: str | None = None
    output: Any = None
    with _tracer.start_as_current_span(f"tool.{tool_name}") as span:
        try:
            output = await call()
            return output
        except Exception as exc:
            status = "error"
            error_code = type(exc).__name__
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", error_code)
            _safe_metric(
                lambda: get_instruments().tool_errors.add(
                    1, {"tool": tool_name, "error_code": error_code or "TOOL_ERROR"}
                )
            )
            raise
        finally:
            duration_ms = _elapsed_ms(start)
            logger.info("tool_completed", tool=tool_name, status=status, duration_ms=duration_ms)
            await safe_emit_audit_event(
                actor="tool",
                actor_name=tool_name,
                event_type="tool_call",
                status=status,
                duration_ms=duration_ms,
                error_code=error_code,
                details=prepare_details({"input": input_data, "output": output}),
            )
