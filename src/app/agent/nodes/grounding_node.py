"""No de validacao de aterramento (grounding) -- Constitution Principio X, spec FR-020 a FR-026.

Inserido no StateGraph entre os agentes de dominio (dispatch / compose_response) e
`validate_response`. Politica (research.md #4):

- Respostas com status PROCEDURAL (nao tentam responder a pergunta) atravessam o no sem chamada
  ao LLM, com `grounding_score = None` e uma justificativa fixa.
- Respostas substantivas sao avaliadas por LLM (score 0-5). `score < grounding_min_score` nunca
  e entregue: ha UMA retentativa se existia contexto (reexecuta o agente de origem com a
  justificativa como dado adicional); senao ESCALATION_REQUIRED (havia contexto) ou
  INSUFFICIENT_CONTEXT (sem contexto). A resposta rebaixada mantem o score/justificativa da
  avaliacao que causou o rebaixamento.
- Sequencias multiagente: cada resposta constituinte nao procedural e avaliada individualmente,
  mesmo que o status composto seja procedural; o score composto e o menor.
- Falha/timeout do avaliador: ESCALATION_REQUIRED -- nunca entrega resposta nao validada.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from app.agent.nodes.compose_response_node import _STATUS_SEVERITY
from app.agent.state import GraphState
from app.config.settings import get_settings
from app.llm.structured_output import get_structured_output
from app.observability.audit import safe_emit_audit_event
from app.observability.metrics import get_instruments
from app.observability.trace import prepare_details
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent
from app.security.policies import wrap_untrusted_content
from app.services.auto_feedback import maybe_record_low_grounding_feedback

logger = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "grounding" / "v1.md"

PROCEDURAL_STATUSES = frozenset(
    {
        Status.INSUFFICIENT_CONTEXT,
        Status.CLARIFICATION_REQUIRED,
        Status.ESCALATION_REQUIRED,
        Status.SECURITY_BLOCKED,
        Status.ERROR,
    }
)

_INSUFFICIENT_MESSAGE = "Nao encontrei informacoes suficientes para responder com seguranca."
_ESCALATION_MESSAGE = (
    "Nao foi possivel validar esta resposta com seguranca. Vou encaminhar sua solicitacao "
    "para um atendente humano."
)
_ERROR_MESSAGE = "Nao foi possivel processar sua solicitacao no momento."
_FINAL_MESSAGES = {
    Status.INSUFFICIENT_CONTEXT: _INSUFFICIENT_MESSAGE,
    Status.ESCALATION_REQUIRED: _ESCALATION_MESSAGE,
    Status.ERROR: _ERROR_MESSAGE,
}


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _procedural_reasoning(status: Status) -> str:
    return (
        f"Nao avaliado: a resposta tem status {status.value} (procedural) e nao contem "
        "conteudo substantivo para avaliar."
    )


async def _evaluate(question: str, answer: str, context: list[str]) -> GroundingEvaluation:
    settings = get_settings()
    context_block = "\n\n".join(context) if context else "(nenhum contexto recuperado)"
    user_content = (
        "Pergunta do usuario:\n"
        + wrap_untrusted_content(question)
        + "\n\nResposta a avaliar:\n"
        + wrap_untrusted_content(answer)
        + "\n\nContexto recuperado:\n"
        + wrap_untrusted_content(context_block)
    )
    messages = [
        {"role": "system", "content": _load_prompt()},
        {"role": "user", "content": user_content},
    ]
    if settings.openrouter_model_grounding:
        return await get_structured_output(
            GroundingEvaluation, messages, model=settings.openrouter_model_grounding
        )
    return await get_structured_output(GroundingEvaluation, messages)


async def _record_evaluation(agent: str, evaluation: GroundingEvaluation) -> None:
    settings = get_settings()
    try:
        get_instruments().grounding_score.record(evaluation.score, {"agent": agent})
    except Exception:  # noqa: BLE001 -- metrica nunca derruba o fluxo
        logger.warning("metric_emit_failed")
    await safe_emit_audit_event(
        actor="system",
        actor_name="grounding",
        event_type="grounding_evaluated",
        status="ok" if evaluation.score >= settings.grounding_min_score else "below_threshold",
        node_name="grounding",
        safe_metadata={"score": evaluation.score},
        details=prepare_details(
            {
                "agent_evaluated": agent,
                "score": evaluation.score,
                "min_score": settings.grounding_min_score,
                "reasoning": evaluation.reasoning,
                "adheres_to_question": evaluation.adheres_to_question,
                "uses_context_correctly": evaluation.uses_context_correctly,
                "no_unsupported_claims": evaluation.no_unsupported_claims,
                "is_complete": evaluation.is_complete,
            }
        ),
    )


def _with_grounding(response: AgentResponse, score: int | None, reasoning: str) -> AgentResponse:
    metadata = response.metadata.model_copy(
        update={"grounding_score": score, "grounding_reasoning": reasoning}
    )
    return response.model_copy(update={"metadata": metadata})


def _downgraded(
    response: AgentResponse,
    status: Status,
    score: int | None,
    reasoning: str,
    error_code: str,
) -> AgentResponse:
    """Resposta final fixa (nunca o texto reprovado); mantem score/justificativa da avaliacao."""
    return AgentResponse(
        status=status,
        agent=response.agent,
        message=_FINAL_MESSAGES.get(status, _ESCALATION_MESSAGE),
        sources=[],
        metadata=ResponseMetadata(
            execution_id=response.metadata.execution_id,
            confidence=0.0,
            grounding_score=score,
            grounding_reasoning=reasoning,
            error_code=error_code,
        ),
    )


def _apply_below_threshold_policy(
    state: GraphState, response: AgentResponse, score: int, reasoning: str
) -> GraphState:
    has_context = bool(state.get("grounding_context")) or bool(response.sources)
    if has_context and state.get("grounding_attempts", 0) == 0:
        state["grounding_retry_requested"] = True
        state["grounding_feedback"] = reasoning
        state["grounding_attempts"] = state.get("grounding_attempts", 0) + 1
        state["agent_responses"] = []
        state["final_response"] = None
        state["grounding_context"] = []
        return state

    policy_status = Status.ESCALATION_REQUIRED if has_context else Status.INSUFFICIENT_CONTEXT
    final_status = max((policy_status, response.status), key=lambda s: _STATUS_SEVERITY[s])
    state["final_response"] = _downgraded(
        response, final_status, score, reasoning, "GROUNDING_BELOW_THRESHOLD"
    )
    return state


async def _handle_single(state: GraphState, response: AgentResponse) -> GraphState:
    settings = get_settings()
    if response.status in PROCEDURAL_STATUSES:
        state["final_response"] = _with_grounding(
            response, None, _procedural_reasoning(response.status)
        )
        return state
    if response.agent in state.get("grounding_exempt_agents", []):
        state["final_response"] = _with_grounding(
            response, None, "Nao avaliado: mensagem do fluxo de suporte (sem conteudo factual)."
        )
        return state

    question = state["user_message"].message
    evaluation = await asyncio.wait_for(
        _evaluate(question, response.message, state.get("grounding_context", [])),
        timeout=settings.node_timeout_seconds,
    )
    await _record_evaluation(response.agent, evaluation)
    await maybe_record_low_grounding_feedback(state, response, evaluation)
    if evaluation.score >= settings.grounding_min_score:
        state["final_response"] = _with_grounding(response, evaluation.score, evaluation.reasoning)
        return state
    return _apply_below_threshold_policy(state, response, evaluation.score, evaluation.reasoning)


async def _handle_multi(state: GraphState, response: AgentResponse) -> GraphState:
    settings = get_settings()
    exempt = state.get("grounding_exempt_agents", [])

    def _is_procedural(r: AgentResponse) -> bool:
        return r.status in PROCEDURAL_STATUSES or r.agent in exempt

    constituents = [r for r in state.get("agent_responses", []) if not _is_procedural(r)]
    procedural = [r for r in state.get("agent_responses", []) if _is_procedural(r)]
    if not constituents:
        reasoning = _procedural_reasoning(response.status)
        state["final_response"] = _with_grounding(response, None, reasoning)
        return state

    question = state["user_message"].message
    context = state.get("grounding_context", [])
    evaluations = await asyncio.wait_for(
        asyncio.gather(*[_evaluate(question, r.message, context) for r in constituents]),
        timeout=settings.node_timeout_seconds,
    )
    for constituent, evaluation in zip(constituents, evaluations, strict=True):
        await _record_evaluation(constituent.agent, evaluation)
        await maybe_record_low_grounding_feedback(state, constituent, evaluation)

    min_score = min(e.score for e in evaluations)
    pairs = list(zip(constituents, evaluations, strict=True))
    reasoning = "; ".join(f"{r.agent}: {e.score} - {e.reasoning}" for r, e in pairs)
    if procedural:
        reasoning += "; nao avaliadas (procedurais): " + ", ".join(
            f"{r.agent} ({r.status.value})" for r in procedural
        )

    if min_score >= settings.grounding_min_score:
        state["final_response"] = _with_grounding(response, min_score, reasoning)
        return state
    return _apply_below_threshold_policy(state, response, min_score, reasoning)


async def grounding_node(state: GraphState) -> GraphState:
    state["node_name"] = "grounding"
    state["grounding_retry_requested"] = False

    response = state.get("final_response")
    if response is None:
        return state

    try:
        if response.agent == "multi_agent":
            return await _handle_multi(state, response)
        return await _handle_single(state, response)
    except Exception as exc:  # noqa: BLE001 -- falha do avaliador nunca entrega resposta nao validada
        logger.warning("grounding_unavailable", reason=type(exc).__name__)
        state["final_response"] = _downgraded(
            response,
            Status.ESCALATION_REQUIRED,
            None,
            "Avaliacao de aterramento indisponivel; resposta nao validada nao foi entregue.",
            "GROUNDING_UNAVAILABLE",
        )
        return state


def route_after_grounding(state: GraphState) -> str:
    if not state.get("grounding_retry_requested"):
        return "done"
    decision = state.get("routing_decision")
    if decision is not None and decision.intent == Intent.MULTI_AGENT:
        return "retry_multi"
    return "retry_dispatch"
