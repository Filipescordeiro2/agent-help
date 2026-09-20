"""FeedbackRepository -- colecao `feedback` (spec FR-039 a FR-042)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class ProblemClassification(StrEnum):
    ROUTING_FAILURE = "ROUTING_FAILURE"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    KNOWLEDGE_GAP = "KNOWLEDGE_GAP"
    TOOL_FAILURE = "TOOL_FAILURE"
    INCORRECT_RESPONSE = "INCORRECT_RESPONSE"
    INCOMPLETE_RESPONSE = "INCOMPLETE_RESPONSE"
    SECURITY_ISSUE = "SECURITY_ISSUE"
    RESOLVED = "RESOLVED"
    ESCALATION_NEEDED = "ESCALATION_NEEDED"


class Feedback(BaseModel):
    feedback_id: str
    user_id: str
    session_id: str
    message_id: str
    execution_id: str
    agent_invoked: str | None = None
    intent_identified: str | None = None
    response_given: dict | None = None
    documents_retrieved: list[dict] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    rating: float | None = None
    comment: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt_version: str | None = None
    agent_version: str | None = None
    problem_classification: ProblemClassification
    # "manual" (POST /feedback) ou "auto_grounding" (nota de grounding baixa, ver
    # app/services/auto_feedback.py).
    source: str = "manual"


class FeedbackRepository(BaseRepository[Feedback]):
    collection_name = "feedback"
    model = Feedback
    id_field = "feedback_id"

    async def list_by_execution(self, execution_id: str) -> list[Feedback]:
        return await self.list(filters={"execution_id": execution_id}, limit=100)
