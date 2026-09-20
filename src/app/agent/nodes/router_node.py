"""No de roteamento do grafo -- invoca o Router Agent e grava RouterDecision no estado.

Tambem valida que o agente selecionado de fato tem a capacidade classificada (FR-010) contra
o registro estatico de agentes, evitando encaminhamentos incorretos por decisao inconsistente.
"""

from __future__ import annotations

from app.agent import small_talk
from app.agent.registry import agent_can_handle
from app.agent.router.agent import RouterAgent
from app.agent.state import GraphState
from app.repository.mongodb.client import get_database
from app.repository.support_cases_repository import SupportCasesRepository
from app.schemas.router import Intent, RouterDecision

_router_agent = RouterAgent()


async def _active_support_case(state: GraphState):
    try:
        return await SupportCasesRepository(get_database()).get_active(state["session_id"])
    except Exception:  # noqa: BLE001 -- sem banco/caso o roteamento segue o caminho normal
        return None


async def router_node(state: GraphState) -> GraphState:
    state["node_name"] = "router"

    message = state["user_message"].message
    small_talk_kind = small_talk.classify_small_talk(message)
    active_case = await _active_support_case(state)
    if active_case is not None and small_talk_kind in (None, small_talk.THANKS):
        # Estamos no meio de um atendimento (perguntamos algo ou aguardamos o "deu certo?"): a
        # resposta do cliente continua o caso, sem depender da classificacao da LLM.
        state["routing_decision"] = RouterDecision(
            intent=Intent.CUSTOMER_SUPPORT,
            target_agent="customer_support_agent",
            confidence=1.0,
            requires_clarification=False,
            reason_code="ACTIVE_SUPPORT_CASE",
        )
        return state
    if small_talk_kind is not None:
        # Saudacao/agradecimento/despedida: sem modelo, sem base, sem sites.
        state["routing_decision"] = RouterDecision(
            intent=Intent.GREETING,
            confidence=1.0,
            requires_clarification=False,
            reason_code=small_talk_kind,
        )
        return state

    decision = await _router_agent.decide(message)
    decision = _validate_agent_capability(decision)

    state["routing_decision"] = decision
    return state


def _validate_agent_capability(decision: RouterDecision) -> RouterDecision:
    """FR-010: nunca despachar para um agente que nao esta habilitado para a intencao decidida."""
    if decision.intent in (
        Intent.CLARIFICATION_REQUIRED,
        Intent.UNKNOWN,
        Intent.SECURITY_BLOCKED,
        Intent.MULTI_AGENT,
        Intent.GREETING,
    ):
        return decision

    if decision.target_agent and not agent_can_handle(decision.target_agent, decision.intent):
        return decision.model_copy(
            update={
                "intent": Intent.CLARIFICATION_REQUIRED,
                "requires_clarification": True,
                "reason_code": "AGENT_CAPABILITY_MISMATCH",
            }
        )
    return decision
