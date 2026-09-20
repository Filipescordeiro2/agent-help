"""T035: politica de retry/escalonamento do grounding, incluindo sequencias multiagente."""

from __future__ import annotations

import pytest

from app.agent.graph import AGENT_HANDLERS, IterationLimitExceeded, build_graph
from app.agent.nodes.compose_response_node import compose_responses
from app.agent.nodes.grounding_node import grounding_node, route_after_grounding
from app.agent.state import new_state
from app.config.settings import get_settings
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision
from app.schemas.user_message import UserMessageInput


def evaluation(score: int, reasoning: str = "r") -> GroundingEvaluation:
    return GroundingEvaluation(
        score=score,
        reasoning=reasoning,
        adheres_to_question=True,
        uses_context_correctly=True,
        no_unsupported_claims=True,
        is_complete=True,
    )


def response(agent: str, status: Status = Status.OK, message: str = "texto") -> AgentResponse:
    return AgentResponse(
        status=status,
        agent=agent,
        message=message,
        metadata=ResponseMetadata(execution_id="e", confidence=0.9),
    )


def base_state() -> dict:
    return new_state(
        session_id="s",
        execution_id="e",
        request_id="r",
        user_message=UserMessageInput(message="pergunta", user_id="u"),
    )


def single_state(text: str = "texto reprovado", context: list[str] | None = None) -> dict:
    state = base_state()
    state["final_response"] = response("knowledge_agent", message=text)
    state["agent_responses"] = [state["final_response"]]
    state["grounding_context"] = list(context or [])
    return state


async def test_low_score_with_context_requests_one_retry_and_clears_state(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(1, "sem fonte")
    state = single_state(context=["trecho A"])

    result = await grounding_node(state)

    assert result["grounding_retry_requested"] is True
    assert result["grounding_feedback"] == "sem fonte"
    assert result["grounding_attempts"] == 1
    assert result["agent_responses"] == []
    assert result["final_response"] is None
    assert result["grounding_context"] == []


async def test_second_low_score_escalates_and_hides_rejected_text(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(1)
    state = single_state("texto reprovado", ["trecho"])
    state["grounding_attempts"] = 1

    result = await grounding_node(state)

    final = result["final_response"]
    assert final.status == Status.ESCALATION_REQUIRED
    assert "texto reprovado" not in final.message
    assert result["grounding_retry_requested"] is False


async def test_low_score_without_context_is_insufficient_context_without_retry(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(0)
    result = await grounding_node(single_state("texto reprovado", []))

    assert result["final_response"].status == Status.INSUFFICIENT_CONTEXT
    assert result["grounding_retry_requested"] is False
    assert "texto reprovado" not in result["final_response"].message


def test_route_after_grounding_picks_retry_target() -> None:
    state = base_state()
    assert route_after_grounding(state) == "done"
    state["grounding_retry_requested"] = True
    state["routing_decision"] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    assert route_after_grounding(state) == "retry_dispatch"
    state["routing_decision"] = RouterDecision(
        intent=Intent.MULTI_AGENT,
        target_sequence=["knowledge_agent", "customer_support_agent"],
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    assert route_after_grounding(state) == "retry_multi"


async def test_retry_counts_against_max_graph_iterations(
    fake_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Stub:
        name = "knowledge_agent"

        async def handle(self, state) -> AgentResponse:
            state["grounding_context"] = [*state.get("grounding_context", []), "trecho"]
            return response("knowledge_agent")

    AGENT_HANDLERS["knowledge_agent"] = Stub()
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    fake_llm[GroundingEvaluation] = evaluation(0)
    monkeypatch.setattr(get_settings(), "max_graph_iterations", 1)

    with pytest.raises(IterationLimitExceeded):
        await build_graph().ainvoke(base_state())


# --- sequencias multiagente (spec FR-020 / US1 AC7) -------------------------------------------


def multi_state(*responses: AgentResponse, context: list[str] | None = None) -> dict:
    state = base_state()
    state["agent_responses"] = list(responses)
    state["final_response"] = compose_responses("e", list(responses))
    state["grounding_context"] = list(context or [])
    return state


async def test_multi_ok_plus_escalation_evaluates_only_the_substantive_part(fake_llm) -> None:
    calls: list[int] = []

    def factory() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(4, "boa")

    fake_llm[GroundingEvaluation] = factory
    ka = response("knowledge_agent", message="resposta do knowledge")
    csa = response("customer_support_agent", Status.ESCALATION_REQUIRED, "encaminhado")
    state = multi_state(ka, csa, context=["trecho"])
    assert state["final_response"].status == Status.ESCALATION_REQUIRED  # status composto

    result = await grounding_node(state)

    final = result["final_response"]
    assert calls == [1]  # so o Knowledge foi avaliado
    assert final.status == Status.ESCALATION_REQUIRED
    assert final.metadata.grounding_score == 4
    assert "knowledge_agent: 4 - boa" in final.metadata.grounding_reasoning
    assert "customer_support_agent (ESCALATION_REQUIRED)" in final.metadata.grounding_reasoning
    assert "resposta do knowledge" in final.message  # texto validado e entregue


async def test_multi_failed_constituent_never_delivers_rejected_text_and_retries(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(1, "reprovado")
    ka = response("knowledge_agent", message="texto reprovado do knowledge")
    csa = response("customer_support_agent", Status.ESCALATION_REQUIRED, "encaminhado")

    first = await grounding_node(multi_state(ka, csa, context=["trecho"]))
    assert first["grounding_retry_requested"] is True and first["agent_responses"] == []

    second_state = multi_state(ka, csa, context=["trecho"])
    second_state["grounding_attempts"] = 1
    second = await grounding_node(second_state)
    final = second["final_response"]
    assert final.status == Status.ESCALATION_REQUIRED
    assert "texto reprovado do knowledge" not in final.message
    assert final.metadata.grounding_score == 1


async def test_multi_composite_score_is_the_minimum_of_evaluated_parts(fake_llm) -> None:
    scores = iter([5, 3])
    fake_llm[GroundingEvaluation] = lambda: evaluation(next(scores))
    result = await grounding_node(
        multi_state(response("knowledge_agent"), response("customer_support_agent"), context=["c"])
    )
    assert result["final_response"].status == Status.OK
    assert result["final_response"].metadata.grounding_score == 3


async def test_multi_all_procedural_makes_no_llm_call_and_score_is_null(fake_llm) -> None:
    calls: list[int] = []

    def factory() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(5)

    fake_llm[GroundingEvaluation] = factory
    result = await grounding_node(
        multi_state(
            response("knowledge_agent", Status.INSUFFICIENT_CONTEXT),
            response("customer_support_agent", Status.ESCALATION_REQUIRED),
        )
    )
    final = result["final_response"]
    assert calls == []
    assert final.metadata.grounding_score is None
    assert "conteudo substantivo" in final.metadata.grounding_reasoning


async def test_multi_more_severe_composed_status_prevails_over_policy_outcome(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(0)
    ka = response("knowledge_agent", message="reprovado")
    csa = response("customer_support_agent", Status.ESCALATION_REQUIRED)
    result = await grounding_node(
        multi_state(ka, csa, context=[])
    )  # sem contexto: policy=INSUFFICIENT

    assert result["grounding_retry_requested"] is False
    assert result["final_response"].status == Status.ESCALATION_REQUIRED
