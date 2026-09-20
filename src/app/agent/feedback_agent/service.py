"""Servico de revisao humana das propostas do Feedback Agent (spec FR-013 a FR-017, FR-040).

As rotas ficam finas e delegam para este modulo. Maquina de estados:
`PENDING_HUMAN_REVIEW -> APPROVED -> APPLIED`, `PENDING_HUMAN_REVIEW -> REJECTED` e, so para
`PROMPT_ADJUSTMENT`, `APPROVED -> APPLIED` via confirmacao humana explicita (`mark_applied`).
"""

from __future__ import annotations

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent.apply import (
    DraftContentInvalidError,
    ProposalTargetNotFoundError,
    apply_draft,
    ensure_target_exists,
    validate_draft,
)
from app.observability.audit import safe_emit_audit_event
from app.observability.metrics import get_instruments
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from app.schemas.feedback_agent import ApprovalSummary

logger = structlog.get_logger(__name__)

__all__ = [
    "DraftContentInvalidError",
    "InvalidProposalStatusError",
    "ProposalApplyFailedError",
    "ProposalNotFoundError",
    "ProposalTargetNotFoundError",
    "approval_summary",
    "approve_proposal",
    "get_proposal",
    "list_proposals",
    "mark_proposal_applied",
    "reject_proposal",
]


class ProposalNotFoundError(Exception):
    pass


class InvalidProposalStatusError(Exception):
    pass


class ProposalApplyFailedError(Exception):
    pass


async def list_proposals(
    db: AsyncIOMotorDatabase,
    status: ProposalStatus | None = None,
    limit: int = 50,
    cursor: int = 0,
) -> list[FeedbackProposal]:
    return await FeedbackProposalsRepository(db).list_by_status(status, limit, cursor)


async def get_proposal(db: AsyncIOMotorDatabase, proposal_id: str) -> FeedbackProposal:
    proposal = await FeedbackProposalsRepository(db).get(proposal_id)
    if proposal is None:
        raise ProposalNotFoundError(proposal_id)
    return proposal


async def approval_summary(db: AsyncIOMotorDatabase) -> ApprovalSummary:
    """Agregado sobre TODAS as propostas ja revisadas (nao so as da pagina corrente)."""
    collection = FeedbackProposalsRepository(db)._collection
    approved = await collection.count_documents({"status": ProposalStatus.APPROVED.value})
    applied = await collection.count_documents({"status": ProposalStatus.APPLIED.value})
    rejected = await collection.count_documents({"status": ProposalStatus.REJECTED.value})
    approved_total = approved + applied
    total = approved_total + rejected
    return ApprovalSummary(
        total_reviewed=total,
        approved_count=approved_total,
        rejected_count=rejected,
        approval_rate=(approved_total / total) if total else 0.0,
    )


def _count(proposal: FeedbackProposal, status: ProposalStatus) -> None:
    try:
        get_instruments().feedback_proposals.add(
            1, {"action_type": proposal.action_type.value, "status": status.value}
        )
    except Exception:  # noqa: BLE001 -- metrica nunca derruba a revisao
        logger.warning("metric_emit_failed")


async def _audit(proposal: FeedbackProposal, event_type: str, status: ProposalStatus) -> None:
    await safe_emit_audit_event(
        actor="system",
        actor_name="feedback_agent_review",
        event_type=event_type,
        status=status.value,
        safe_metadata={
            "proposal_id": proposal.proposal_id,
            "action_type": proposal.action_type.value,
        },
    )


async def _restore_pending(repo: FeedbackProposalsRepository, proposal_id: str) -> None:
    """Compensacao: falha ao aplicar devolve a proposta a `PENDING_HUMAN_REVIEW`."""
    await repo.update(
        proposal_id,
        {
            "status": ProposalStatus.PENDING_HUMAN_REVIEW.value,
            "reviewed_at": None,
            "reviewed_by": None,
        },
    )


async def approve_proposal(
    db: AsyncIOMotorDatabase, proposal_id: str, reviewed_by: str
) -> FeedbackProposal:
    repo = FeedbackProposalsRepository(db)
    proposal = await get_proposal(db, proposal_id)
    if proposal.status != ProposalStatus.PENDING_HUMAN_REVIEW:
        raise InvalidProposalStatusError(proposal.status.value)

    # Revalida ANTES de qualquer mudanca de estado: draft invalido/alvo ausente -> segue PENDING.
    draft = validate_draft(proposal)
    await ensure_target_exists(db, proposal.action_type, draft)

    claimed = await repo.claim_for_review(proposal_id, ProposalStatus.APPROVED, reviewed_by)
    if claimed is None:  # outra revisao concorrente venceu
        raise InvalidProposalStatusError("REVIEWED_CONCURRENTLY")
    _count(claimed, ProposalStatus.APPROVED)
    await _audit(claimed, "proposal_reviewed", ProposalStatus.APPROVED)

    if claimed.action_type == ActionType.PROMPT_ADJUSTMENT:
        return claimed  # aplicacao manual; so vira APPLIED via mark-applied (FR-040)

    try:
        applied_entity_id = await apply_draft(db, claimed.action_type, draft)
    except ProposalTargetNotFoundError:
        await _restore_pending(repo, proposal_id)
        raise
    except Exception as exc:  # noqa: BLE001 -- compensa e sinaliza falha estruturada
        logger.error("proposal_apply_failed", proposal_id=proposal_id, reason=type(exc).__name__)
        await _restore_pending(repo, proposal_id)
        raise ProposalApplyFailedError(type(exc).__name__) from exc

    applied = await repo.update(
        proposal_id,
        {"status": ProposalStatus.APPLIED.value, "applied_entity_id": applied_entity_id},
    )
    assert applied is not None
    _count(applied, ProposalStatus.APPLIED)
    await _audit(applied, "proposal_reviewed", ProposalStatus.APPLIED)
    return applied


async def reject_proposal(
    db: AsyncIOMotorDatabase, proposal_id: str, reason: str, reviewed_by: str
) -> FeedbackProposal:
    repo = FeedbackProposalsRepository(db)
    proposal = await get_proposal(db, proposal_id)
    if proposal.status != ProposalStatus.PENDING_HUMAN_REVIEW:
        raise InvalidProposalStatusError(proposal.status.value)
    rejected = await repo.claim_for_review(
        proposal_id, ProposalStatus.REJECTED, reviewed_by, rejection_reason=reason
    )
    if rejected is None:
        raise InvalidProposalStatusError("REVIEWED_CONCURRENTLY")
    _count(rejected, ProposalStatus.REJECTED)
    await _audit(rejected, "proposal_reviewed", ProposalStatus.REJECTED)
    return rejected


async def mark_proposal_applied(
    db: AsyncIOMotorDatabase, proposal_id: str, confirmed_by: str
) -> FeedbackProposal:
    """Confirmacao humana de que um `PROMPT_ADJUSTMENT` aprovado foi de fato aplicado (FR-040)."""
    await get_proposal(db, proposal_id)  # 404 estruturado quando inexistente
    applied = await FeedbackProposalsRepository(db).mark_applied(proposal_id, confirmed_by)
    if applied is None:
        raise InvalidProposalStatusError("NOT_APPROVED_PROMPT_ADJUSTMENT")
    _count(applied, ProposalStatus.APPLIED)
    await _audit(applied, "proposal_applied_confirmed", ProposalStatus.APPLIED)
    return applied
