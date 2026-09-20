"""Montagem do StateGraph -- unico mecanismo de orquestracao (Constitution Principio I).

Nos explicitos, arestas explicitas (normais e condicionais), limite maximo de iteracoes e
timeout por no. O despacho para agentes de dominio (Knowledge, Customer Support) e feito por
um registro pluggable (`AGENT_HANDLERS`), populado pelas User Stories 1 e 2 -- a montagem do
grafo em si nao muda quando um novo agente e ligado, apenas o registro.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from langgraph.graph import END, StateGraph

from app.agent.contracts import Agent
from app.agent.nodes.clarification_node import clarification_node
from app.agent.nodes.compose_response_node import compose_response_node
from app.agent.nodes.grounding_node import grounding_node, route_after_grounding
from app.agent.nodes.router_node import router_node
from app.agent.nodes.security_node import security_node
from app.agent.nodes.support_commit_node import support_commit_node
from app.agent.nodes.validate_response_node import (
    build_security_blocked_response,
    validate_response_node,
)
from app.agent.nodes.welcome_node import welcome_node
from app.agent.state import GraphState
from app.config.settings import get_settings
from app.observability.instrumentation import instrument_node, run_instrumented_agent
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.router import Intent

logger = structlog.get_logger(__name__)

NodeFn = Callable[[GraphState], Awaitable[GraphState]]

# Registro pluggable de agentes de dominio, por nome de intencao. Populado por
# app/agent/nodes/knowledge_node.py (US1) e customer_support_node.py (US2).
AGENT_HANDLERS: dict[str, Agent] = {}

# Numero maximo de agentes em uma sequencia MULTI_AGENT antes de preferir escalar em vez de
# continuar encadeando (spec Edge Cases, US3).
MAX_MULTI_AGENT_SEQUENCE = 3


async def _run_agent_with_timeout(handler: Agent, state: GraphState) -> AgentResponse:
    """Roda o agente dentro do orcamento de tempo; estourar o prazo NAO derruba a requisicao:
    vira um encaminhamento ao atendimento (nunca uma resposta inventada nem um erro 500)."""
    settings = get_settings()
    try:
        return await asyncio.wait_for(
            run_instrumented_agent(handler, state), timeout=settings.agent_timeout_seconds
        )
    except TimeoutError:
        logger.warning("agent_timeout", agent=handler.name)
        return AgentResponse(
            status=Status.ESCALATION_REQUIRED,
            agent=handler.name,
            message=settings.support_contact_message,
            metadata=ResponseMetadata(
                execution_id=state["execution_id"], confidence=0.0, error_code="AGENT_TIMEOUT"
            ),
        )


class IterationLimitExceeded(Exception):
    """Levantado quando o grafo excede o limite maximo de iteracoes (loop de seguranca)."""


def _check_iteration_limit(state: GraphState) -> None:
    settings = get_settings()
    state["iteration_count"] = state.get("iteration_count", 0) + 1
    if state["iteration_count"] > settings.max_graph_iterations:
        raise IterationLimitExceeded(
            f"limite de {settings.max_graph_iterations} iteracoes excedido"
        )


async def dispatch_node(state: GraphState) -> GraphState:
    """Despacho de agente unico (intencoes KNOWLEDGE/CUSTOMER_SUPPORT)."""
    state["node_name"] = "dispatch"
    _check_iteration_limit(state)

    decision = state.get("routing_decision")
    if decision is None or decision.target_agent is None:
        return state

    handler = AGENT_HANDLERS.get(decision.target_agent)
    if handler is None:
        return state

    response = await _run_agent_with_timeout(handler, state)
    state["agent_responses"] = [*state.get("agent_responses", []), response]
    state["final_response"] = response
    return state


async def multi_agent_dispatch_node(state: GraphState) -> GraphState:
    """Despacho sequencial para intencao MULTI_AGENT (spec FR-021, US3)."""
    state["node_name"] = "multi_agent_dispatch"
    _check_iteration_limit(state)

    decision = state.get("routing_decision")
    target_sequence = decision.target_sequence if decision else []

    if len(target_sequence) > MAX_MULTI_AGENT_SEQUENCE:
        target_sequence = target_sequence[:MAX_MULTI_AGENT_SEQUENCE]

    responses = list(state.get("agent_responses", []))
    for agent_name in target_sequence:
        handler = AGENT_HANDLERS.get(agent_name)
        if handler is None:
            continue
        responses.append(await _run_agent_with_timeout(handler, state))

    state["agent_responses"] = responses
    return state


def _route_after_security(state: GraphState) -> str:
    return "blocked" if state.get("security_blocked") else "continue"


def _route_after_router(state: GraphState) -> str:
    decision = state.get("routing_decision")
    if decision is None:
        return "clarify"
    if decision.intent == Intent.GREETING:
        return "welcome"
    if decision.intent == Intent.MULTI_AGENT:
        return "multi_dispatch"
    if decision.intent in (
        Intent.CLARIFICATION_REQUIRED,
        Intent.UNKNOWN,
        Intent.SECURITY_BLOCKED,
    ):
        return "clarify"
    return "dispatch"


async def _security_blocked_node(state: GraphState) -> GraphState:
    state["final_response"] = build_security_blocked_response(state)
    return state


def build_graph(*, persist_fn: NodeFn | None = None):
    """Constroi o StateGraph completo. `persist_fn` e injetado para permitir testes com um
    banco de dados de teste (sem acoplar a montagem do grafo a uma instancia global de DB).

    Checkpoints sao persistidos explicitamente pelo `persist_fn` (via `MongoCheckpointer`,
    ver app/agent/checkpoints/mongo_checkpointer.py) em vez do protocolo nativo de
    checkpointer do LangGraph -- mantem o unico mecanismo de persistencia em MongoDB
    (Constitution Principio VI) sob controle explicito do nosso proprio repositorio."""

    graph = StateGraph(GraphState)

    graph.add_node("security", instrument_node("security", security_node))
    graph.add_node("security_blocked", instrument_node("security_blocked", _security_blocked_node))
    graph.add_node("router", instrument_node("router", router_node))
    graph.add_node("clarification", instrument_node("clarification", clarification_node))
    graph.add_node("welcome", instrument_node("welcome", welcome_node))
    graph.add_node("dispatch", instrument_node("dispatch", dispatch_node))
    graph.add_node(
        "multi_agent_dispatch", instrument_node("multi_agent_dispatch", multi_agent_dispatch_node)
    )
    graph.add_node("compose_response", instrument_node("compose_response", compose_response_node))
    graph.add_node("grounding", instrument_node("grounding", grounding_node))
    graph.add_node(
        "validate_response", instrument_node("validate_response", validate_response_node)
    )
    graph.add_node("support_commit", instrument_node("support_commit", support_commit_node))
    if persist_fn is not None:
        graph.add_node("persist", instrument_node("persist", persist_fn))

    graph.set_entry_point("security")
    graph.add_conditional_edges(
        "security", _route_after_security, {"blocked": "security_blocked", "continue": "router"}
    )
    graph.add_edge("security_blocked", "validate_response")
    graph.add_conditional_edges(
        "router",
        _route_after_router,
        {
            "dispatch": "dispatch",
            "multi_dispatch": "multi_agent_dispatch",
            "clarify": "clarification",
            "welcome": "welcome",
        },
    )
    graph.add_edge("clarification", "validate_response")
    graph.add_edge("welcome", "validate_response")
    graph.add_edge("dispatch", "grounding")
    graph.add_edge("multi_agent_dispatch", "compose_response")
    graph.add_edge("compose_response", "grounding")
    graph.add_conditional_edges(
        "grounding",
        route_after_grounding,
        {
            "retry_dispatch": "dispatch",
            "retry_multi": "multi_agent_dispatch",
            "done": "validate_response",
        },
    )

    graph.add_edge("validate_response", "support_commit")
    if persist_fn is not None:
        graph.add_edge("support_commit", "persist")
        graph.add_edge("persist", END)
    else:
        graph.add_edge("support_commit", END)

    return graph.compile()
