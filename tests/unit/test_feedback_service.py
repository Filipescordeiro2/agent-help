"""T120: teste unitario do servico de feedback -- validacao de campos obrigatorios, enum
`problem_classification` (spec FR-039 a FR-041)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError

import app.services.feedback as feedback_service
from app.repository.feedback_repository import (
    Feedback,
    FeedbackRepository,
    ProblemClassification,
)


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


async def test_create_feedback_persists_all_required_fields(db) -> None:
    feedback = await feedback_service.create_feedback(
        db,
        user_id="client_xpto",
        session_id="s1",
        message_id="m1",
        execution_id="e1",
        problem_classification="INCORRECT_RESPONSE",
        agent_invoked="knowledge_agent",
        intent_identified="KNOWLEDGE",
        rating=2.0,
        comment="resposta incompleta",
    )

    assert feedback.problem_classification == ProblemClassification.INCORRECT_RESPONSE
    stored = await FeedbackRepository(db).get(feedback.feedback_id)
    assert stored is not None
    assert stored.agent_invoked == "knowledge_agent"


async def test_create_feedback_rejects_invalid_problem_classification(db) -> None:
    with pytest.raises(ValueError):
        await feedback_service.create_feedback(
            db,
            user_id="u",
            session_id="s",
            message_id="m",
            execution_id="e",
            problem_classification="NOT_A_VALID_CLASSIFICATION",
        )


def test_feedback_requires_problem_classification() -> None:
    with pytest.raises(ValidationError):
        Feedback(feedback_id="f1", user_id="u", session_id="s", message_id="m", execution_id="e")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "classification",
    [
        "ROUTING_FAILURE",
        "RETRIEVAL_FAILURE",
        "KNOWLEDGE_GAP",
        "TOOL_FAILURE",
        "INCORRECT_RESPONSE",
        "INCOMPLETE_RESPONSE",
        "SECURITY_ISSUE",
        "RESOLVED",
        "ESCALATION_NEEDED",
    ],
)
async def test_all_minimum_classifications_are_accepted(db, classification: str) -> None:
    feedback = await feedback_service.create_feedback(
        db,
        user_id="u",
        session_id="s",
        message_id="m",
        execution_id="e",
        problem_classification=classification,
    )
    assert feedback.problem_classification.value == classification


async def test_list_feedback_filters_by_agent(db) -> None:
    await feedback_service.create_feedback(
        db,
        user_id="u",
        session_id="s1",
        message_id="m1",
        execution_id="e1",
        problem_classification="RESOLVED",
        agent_invoked="knowledge_agent",
    )
    await feedback_service.create_feedback(
        db,
        user_id="u",
        session_id="s2",
        message_id="m2",
        execution_id="e2",
        problem_classification="RESOLVED",
        agent_invoked="customer_support_agent",
    )

    results = await feedback_service.list_feedback(db, agent_invoked="knowledge_agent")

    assert len(results) == 1
    assert results[0].agent_invoked == "knowledge_agent"


async def test_analytics_aggregates_by_classification_and_agent(db) -> None:
    await feedback_service.create_feedback(
        db,
        user_id="u",
        session_id="s1",
        message_id="m1",
        execution_id="e1",
        problem_classification="RESOLVED",
        agent_invoked="knowledge_agent",
        rating=5.0,
    )
    await feedback_service.create_feedback(
        db,
        user_id="u",
        session_id="s2",
        message_id="m2",
        execution_id="e2",
        problem_classification="ROUTING_FAILURE",
        agent_invoked="knowledge_agent",
        rating=1.0,
    )

    analytics = await feedback_service.get_analytics(db)

    assert analytics["total"] == 2
    assert analytics["by_problem_classification"]["RESOLVED"] == 1
    assert analytics["by_problem_classification"]["ROUTING_FAILURE"] == 1
    assert analytics["by_agent"]["knowledge_agent"] == 2
    assert analytics["average_rating"] == 3.0
