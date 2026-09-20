"""Trilha de auditoria detalhada: registra O QUE aconteceu (e por que) em cada passo do atendimento.

Complementa `audit.py` (metadados seguros para metricas) com `details`: conteudo mascarado/truncado
(`redaction.py`) -- entrada do usuario, retorno de cada agente/no/ferramenta, decisao do Router,
consulta a base e a fontes web, prompts e saidas dos modelos, resposta final. E a fonte da API
`GET /api/v1/audit/sessions/{session_id}`.

- Ligado/desligado por `AUDIT_CAPTURE_CONTENT` (desligado => so metadados, sem conteudo).
- Nunca propaga falha: auditoria jamais derruba o atendimento.
"""

from __future__ import annotations

from typing import Any, Literal

from app.config.settings import get_settings
from app.observability.audit import safe_emit_audit_event
from app.observability.redaction import sanitize
from app.schemas.agent_response import AgentResponse


def prepare_details(details: dict[str, Any] | None, *, llm: bool = False) -> dict[str, Any] | None:
    """`details` mascarado e truncado, ou `None` se a captura de conteudo estiver desligada."""
    settings = get_settings()
    if not details or not settings.audit_capture_content:
        return None
    limit = settings.audit_max_llm_chars if llm else settings.audit_max_content_chars
    return sanitize(details, max_chars=limit)


async def emit_trace(
    event_type: str,
    *,
    actor: Literal["agent", "tool", "system"],
    actor_name: str,
    status: str = "ok",
    node_name: str | None = None,
    duration_ms: float | None = None,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
    llm: bool = False,
) -> None:
    await safe_emit_audit_event(
        actor=actor,
        actor_name=actor_name,
        event_type=event_type,
        status=status,
        node_name=node_name,
        duration_ms=duration_ms,
        error_code=error_code,
        details=prepare_details(details, llm=llm),
    )


def summarize_response(response: AgentResponse | None) -> dict[str, Any]:
    """Resumo de uma `AgentResponse` para a trilha (status, mensagem, fontes, grounding)."""
    if response is None:
        return {}
    return {
        "status": response.status.value,
        "agent": response.agent,
        "message": response.message,
        "sources": [
            {
                "document_id": s.document_id,
                "chunk_id": s.chunk_id,
                "score": s.score,
                "url": s.url,
            }
            for s in response.sources
        ],
        "confidence": response.metadata.confidence,
        "grounded_in_sources": response.metadata.grounded_in_sources,
        "grounding_score": response.metadata.grounding_score,
        "grounding_reasoning": response.metadata.grounding_reasoning,
        "error_code": response.metadata.error_code,
        "ticket_id": response.metadata.ticket_id,
    }


def summarize_node(name: str, state: Any) -> dict[str, Any]:
    """O que cada no do grafo decidiu, a partir do estado que ele devolveu."""
    if not isinstance(state, dict):
        return {}
    final = state.get("final_response")
    if name == "security":
        return {"blocked": state.get("security_blocked"), "reason": state.get("security_reason")}
    if name == "router":
        decision = state.get("routing_decision")
        return decision.model_dump(mode="json") if decision is not None else {}
    if name in ("dispatch", "multi_agent_dispatch"):
        return {
            "responses": [
                {"agent": r.agent, "status": r.status.value}
                for r in state.get("agent_responses", [])
            ]
        }
    if name == "grounding":
        return {
            "attempts": state.get("grounding_attempts"),
            "retry_requested": state.get("grounding_retry_requested"),
            "feedback_for_retry": state.get("grounding_feedback"),
            "final": summarize_response(final),
        }
    if name in ("compose_response", "clarification", "security_blocked", "validate_response"):
        return {"final": summarize_response(final)}
    return {}
