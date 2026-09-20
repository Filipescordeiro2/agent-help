"""T046: testes unitarios dos schemas do contrato universal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata, SourceRef
from app.schemas.router import Intent, RouterDecision
from app.schemas.user_message import UserMessageInput


def test_agent_response_accepts_valid_ok_payload() -> None:
    response = AgentResponse(
        status=Status.OK,
        agent="knowledge_agent",
        message="ola",
        metadata=ResponseMetadata(execution_id="exec_1", confidence=0.9),
    )
    assert response.status == Status.OK
    assert response.sources == []


def test_agent_response_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        AgentResponse(
            status="NOT_A_STATUS",  # type: ignore[arg-type]
            agent="a",
            message="m",
            metadata=ResponseMetadata(execution_id="e", confidence=0.5),
        )


def test_agent_response_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        AgentResponse(
            status=Status.OK,
            agent="a",
            message="",
            metadata=ResponseMetadata(execution_id="e", confidence=0.5),
        )


def test_agent_response_requires_grounded_in_sources_when_sources_present() -> None:
    with pytest.raises(ValidationError):
        AgentResponse(
            status=Status.OK,
            agent="knowledge_agent",
            message="resposta",
            sources=[SourceRef(document_id="doc_1", score=0.9)],
            metadata=ResponseMetadata(execution_id="e", confidence=0.9),
        )


def test_agent_response_accepts_grounded_in_sources_when_provided() -> None:
    response = AgentResponse(
        status=Status.OK,
        agent="knowledge_agent",
        message="resposta",
        sources=[SourceRef(document_id="doc_1", score=0.9)],
        metadata=ResponseMetadata(execution_id="e", confidence=0.9, grounded_in_sources=True),
    )
    assert response.metadata.grounded_in_sources is True


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_source_ref_score_must_be_within_0_and_1(score: float) -> None:
    with pytest.raises(ValidationError):
        SourceRef(document_id="doc_1", score=score)


def test_user_message_input_requires_non_blank_message() -> None:
    with pytest.raises(ValidationError):
        UserMessageInput(message="   ", user_id="client_xpto")


def test_user_message_input_trims_message() -> None:
    payload = UserMessageInput(message="  ola  ", user_id="client_xpto")
    assert payload.message == "ola"


def test_router_decision_requires_confidence_and_reason_code() -> None:
    decision = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    assert decision.intent == Intent.KNOWLEDGE


@pytest.mark.parametrize("score", [-1, 6])
def test_response_metadata_rejects_out_of_range_grounding_score(score: int) -> None:
    with pytest.raises(ValidationError):
        ResponseMetadata(execution_id="e", confidence=0.5, grounding_score=score)


@pytest.mark.parametrize("score", [0, 5, None])
def test_response_metadata_accepts_valid_grounding_score(score: int | None) -> None:
    metadata = ResponseMetadata(
        execution_id="e", confidence=0.5, grounding_score=score, grounding_reasoning="ok"
    )
    assert metadata.grounding_score == score


@pytest.mark.parametrize("score", [-1, 6])
def test_grounding_evaluation_rejects_out_of_range_score(score: int) -> None:
    from app.schemas.grounding import GroundingEvaluation

    with pytest.raises(ValidationError):
        GroundingEvaluation(
            score=score,
            reasoning="r",
            adheres_to_question=True,
            uses_context_correctly=True,
            no_unsupported_claims=True,
            is_complete=True,
        )


def test_grounding_evaluation_requires_all_criteria_booleans() -> None:
    from app.schemas.grounding import GroundingEvaluation

    with pytest.raises(ValidationError):
        GroundingEvaluation(score=3, reasoning="r")  # type: ignore[call-arg]
    ok = GroundingEvaluation(
        score=0,
        reasoning="r",
        adheres_to_question=False,
        uses_context_correctly=False,
        no_unsupported_claims=False,
        is_complete=False,
    )
    assert ok.score == 0


def test_serialize_state_handles_new_grounding_keys() -> None:
    from app.agent.state import new_state, serialize_state

    state = new_state(
        session_id="s",
        execution_id="e",
        request_id="r",
        user_message=UserMessageInput(message="oi", user_id="u"),
    )
    state["grounding_context"] = ["trecho"]
    state["grounding_feedback"] = "faltou fonte"
    serialized = serialize_state(state)
    assert serialized["grounding_attempts"] == 0
    assert serialized["grounding_retry_requested"] is False
    assert serialized["grounding_context"] == ["trecho"]
    assert serialized["grounding_feedback"] == "faltou fonte"
