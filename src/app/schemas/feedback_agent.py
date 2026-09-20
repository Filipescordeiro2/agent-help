"""Schemas de resposta/requisicao da API do Feedback Agent (contracts/schemas.md)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.repository.feedback_proposals_repository import FeedbackProposal


class ApprovalSummary(BaseModel):
    total_reviewed: int = Field(description="APPROVED + APPLIED + REJECTED")
    approved_count: int = Field(description="inclui APPLIED (toda proposta aplicada foi aprovada)")
    rejected_count: int
    approval_rate: float = Field(description="approved_count / total_reviewed; 0.0 sem revisoes")


class ProposalListResponse(BaseModel):
    proposals: list[FeedbackProposal]
    approval_summary: ApprovalSummary


class RejectRequest(BaseModel):
    rejection_reason: str = Field(min_length=1)
