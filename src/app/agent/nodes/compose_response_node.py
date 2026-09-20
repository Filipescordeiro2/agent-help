"""Composicao de resposta combinada para sequencias multiagente (spec FR-021, US3 Acceptance
Scenario 2) -- um unico AgentResponse coerente cobrindo todas as partes do pedido.

Politica de composicao (conservadora, dada a ausencia de especificacao mais detalhada):
- status: o mais severo entre as respostas compostas (SECURITY_BLOCKED > ERROR >
  ESCALATION_REQUIRED > CLARIFICATION_REQUIRED > INSUFFICIENT_CONTEXT > OK) -- uma sequencia
  multiagente nunca deve "esconder" um problema atras de uma parte bem-sucedida.
- message: cada parte e apresentada separadamente, atribuida ao agente que a produziu.
- sources: uniao de todas as fontes.
- confidence: a menor confianca entre as partes (conservador).
- grounded_in_sources: False se qualquer parte com fontes nao estiver fundamentada.
"""

from __future__ import annotations

from app.agent.state import GraphState
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata

_STATUS_SEVERITY = {
    Status.SECURITY_BLOCKED: 5,
    Status.ERROR: 4,
    Status.ESCALATION_REQUIRED: 3,
    Status.CLARIFICATION_REQUIRED: 2,
    Status.INSUFFICIENT_CONTEXT: 1,
    Status.OK: 0,
}


def compose_responses(execution_id: str, responses: list[AgentResponse]) -> AgentResponse:
    if not responses:
        return AgentResponse(
            status=Status.ERROR,
            agent="orchestrator",
            message="Nao foi possivel processar sua solicitacao no momento.",
            metadata=ResponseMetadata(
                execution_id=execution_id, confidence=0.0, error_code="NO_RESPONSE_PRODUCED"
            ),
        )

    if len(responses) == 1:
        return responses[0]

    combined_status = max(responses, key=lambda r: _STATUS_SEVERITY[r.status]).status
    combined_message = "\n\n".join(f"[{r.agent}] {r.message}" for r in responses)
    combined_sources = [source for r in responses for source in r.sources]
    combined_confidence = min(r.metadata.confidence for r in responses)

    grounded_values = [
        r.metadata.grounded_in_sources
        for r in responses
        if r.metadata.grounded_in_sources is not None
    ]
    grounded_in_sources = all(grounded_values) if grounded_values else None

    return AgentResponse(
        status=combined_status,
        agent="multi_agent",
        message=combined_message,
        sources=combined_sources,
        metadata=ResponseMetadata(
            execution_id=execution_id,
            confidence=combined_confidence,
            grounded_in_sources=grounded_in_sources,
        ),
    )


async def compose_response_node(state: GraphState) -> GraphState:
    state["node_name"] = "compose_response"
    state["final_response"] = compose_responses(
        state["execution_id"], state.get("agent_responses", [])
    )
    return state
