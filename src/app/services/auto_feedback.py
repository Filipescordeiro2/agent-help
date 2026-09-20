"""Feedback automatico a partir do grounding.

Toda avaliacao de grounding com nota entre 0 e `auto_feedback_max_score` (padrao 3) vira um
Feedback (`source="auto_grounding"`) com a justificativa do proprio avaliador. O Feedback Agent
(retaguarda, com aprovacao humana) le esses feedbacks como os demais e propoe a melhoria:
nova skill, playbook ou documento. Nada e aplicado sozinho.

Regras:
- No maximo UM feedback automatico por (execucao, agente): se a retentativa tambem for baixa,
  fica o de menor nota (o mais informativo).
- Pergunta e resposta vao mascaradas/truncadas (mesma redacao da auditoria).
- Falha ao gravar NUNCA afeta a resposta ao cliente.
"""

from __future__ import annotations

import structlog

from app.agent.state import GraphState
from app.config.settings import get_settings
from app.observability.audit import safe_emit_audit_event
from app.observability.redaction import redact_text, truncate
from app.repository.feedback_repository import (
    Feedback,
    FeedbackRepository,
    ProblemClassification,
)
from app.repository.mongodb.client import get_database
from app.schemas.agent_response import AgentResponse
from app.schemas.grounding import GroundingEvaluation
from app.services.feedback import create_feedback

logger = structlog.get_logger(__name__)

AUTO_SOURCE = "auto_grounding"
_MAX_TEXT = 1500


def classify_low_grounding(evaluation: GroundingEvaluation, *, had_context: bool):
    """Tipo do problema a partir dos criterios do avaliador (o Feedback Agent reclassifica)."""
    if not had_context:
        return ProblemClassification.KNOWLEDGE_GAP
    if not evaluation.no_unsupported_claims:
        return ProblemClassification.INCORRECT_RESPONSE
    if not (evaluation.adheres_to_question and evaluation.uses_context_correctly):
        return ProblemClassification.RETRIEVAL_FAILURE
    return ProblemClassification.INCOMPLETE_RESPONSE


def _comment(question: str, evaluation: GroundingEvaluation, had_context: bool) -> str:
    return (
        f"[AUTO grounding {evaluation.score}/5] "
        f"Pergunta do cliente: {truncate(redact_text(question), 400)} | "
        f"Avaliador: {evaluation.reasoning} | "
        f"Criterios: aderente={evaluation.adheres_to_question}, "
        f"usou_o_contexto={evaluation.uses_context_correctly}, "
        f"sem_alegacoes_sem_suporte={evaluation.no_unsupported_claims}, "
        f"completa={evaluation.is_complete}, contexto_recuperado={had_context}. "
        "Sugerir como melhorar (conteudo faltante na base, skill ou playbook)."
    )


async def maybe_record_low_grounding_feedback(
    state: GraphState, response: AgentResponse, evaluation: GroundingEvaluation
) -> Feedback | None:
    settings = get_settings()
    if not settings.auto_feedback_enabled or evaluation.score > settings.auto_feedback_max_score:
        return None
    try:
        return await _record(state, response, evaluation)
    except Exception as exc:  # noqa: BLE001 -- feedback e sinal de melhoria, nunca derruba o fluxo
        logger.warning("auto_feedback_failed", reason=type(exc).__name__)
        return None


async def _record(
    state: GraphState, response: AgentResponse, evaluation: GroundingEvaluation
) -> Feedback | None:
    db = get_database()
    repo = FeedbackRepository(db)
    execution_id = state["execution_id"]
    had_context = bool(state.get("grounding_context")) or bool(response.sources)
    decision = state.get("routing_decision")

    existing = next(
        (
            f
            for f in await repo.list_by_execution(execution_id)
            if f.source == AUTO_SOURCE and f.agent_invoked == response.agent
        ),
        None,
    )
    if existing is not None and (existing.rating is None or existing.rating <= evaluation.score):
        return existing  # ja ha um automatico igual ou pior para esta execucao

    fields = {
        "problem_classification": classify_low_grounding(evaluation, had_context=had_context),
        "rating": float(evaluation.score),
        "comment": _comment(state["user_message"].message, evaluation, had_context),
        "response_given": {
            "status": response.status.value,
            "message": truncate(redact_text(response.message), _MAX_TEXT),
            "grounding_score": evaluation.score,
        },
        "documents_retrieved": [
            {"document_id": s.document_id, "score": s.score, "url": s.url} for s in response.sources
        ],
    }
    if existing is not None:
        feedback = await repo.update(existing.feedback_id, fields) or existing
    else:
        feedback = await create_feedback(
            db,
            user_id=state["user_message"].user_id,
            session_id=state["session_id"],
            message_id=state.get("message_id", execution_id),
            execution_id=execution_id,
            agent_invoked=response.agent,
            intent_identified=decision.reason_code if decision else None,
            problem_classification=fields["problem_classification"].value,
            rating=fields["rating"],
            comment=fields["comment"],
            response_given=fields["response_given"],
            documents_retrieved=fields["documents_retrieved"],
            source=AUTO_SOURCE,
        )
    await safe_emit_audit_event(
        actor="system",
        actor_name="auto_feedback",
        event_type="auto_feedback_created",
        status="ok",
        node_name="grounding",
        safe_metadata={"score": evaluation.score},
        details={
            "feedback_id": feedback.feedback_id,
            "score": evaluation.score,
            "problem_classification": feedback.problem_classification.value,
            "agent": response.agent,
        },
    )
    return feedback
