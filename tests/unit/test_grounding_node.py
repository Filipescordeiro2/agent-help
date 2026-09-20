"""T034: no de grounding -- casos conhecidos, fronteira do limiar, status procedurais, falhas."""

from __future__ import annotations

import asyncio

import pytest
from prometheus_client import generate_latest

import app.agent.nodes.grounding_node as grounding_module
from app.agent.nodes.grounding_node import PROCEDURAL_STATUSES, grounding_node
from app.agent.state import new_state
from app.config.settings import get_settings
from app.llm.structured_output import StructuredOutputError
from app.observability import otel
from app.schemas.agent_response import AgentResponse, SourceRef, Status
from app.schemas.common import ResponseMetadata
from app.schemas.grounding import GroundingEvaluation
from app.schemas.user_message import UserMessageInput


def evaluation(score: int, reasoning: str = "ok") -> GroundingEvaluation:
    return GroundingEvaluation(
        score=score,
        reasoning=reasoning,
        adheres_to_question=score >= 3,
        uses_context_correctly=score >= 3,
        no_unsupported_claims=score >= 3,
        is_complete=score >= 3,
    )


def make_response(
    status: Status = Status.OK, agent: str = "knowledge_agent", message: str = "resposta original"
) -> AgentResponse:
    return AgentResponse(
        status=status,
        agent=agent,
        message=message,
        metadata=ResponseMetadata(execution_id="exec-1", confidence=0.9),
    )


def make_state(response: AgentResponse | None, context: list[str] | None = None) -> dict:
    state = new_state(
        session_id="s1",
        execution_id="exec-1",
        request_id="r1",
        user_message=UserMessageInput(message="Qual a taxa?", user_id="u1"),
    )
    state["final_response"] = response
    state["grounding_context"] = list(context or [])
    return state


@pytest.fixture(autouse=True)
def _telemetry() -> None:
    otel.init_telemetry()


async def test_good_response_is_delivered_with_score_and_reasoning(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(5, "totalmente aderente")
    state = make_state(make_response(), ["trecho recuperado"])

    result = await grounding_node(state)

    final = result["final_response"]
    assert final.status == Status.OK and final.message == "resposta original"
    assert final.metadata.grounding_score == 5
    assert final.metadata.grounding_reasoning == "totalmente aderente"
    assert result["grounding_retry_requested"] is False


async def test_hallucinated_response_is_not_delivered_and_triggers_one_retry(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(0, "alucinada")
    state = make_state(make_response(), ["trecho"])

    result = await grounding_node(state)

    assert result["grounding_retry_requested"] is True
    assert result["final_response"] is None
    assert result["grounding_feedback"] == "alucinada"
    assert result["grounding_attempts"] == 1


async def test_incomplete_response_without_context_becomes_insufficient_context(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(2, "incompleta")
    result = await grounding_node(make_state(make_response(), []))

    final = result["final_response"]
    assert final.status == Status.INSUFFICIENT_CONTEXT
    assert "resposta original" not in final.message
    assert final.metadata.grounding_score == 2
    assert final.metadata.grounding_reasoning == "incompleta"
    assert final.metadata.error_code == "GROUNDING_BELOW_THRESHOLD"
    assert result["grounding_retry_requested"] is False


@pytest.mark.parametrize(("score", "delivered"), [(3, True), (4, True), (2, False)])
async def test_threshold_boundary_only_strictly_below_blocks(
    fake_llm, monkeypatch: pytest.MonkeyPatch, score: int, delivered: bool
) -> None:
    monkeypatch.setattr(get_settings(), "grounding_min_score", 3)
    fake_llm[GroundingEvaluation] = evaluation(score)
    result = await grounding_node(make_state(make_response(), ["c"]))
    if delivered:
        assert result["final_response"].status == Status.OK
    else:
        assert result["final_response"] is None and result["grounding_retry_requested"]


async def test_downgraded_response_keeps_score_and_reasoning(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(1, "faltou fonte")
    state = make_state(make_response(), ["trecho"])
    state["grounding_attempts"] = 1  # retentativa ja consumida

    result = await grounding_node(state)

    final = result["final_response"]
    assert final.status == Status.ESCALATION_REQUIRED
    assert final.metadata.grounding_score == 1
    assert final.metadata.grounding_reasoning == "faltou fonte"
    assert final.message != "resposta original"


@pytest.mark.parametrize("status", sorted(PROCEDURAL_STATUSES, key=lambda s: s.value))
async def test_procedural_status_passes_without_llm_and_with_null_score(
    fake_llm, status: Status
) -> None:
    calls: list[int] = []

    def factory() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(5)

    fake_llm[GroundingEvaluation] = factory
    agent = f"proc_agent_{status.value.lower()}"
    result = await grounding_node(make_state(make_response(status, agent=agent)))

    final = result["final_response"]
    assert calls == []
    assert final.status == status and final.message == "resposta original"
    assert final.metadata.grounding_score is None
    assert status.value in final.metadata.grounding_reasoning
    assert "conteudo substantivo" in final.metadata.grounding_reasoning
    assert f'agent="{agent}"' not in generate_latest().decode()


async def test_status_outside_procedural_list_is_evaluated_by_default(
    fake_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def factory() -> GroundingEvaluation:
        calls.append(1)
        return evaluation(4)

    fake_llm[GroundingEvaluation] = factory
    monkeypatch.setattr(
        grounding_module,
        "PROCEDURAL_STATUSES",
        PROCEDURAL_STATUSES - {Status.ESCALATION_REQUIRED},
    )
    result = await grounding_node(make_state(make_response(Status.ESCALATION_REQUIRED)))

    assert calls == [1]
    assert result["final_response"].metadata.grounding_score == 4


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("llm down"), StructuredOutputError("saida invalida"), TimeoutError()],
)
async def test_evaluator_failure_forces_escalation_never_delivers_original(
    fake_llm, failure: Exception
) -> None:
    def factory() -> GroundingEvaluation:
        raise failure

    fake_llm[GroundingEvaluation] = factory
    result = await grounding_node(make_state(make_response(), ["c"]))

    final = result["final_response"]
    assert final.status == Status.ESCALATION_REQUIRED
    assert final.metadata.error_code == "GROUNDING_UNAVAILABLE"
    assert "resposta original" not in final.message
    assert final.metadata.grounding_score is None


async def test_evaluator_timeout_forces_escalation(
    fake_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "node_timeout_seconds", 0.05)

    async def slow(_schema, _messages, **_kwargs):
        await asyncio.sleep(1)
        return evaluation(5)

    monkeypatch.setattr(grounding_module, "get_structured_output", slow)
    result = await grounding_node(make_state(make_response(), ["c"]))

    assert result["final_response"].status == Status.ESCALATION_REQUIRED
    assert result["final_response"].metadata.error_code == "GROUNDING_UNAVAILABLE"


async def test_grounding_model_is_passed_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    async def capture(_schema, _messages, **kwargs):
        seen.append(kwargs)
        return evaluation(5)

    monkeypatch.setattr(grounding_module, "get_structured_output", capture)
    settings = get_settings()

    monkeypatch.setattr(settings, "openrouter_model_grounding", None)
    await grounding_node(make_state(make_response(), ["c"]))
    monkeypatch.setattr(settings, "openrouter_model_grounding", "cheap/grader")
    await grounding_node(make_state(make_response(), ["c"]))

    assert seen == [{}, {"model": "cheap/grader"}]


async def test_evaluated_sources_count_as_context_for_retry(fake_llm) -> None:
    fake_llm[GroundingEvaluation] = evaluation(1)
    response = make_response().model_copy(
        update={"sources": [SourceRef(document_id="d", score=0.9)]}
    )
    response.metadata.grounded_in_sources = True
    result = await grounding_node(make_state(response, []))
    assert result["grounding_retry_requested"] is True


async def test_no_final_response_is_a_noop(fake_llm) -> None:
    result = await grounding_node(make_state(None))
    assert result["final_response"] is None and result["grounding_retry_requested"] is False
