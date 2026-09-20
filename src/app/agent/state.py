"""Estado tipado do StateGraph (Constitution Principio I -- nunca estado implicito)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from app.schemas.agent_response import AgentResponse
from app.schemas.router import RouterDecision
from app.schemas.user_message import UserMessageInput


class GraphState(TypedDict, total=False):
    session_id: str
    execution_id: str
    request_id: str
    message_id: str
    user_message: UserMessageInput
    routing_decision: RouterDecision | None
    agent_responses: list[AgentResponse]
    final_response: AgentResponse | None
    iteration_count: int
    started_at: datetime
    security_blocked: bool
    security_reason: str | None
    node_name: str
    grounding_attempts: int
    grounding_feedback: str | None
    grounding_retry_requested: bool
    grounding_context: list[str]
    # O Knowledge Agent ja consultou as fontes web nesta execucao (nao repetir na retentativa).
    web_consulted: bool
    # Fluxo de suporte: novo estado do caso (gravado por `support_commit`) e agentes cuja resposta
    # (pergunta, agradecimento, chamado) nao passa pelo grounding.
    support_outcome: Any
    grounding_exempt_agents: list[str]
    answer_mode: str


def new_state(
    *,
    session_id: str,
    execution_id: str,
    request_id: str,
    user_message: UserMessageInput,
    message_id: str | None = None,
) -> GraphState:
    return GraphState(
        session_id=session_id,
        execution_id=execution_id,
        request_id=request_id,
        message_id=message_id or str(uuid.uuid4()),
        user_message=user_message,
        routing_decision=None,
        agent_responses=[],
        final_response=None,
        iteration_count=0,
        started_at=datetime.now(UTC),
        security_blocked=False,
        security_reason=None,
        node_name="start",
        grounding_attempts=0,
        grounding_feedback=None,
        grounding_retry_requested=False,
        grounding_context=[],
    )


def serialize_state(state: GraphState) -> dict[str, Any]:
    """Serializacao do estado para persistencia em checkpoint (JSON-safe)."""
    out: dict[str, Any] = dict(state)
    if out.get("user_message") is not None:
        out["user_message"] = out["user_message"].model_dump(mode="json")
    if out.get("routing_decision") is not None:
        out["routing_decision"] = out["routing_decision"].model_dump(mode="json")
    out["agent_responses"] = [r.model_dump(mode="json") for r in out.get("agent_responses", [])]
    if out.get("final_response") is not None:
        out["final_response"] = out["final_response"].model_dump(mode="json")
    if isinstance(out.get("started_at"), datetime):
        out["started_at"] = out["started_at"].isoformat()
    return out
