"""No `support_commit` -- grava o novo estado do caso de suporte DEPOIS da resposta validada.

O Customer Support Agent decide e deixa o resultado em `state["support_outcome"]` (ver
`app/agent/customer_support/case_flow.py`). So aqui, depois do grounding e da validacao de saida, o
caso e gravado -- assim uma retentativa do grounding nunca conta duas vezes uma pergunta de
esclarecimento ou uma tentativa de solucao, e uma resposta barrada nunca avanca o caso.

Fecha tambem a ultima folga do fluxo: se a resposta do fluxo de suporte foi rebaixada para
"encaminhar a um atendente" (grounding reprovou) e ainda nao ha chamado, abre-o agora -- sempre
pela trava de prontidao (`TicketNotReadyError`): chamado incompleto nunca abre.
"""

from __future__ import annotations

import structlog

from app.agent.customer_support.case_flow import ticket_message
from app.agent.state import GraphState
from app.observability.trace import emit_trace
from app.repository.mongodb.client import get_database
from app.repository.support_cases_repository import CaseStatus, SupportCasesRepository
from app.repository.tickets_repository import TicketReason
from app.schemas.agent_response import Status
from app.services.tickets import TicketNotReadyError, open_ticket

logger = structlog.get_logger(__name__)


async def support_commit_node(state: GraphState) -> GraphState:
    state["node_name"] = "support_commit"

    outcome = state.get("support_outcome")
    final = state.get("final_response")
    if outcome is None or final is None:
        return state
    if final.status in (Status.ERROR, Status.SECURITY_BLOCKED):
        return state  # resposta barrada: o caso nao avanca

    try:
        db = get_database()
        case = outcome.case
        action = outcome.action

        downgraded = (
            final.status == Status.ESCALATION_REQUIRED
            and final.metadata.ticket_id is None
            and case.ticket_id is None
            and action == "solution_proposed"
        )
        if downgraded:
            # A solucao passada nao passou no grounding e a resposta virou "encaminhar": abre o
            # chamado agora, com a analise, e informa o numero.
            try:
                ticket = await open_ticket(
                    db,
                    case=case,
                    reason=TicketReason.VALIDATION_FAILED,
                    execution_id=state["execution_id"],
                )
            except TicketNotReadyError:
                ticket = None  # incompleto: nao abre
            if ticket is not None:
                case.status = CaseStatus.TICKET_OPENED
                case.ticket_id = ticket.ticket_id
                case.closed_reason = TicketReason.VALIDATION_FAILED.value
                action = "ticket_opened"
                state["final_response"] = final.model_copy(
                    update={
                        "message": ticket_message(
                            TicketReason.VALIDATION_FAILED, ticket.ticket_id, case.problem_summary
                        ),
                        "metadata": final.metadata.model_copy(
                            update={"ticket_id": ticket.ticket_id}
                        ),
                    }
                )

        await SupportCasesRepository(db).save(case)
        await emit_trace(
            "support_case",
            actor="agent",
            actor_name="customer_support_agent",
            details={
                "action": action,
                "detail": outcome.detail,
                "case_id": case.case_id,
                "status": case.status.value,
                "problem_summary": case.problem_summary,
                "error_code": case.error_code,
                "clarification_count": case.clarification_count,
                "attempts": len(case.attempts),
                "ticket_id": case.ticket_id,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- o estado do caso nunca derruba a resposta ao cliente
        logger.warning("support_commit_failed", reason=type(exc).__name__)
    return state
