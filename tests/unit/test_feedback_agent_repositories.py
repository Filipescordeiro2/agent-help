"""T031: repositorios de propostas e execucoes do Feedback Agent."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.repository.feedback_agent_runs_repository import (
    FeedbackAgentRun,
    FeedbackAgentRunsRepository,
    RunStatus,
    TriggerType,
)
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)


def _proposal(
    proposal_id: str = "p1",
    *,
    feedback_ids: list[str] | None = None,
    action_type: ActionType = ActionType.CREATE_PLAYBOOK,
    status: ProposalStatus = ProposalStatus.PENDING_HUMAN_REVIEW,
    target_topic: str | None = "maquininha nao liga",
    **extra,
) -> FeedbackProposal:
    return FeedbackProposal(
        proposal_id=proposal_id,
        feedback_ids=feedback_ids if feedback_ids is not None else ["f1"],
        status=status,
        action_type=action_type,
        draft_content={"name": "x"},
        justificativa="j",
        confidence=0.8,
        target_topic=target_topic,
        **extra,
    )


def test_validators() -> None:
    with pytest.raises(ValidationError):
        _proposal(feedback_ids=[])
    with pytest.raises(ValidationError):
        _proposal(status=ProposalStatus.REJECTED)
    with pytest.raises(ValidationError):
        FeedbackProposal(
            proposal_id="p",
            feedback_ids=["f"],
            action_type=ActionType.CREATE_SKILL,
            justificativa="j",
            confidence=1.5,
        )
    assert _proposal(status=ProposalStatus.REJECTED, rejection_reason="nao serve").rejection_reason
    assert _proposal().status == ProposalStatus.PENDING_HUMAN_REVIEW


async def test_claim_for_review_is_atomic(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal())

    results = await asyncio.gather(
        repo.claim_for_review("p1", ProposalStatus.APPROVED, "rev-a"),
        repo.claim_for_review("p1", ProposalStatus.APPROVED, "rev-b"),
    )

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].status == ProposalStatus.APPROVED and winners[0].reviewed_by
    assert winners[0].reviewed_at is not None


async def test_claim_for_review_rejection_records_reason(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal())
    rejected = await repo.claim_for_review(
        "p1", ProposalStatus.REJECTED, "rev", rejection_reason="duplicada"
    )
    assert rejected is not None and rejected.rejection_reason == "duplicada"


async def test_find_active_for_feedback_ids_ignores_rejected(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal("p1", feedback_ids=["f1"]))
    await repo.insert(
        _proposal("p2", feedback_ids=["f2"], status=ProposalStatus.REJECTED, rejection_reason="x")
    )
    await repo.insert(_proposal("p3", feedback_ids=["f3"], status=ProposalStatus.APPROVED))

    found = await repo.find_active_for_feedback_ids(["f1", "f2", "f3"])
    assert {p.proposal_id for p in found} == {"p1", "p3"}


async def test_find_active_by_equivalence(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal("pend", target_topic="topico a"))
    await repo.insert(_proposal("appr", target_topic="topico b", status=ProposalStatus.APPROVED))
    await repo.insert(
        _proposal(
            "rej",
            target_topic="topico c",
            status=ProposalStatus.REJECTED,
            rejection_reason="x",
        )
    )
    await repo.insert(
        _proposal(
            "applied",
            target_topic="topico d",
            status=ProposalStatus.APPLIED,
        )
    )

    assert (
        await repo.find_active_by_equivalence(ActionType.CREATE_PLAYBOOK, "topico a")
    ).proposal_id == "pend"
    assert (
        await repo.find_active_by_equivalence(ActionType.CREATE_PLAYBOOK, "topico b")
    ).proposal_id == "appr"
    assert await repo.find_active_by_equivalence(ActionType.CREATE_PLAYBOOK, "topico c") is None
    assert await repo.find_active_by_equivalence(ActionType.CREATE_PLAYBOOK, "topico d") is None
    assert await repo.find_active_by_equivalence(ActionType.CREATE_SKILL, "topico a") is None
    assert await repo.find_active_by_equivalence(ActionType.CREATE_PLAYBOOK, None) is None


async def test_add_feedback_ids_unions_without_duplicates_and_only_when_pending(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal("pend", feedback_ids=["f1"]))
    await repo.insert(_proposal("appr", feedback_ids=["f9"], status=ProposalStatus.APPROVED))

    assert await repo.add_feedback_ids("pend", ["f1", "f2"]) is True
    assert sorted((await repo.get("pend")).feedback_ids) == ["f1", "f2"]
    assert await repo.add_feedback_ids("appr", ["f10"]) is False
    assert (await repo.get("appr")).feedback_ids == ["f9"]


async def test_mark_applied_only_for_approved_prompt_adjustment(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(
        _proposal("ok", action_type=ActionType.PROMPT_ADJUSTMENT, status=ProposalStatus.APPROVED)
    )
    await repo.insert(_proposal("pending", action_type=ActionType.PROMPT_ADJUSTMENT))
    await repo.insert(
        _proposal("skill", action_type=ActionType.CREATE_SKILL, status=ProposalStatus.APPROVED)
    )
    await repo.insert(
        _proposal(
            "rej",
            action_type=ActionType.PROMPT_ADJUSTMENT,
            status=ProposalStatus.REJECTED,
            rejection_reason="x",
        )
    )

    applied = await repo.mark_applied("ok", "rev-1")
    assert applied is not None and applied.status == ProposalStatus.APPLIED
    assert applied.applied_confirmed_by == "rev-1" and applied.applied_at is not None
    assert await repo.mark_applied("ok", "rev-2") is None
    for pid in ("pending", "skill", "rej"):
        assert await repo.mark_applied(pid, "rev") is None


async def test_list_by_status_filters(test_db) -> None:
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(_proposal("a"))
    await repo.insert(_proposal("b", status=ProposalStatus.APPROVED))
    pending = await repo.list_by_status(ProposalStatus.PENDING_HUMAN_REVIEW)
    assert [p.proposal_id for p in pending] == ["a"]
    assert len(await repo.list_by_status()) == 2


async def test_runs_processed_ids_ignore_failed_runs(test_db) -> None:
    repo = FeedbackAgentRunsRepository(test_db)
    await repo.insert(
        FeedbackAgentRun(
            run_id="r1",
            trigger_type=TriggerType.MANUAL,
            status=RunStatus.COMPLETED,
            processed_feedback_ids=["f1", "f2"],
        )
    )
    await repo.insert(
        FeedbackAgentRun(
            run_id="r2",
            trigger_type=TriggerType.SCHEDULED,
            status=RunStatus.FAILED,
            processed_feedback_ids=["f3"],
        )
    )
    assert await repo.all_processed_feedback_ids() == {"f1", "f2"}


async def test_run_defaults_and_blocked_fields(test_db) -> None:
    repo = FeedbackAgentRunsRepository(test_db)
    run = await repo.insert(FeedbackAgentRun(run_id="r", trigger_type=TriggerType.MANUAL))
    assert run.blocked_feedback_ids == [] and run.blocked_reasons == {}
    await repo.update("r", {"blocked_feedback_ids": ["f1"], "blocked_reasons": {"f1": "X"}})
    stored = await repo.get("r")
    assert stored.blocked_reasons == {"f1": "X"}
    recent = await repo.list_recent(RunStatus.RUNNING)
    assert [r.run_id for r in recent] == ["r"]
