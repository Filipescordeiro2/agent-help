"""Servico de feedback -- captura e analytics (spec FR-039 a FR-042).

GARANTIA ARQUITETURAL (FR-042): este modulo NUNCA importa `app/agent/prompts/`,
`app/services/skills.py`, `app/services/playbooks.py` ou `app/config/` para fins de escrita --
feedback e estritamente um sinal de leitura/analise. Qualquer mudanca de comportamento a partir
de um feedback exige uma alteracao de codigo revisada, versionada e testada manualmente, fora
deste modulo. Ver tests/security/test_feedback_no_auto_apply.py.
"""

from __future__ import annotations

import uuid
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.feedback_repository import (
    Feedback,
    FeedbackRepository,
    ProblemClassification,
)


async def create_feedback(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    message_id: str,
    execution_id: str,
    problem_classification: str,
    agent_invoked: str | None = None,
    intent_identified: str | None = None,
    response_given: dict | None = None,
    documents_retrieved: list[dict] | None = None,
    tools_used: list[str] | None = None,
    rating: float | None = None,
    comment: str | None = None,
    prompt_version: str | None = None,
    agent_version: str | None = None,
    source: str = "manual",
) -> Feedback:
    feedback = Feedback(
        feedback_id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        execution_id=execution_id,
        agent_invoked=agent_invoked,
        intent_identified=intent_identified,
        response_given=response_given,
        documents_retrieved=documents_retrieved or [],
        tools_used=tools_used or [],
        rating=rating,
        comment=comment,
        prompt_version=prompt_version,
        agent_version=agent_version,
        problem_classification=ProblemClassification(problem_classification),
        source=source,
    )
    return await FeedbackRepository(db).insert(feedback)


async def list_feedback(
    db: AsyncIOMotorDatabase,
    *,
    agent_invoked: str | None = None,
    problem_classification: str | None = None,
) -> list[Feedback]:
    filters: dict = {}
    if agent_invoked:
        filters["agent_invoked"] = agent_invoked
    if problem_classification:
        filters["problem_classification"] = problem_classification
    return await FeedbackRepository(db).list(filters=filters, limit=500)


async def get_analytics(db: AsyncIOMotorDatabase) -> dict:
    """Agregacoes somente leitura -- nunca escreve de volta em nenhuma outra colecao."""
    all_feedback = await FeedbackRepository(db).list(limit=10_000)

    by_classification = Counter(f.problem_classification.value for f in all_feedback)
    by_agent = Counter(f.agent_invoked for f in all_feedback if f.agent_invoked)
    ratings = [f.rating for f in all_feedback if f.rating is not None]

    return {
        "total": len(all_feedback),
        "by_problem_classification": dict(by_classification),
        "by_agent": dict(by_agent),
        "average_rating": (sum(ratings) / len(ratings)) if ratings else None,
    }
