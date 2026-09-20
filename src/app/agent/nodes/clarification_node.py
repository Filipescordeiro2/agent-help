"""No de esclarecimento -- constroi a resposta final para os casos em que o Router decidiu
CLARIFICATION_REQUIRED ou UNKNOWN (spec FR-008, Acceptance Scenarios 1 e 3 da US3).

`UNKNOWN` tambem resolve para o status `CLARIFICATION_REQUIRED` do contrato universal, ja que
FR-003 define um conjunto fixo de status e `UNKNOWN` e apenas uma intencao interna do Router,
nunca um status exposto ao usuario.
"""

from __future__ import annotations

from app.agent.state import GraphState
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.router import Intent

_CLARIFICATION_MESSAGE = (
    "Nao consegui entender completamente sua solicitacao. Pode me dar mais detalhes sobre o "
    "que voce precisa (por exemplo, se e uma duvida geral ou um problema com um dispositivo)?"
)
_UNKNOWN_MESSAGE = (
    "Isso parece estar fora do que consigo ajudar diretamente. Pode reformular ou detalhar "
    "melhor sua solicitacao?"
)


async def clarification_node(state: GraphState) -> GraphState:
    state["node_name"] = "clarification"

    decision = state.get("routing_decision")
    intent = decision.intent if decision else Intent.CLARIFICATION_REQUIRED
    confidence = decision.confidence if decision else 0.0

    message = _UNKNOWN_MESSAGE if intent == Intent.UNKNOWN else _CLARIFICATION_MESSAGE

    state["final_response"] = AgentResponse(
        status=Status.CLARIFICATION_REQUIRED,
        agent="router_agent",
        message=message,
        sources=[],
        metadata=ResponseMetadata(execution_id=state["execution_id"], confidence=confidence),
    )
    return state
