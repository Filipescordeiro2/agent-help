"""FeedbackProposalsRepository -- colecao `feedback_proposals` (data-model.md, spec FR-006).

Toda proposta nasce `PENDING_HUMAN_REVIEW` (Constitution Principio XII). As transicoes de
revisao sao atomicas (`find_one_and_update` com filtro de status) para que duas revisoes
concorrentes da mesma proposta nunca se apliquem duas vezes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.repository.base import BaseRepository


class ProposalStatus(StrEnum):
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class ActionType(StrEnum):
    CREATE_SKILL = "CREATE_SKILL"
    UPDATE_SKILL = "UPDATE_SKILL"
    CREATE_PLAYBOOK = "CREATE_PLAYBOOK"
    UPDATE_PLAYBOOK = "UPDATE_PLAYBOOK"
    CREATE_KNOWLEDGE = "CREATE_KNOWLEDGE"
    UPDATE_KNOWLEDGE = "UPDATE_KNOWLEDGE"
    PROMPT_ADJUSTMENT = "PROMPT_ADJUSTMENT"
    NO_ACTION = "NO_ACTION"


ACTIVE_STATUSES = (ProposalStatus.PENDING_HUMAN_REVIEW, ProposalStatus.APPROVED)


class FeedbackProposal(BaseModel):
    proposal_id: str
    feedback_ids: list[str]
    status: ProposalStatus = ProposalStatus.PENDING_HUMAN_REVIEW
    action_type: ActionType
    draft_content: dict[str, Any] = Field(default_factory=dict)
    justificativa: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    rejection_reason: str | None = None
    applied_entity_id: str | None = None
    applied_confirmed_by: str | None = None
    applied_at: datetime | None = None
    target_topic: str | None = None

    @field_validator("feedback_ids")
    @classmethod
    def _feedback_ids_not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("feedback_ids nao pode ser vazio")
        return value

    @model_validator(mode="after")
    def _rejection_reason_required_when_rejected(self) -> FeedbackProposal:
        if self.status == ProposalStatus.REJECTED and not self.rejection_reason:
            raise ValueError("rejection_reason e obrigatorio quando status == REJECTED")
        return self


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class FeedbackProposalsRepository(BaseRepository[FeedbackProposal]):
    collection_name = "feedback_proposals"
    model = FeedbackProposal
    id_field = "proposal_id"

    @staticmethod
    def _to_model(raw: dict[str, Any]) -> FeedbackProposal:
        raw.pop("_id", None)
        return FeedbackProposal.model_validate(raw)

    async def list_by_status(
        self, status: ProposalStatus | None = None, limit: int = 50, cursor: int = 0
    ) -> list[FeedbackProposal]:
        filters = {"status": status.value} if status else {}
        raw_docs = (
            await self._collection.find(filters)
            .sort("created_at", -1)
            .skip(cursor)
            .limit(limit)
            .to_list(length=limit)
        )
        return [self._to_model(doc) for doc in raw_docs]

    async def find_active_for_feedback_ids(self, ids: list[str]) -> list[FeedbackProposal]:
        raw_docs = await self._collection.find(
            {
                "feedback_ids": {"$in": ids},
                "status": {"$in": [s.value for s in ACTIVE_STATUSES]},
            }
        ).to_list(length=1000)
        return [self._to_model(doc) for doc in raw_docs]

    async def find_active_by_equivalence(
        self, action_type: ActionType, target_topic: str | None
    ) -> FeedbackProposal | None:
        """Proposta ativa com o mesmo par (action_type, target_topic) -- spec FR-018."""
        if not target_topic:
            return None
        raw_docs = await self._collection.find(
            {
                "action_type": action_type.value,
                "target_topic": target_topic,
                "status": {"$in": [s.value for s in ACTIVE_STATUSES]},
            }
        ).to_list(length=100)
        if not raw_docs:
            return None
        proposals = [self._to_model(doc) for doc in raw_docs]
        # Prefere a pendente (que ainda aceita novos feedbacks) sobre a ja aprovada.
        proposals.sort(key=lambda p: p.status != ProposalStatus.PENDING_HUMAN_REVIEW)
        return proposals[0]

    async def add_feedback_ids(self, proposal_id: str, ids: list[str]) -> bool:
        """Uniao atomica de ids; so vale enquanto a proposta esta pendente."""
        result = await self._collection.update_one(
            {"proposal_id": proposal_id, "status": ProposalStatus.PENDING_HUMAN_REVIEW.value},
            {"$addToSet": {"feedback_ids": {"$each": ids}}},
        )
        return result.matched_count > 0

    async def claim_for_review(
        self,
        proposal_id: str,
        new_status: ProposalStatus,
        reviewer_id: str,
        *,
        rejection_reason: str | None = None,
    ) -> FeedbackProposal | None:
        """Transicao atomica PENDING_HUMAN_REVIEW -> new_status; `None` se ja reivindicada."""
        updates: dict[str, Any] = {
            "status": new_status.value,
            "reviewed_at": _now_iso(),
            "reviewed_by": reviewer_id,
        }
        if rejection_reason is not None:
            updates["rejection_reason"] = rejection_reason
        raw = await self._collection.find_one_and_update(
            {"proposal_id": proposal_id, "status": ProposalStatus.PENDING_HUMAN_REVIEW.value},
            {"$set": updates},
            return_document=True,
        )
        return self._to_model(raw) if raw else None

    async def mark_applied(self, proposal_id: str, confirmed_by: str) -> FeedbackProposal | None:
        """APPROVED -> APPLIED, exclusivo de PROMPT_ADJUSTMENT (spec FR-040)."""
        raw = await self._collection.find_one_and_update(
            {
                "proposal_id": proposal_id,
                "status": ProposalStatus.APPROVED.value,
                "action_type": ActionType.PROMPT_ADJUSTMENT.value,
            },
            {
                "$set": {
                    "status": ProposalStatus.APPLIED.value,
                    "applied_confirmed_by": confirmed_by,
                    "applied_at": _now_iso(),
                }
            },
            return_document=True,
        )
        return self._to_model(raw) if raw else None
