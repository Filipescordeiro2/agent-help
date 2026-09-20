"""POST/GET /api/v1/feedback, GET /api/v1/feedback/analytics -- spec FR-039 a FR-042."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.repository.feedback_repository import Feedback
from app.routers.deps import get_db
from app.routers.identity import IdentityHeaders, get_identity_headers
from app.services import feedback as service

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackInput(BaseModel):
    # Os quatro ids podem vir nos cabecalhos X-User-Id / X-Session-Id / X-Message-Id /
    # X-Execution-Id (mesmo efeito do corpo; se vierem nos dois, precisam ser iguais).
    user_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-User-Id.",
        json_schema_extra={"deprecated": True},
    )
    session_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Session-Id.",
        json_schema_extra={"deprecated": True},
    )
    message_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Message-Id.",
        json_schema_extra={"deprecated": True},
    )
    execution_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Execution-Id.",
        json_schema_extra={"deprecated": True},
    )
    problem_classification: str
    agent_invoked: str | None = None
    intent_identified: str | None = None
    response_given: dict | None = None
    documents_retrieved: list[dict] = []
    tools_used: list[str] = []
    rating: float | None = None
    comment: str | None = None
    prompt_version: str | None = None
    agent_version: str | None = None


@router.post("", response_model=Feedback)
async def create_feedback(
    payload: FeedbackInput,
    ids: IdentityHeaders = Depends(get_identity_headers),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Feedback:
    data = payload.model_dump()
    for field in ("user_id", "session_id", "message_id", "execution_id"):
        data[field] = ids.resolve(field, data[field])
    return await service.create_feedback(db, **data)


@router.get("", response_model=list[Feedback])
async def list_feedback(
    agent_invoked: str | None = None,
    problem_classification: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[Feedback]:
    return await service.list_feedback(
        db, agent_invoked=agent_invoked, problem_classification=problem_classification
    )


@router.get("/analytics")
async def feedback_analytics(db: AsyncIOMotorDatabase = Depends(get_db)) -> dict:
    return await service.get_analytics(db)
