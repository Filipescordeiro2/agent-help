"""No de validacao de resposta -- valida todo AgentResponse contra o schema e o OutputScanner
antes de prosseguir (Constitution Principio IV: nenhuma resposta fora do contrato sai do grafo).
"""

from __future__ import annotations

import uuid

from app.agent.state import GraphState
from app.observability.trace import emit_trace
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.security.scanners import get_output_scanner


async def validate_response_node(state: GraphState) -> GraphState:
    state["node_name"] = "validate_response"

    candidate = state.get("final_response")
    if candidate is None and state.get("agent_responses"):
        candidate = state["agent_responses"][-1]

    if candidate is None:
        candidate = AgentResponse(
            status=Status.ERROR,
            agent="orchestrator",
            message="Nao foi possivel processar sua solicitacao no momento.",
            metadata=ResponseMetadata(
                execution_id=state["execution_id"],
                confidence=0.0,
                error_code="NO_RESPONSE_PRODUCED",
            ),
        )

    scan = get_output_scanner().scan(candidate.message)
    if not scan.is_safe:
        # Auditoria: o que o agente tinha respondido e por que a saida foi barrada.
        await emit_trace(
            "output_blocked",
            actor="system",
            actor_name="output_scanner",
            status="blocked",
            error_code=scan.reason_code or "OUTPUT_BLOCKED",
            details={
                "agent": candidate.agent,
                "reason": scan.reason_code,
                "blocked_message": candidate.message,
            },
        )
        candidate = AgentResponse(
            status=Status.ERROR,
            agent=candidate.agent,
            message="Nao foi possivel entregar esta resposta com seguranca.",
            metadata=ResponseMetadata(
                execution_id=state["execution_id"],
                confidence=0.0,
                error_code=scan.reason_code or "OUTPUT_BLOCKED",
            ),
        )

    state["final_response"] = candidate
    return state


def build_security_blocked_response(state: GraphState) -> AgentResponse:
    return AgentResponse(
        status=Status.SECURITY_BLOCKED,
        agent="security",
        message="Sua solicitacao nao pode ser processada por motivos de seguranca.",
        metadata=ResponseMetadata(
            execution_id=state.get("execution_id", str(uuid.uuid4())),
            confidence=1.0,
            error_code=state.get("security_reason") or "SECURITY_BLOCKED",
        ),
    )
