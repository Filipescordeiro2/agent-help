"""T061: maquina de estados de FeedbackProposal (spec FR-015, FR-017, FR-040)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.feedback_agent import service
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from app.schemas.feedback_agent import RejectRequest


async def seed(
    db,
    pid: str,
    *,
    status: ProposalStatus = ProposalStatus.PENDING_HUMAN_REVIEW,
    action: ActionType = ActionType.PROMPT_ADJUSTMENT,
    draft: dict | None = None,
    **extra,
) -> None:
    await FeedbackProposalsRepository(db).insert(
        FeedbackProposal(
            proposal_id=pid,
            feedback_ids=["f"],
            status=status,
            action_type=action,
            draft_content=draft
            if draft is not None
            else {"target_prompt": "knowledge", "proposed_change": "x"},
            justificativa="j",
            confidence=0.8,
            **extra,
        )
    )


@pytest.mark.parametrize(
    "status",
    [ProposalStatus.APPROVED, ProposalStatus.APPLIED, ProposalStatus.REJECTED],
)
async def test_only_pending_proposals_can_be_approved_or_rejected(
    test_db, status: ProposalStatus
) -> None:
    extra = {"rejection_reason": "x"} if status == ProposalStatus.REJECTED else {}
    await seed(test_db, "p", status=status, **extra)

    with pytest.raises(service.InvalidProposalStatusError):
        await service.approve_proposal(test_db, "p", "rev")
    with pytest.raises(service.InvalidProposalStatusError):
        await service.reject_proposal(test_db, "p", "motivo", "rev")

    assert (await FeedbackProposalsRepository(test_db).get("p")).status == status


async def test_reject_requires_a_non_empty_reason() -> None:
    with pytest.raises(ValidationError):
        RejectRequest(rejection_reason="")
    with pytest.raises(ValidationError):
        FeedbackProposal(
            proposal_id="p",
            feedback_ids=["f"],
            status=ProposalStatus.REJECTED,
            action_type=ActionType.CREATE_SKILL,
            justificativa="j",
            confidence=0.5,
        )


async def test_review_fields_are_filled_on_review(test_db) -> None:
    await seed(test_db, "p")
    approved = await service.approve_proposal(test_db, "p", "revisor_1")
    assert approved.reviewed_by == "revisor_1" and approved.reviewed_at is not None


async def test_prompt_adjustment_goes_to_approved_never_applied_and_cannot_be_reviewed_again(
    test_db,
) -> None:
    await seed(test_db, "p")

    approved = await service.approve_proposal(test_db, "p", "rev")

    assert approved.status == ProposalStatus.APPROVED
    with pytest.raises(service.InvalidProposalStatusError):
        await service.approve_proposal(test_db, "p", "rev")
    with pytest.raises(service.InvalidProposalStatusError):
        await service.reject_proposal(test_db, "p", "motivo", "rev")


async def test_mark_applied_moves_an_approved_prompt_adjustment_to_applied(test_db) -> None:
    await seed(test_db, "p")
    await service.approve_proposal(test_db, "p", "rev")

    applied = await service.mark_proposal_applied(test_db, "p", "revisor_2")

    assert applied.status == ProposalStatus.APPLIED
    assert applied.applied_confirmed_by == "revisor_2" and applied.applied_at is not None
    assert applied.applied_entity_id is None
    with pytest.raises(service.InvalidProposalStatusError):  # so uma confirmacao
        await service.mark_proposal_applied(test_db, "p", "revisor_3")


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (ProposalStatus.PENDING_HUMAN_REVIEW, ActionType.PROMPT_ADJUSTMENT),
        (ProposalStatus.REJECTED, ActionType.PROMPT_ADJUSTMENT),
        (ProposalStatus.APPLIED, ActionType.PROMPT_ADJUSTMENT),
        (ProposalStatus.APPROVED, ActionType.CREATE_SKILL),
    ],
)
async def test_mark_applied_is_refused_for_any_other_status_or_action_type(
    test_db, status: ProposalStatus, action: ActionType
) -> None:
    extra = {"rejection_reason": "x"} if status == ProposalStatus.REJECTED else {}
    await seed(test_db, "p", status=status, action=action, draft={}, **extra)

    with pytest.raises(service.InvalidProposalStatusError):
        await service.mark_proposal_applied(test_db, "p", "rev")
    assert (await FeedbackProposalsRepository(test_db).get("p")).status == status


async def test_unknown_proposal_raises_not_found(test_db) -> None:
    for call in (
        service.approve_proposal(test_db, "nope", "rev"),
        service.reject_proposal(test_db, "nope", "m", "rev"),
        service.mark_proposal_applied(test_db, "nope", "rev"),
        service.get_proposal(test_db, "nope"),
    ):
        with pytest.raises(service.ProposalNotFoundError):
            await call


async def test_approval_summary_counts_applied_as_approved(test_db) -> None:
    empty = await service.approval_summary(test_db)
    assert empty.total_reviewed == 0 and empty.approval_rate == 0.0

    await seed(test_db, "a", status=ProposalStatus.APPROVED)
    await seed(test_db, "b", status=ProposalStatus.APPLIED, action=ActionType.CREATE_SKILL)
    await seed(test_db, "c", status=ProposalStatus.REJECTED, rejection_reason="x")
    await seed(test_db, "d", status=ProposalStatus.REJECTED, rejection_reason="x")
    await seed(test_db, "e")  # pendente nao conta

    summary = await service.approval_summary(test_db)
    assert (summary.total_reviewed, summary.approved_count, summary.rejected_count) == (4, 2, 2)
    assert summary.approval_rate == 0.5
