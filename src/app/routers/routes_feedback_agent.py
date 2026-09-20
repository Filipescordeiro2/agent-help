"""Rotas do Feedback Agent -- propostas (US3) e execucoes (US4). Rotas finas (FR-044).

Toda rota esta sob o boundary de confianca interna (`X-Internal-Service-Token`). Aprovar,
rejeitar e confirmar aplicacao exigem `X-Reviewer-Id` (identidade pre-validada pelo canal
interno, nunca vinda do corpo nem do LLM -- Principio V).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent import service
from app.agent.feedback_agent.concurrency import FeedbackAgentRunInProgress
from app.agent.feedback_agent.runner import run_feedback_agent
from app.repository.feedback_agent_runs_repository import (
    FeedbackAgentRun,
    FeedbackAgentRunsRepository,
    RunStatus,
    TriggerType,
)
from app.repository.feedback_proposals_repository import (
    FeedbackProposal,
    ProposalStatus,
)
from app.routers.deps import get_db, require_llm_api_key
from app.routers.errors import structured_error
from app.schemas.feedback_agent import ProposalListResponse, RejectRequest

router = APIRouter(prefix="/api/v1/feedback-agent", tags=["feedback-agent"])

_MAX_REVIEWER_ID_LENGTH = 128


class ReviewerIdRequired(Exception):
    pass


def _reviewer_id(x_reviewer_id: str | None) -> str:
    reviewer = (x_reviewer_id or "").strip()
    if not reviewer or len(reviewer) > _MAX_REVIEWER_ID_LENGTH:
        raise ReviewerIdRequired
    return reviewer


def _reviewer_required() -> JSONResponse:
    return structured_error(
        400, "REVIEWER_ID_REQUIRED", "O cabecalho X-Reviewer-Id e obrigatorio nesta operacao."
    )


def _map_service_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, service.ProposalNotFoundError):
        return structured_error(404, "PROPOSAL_NOT_FOUND", "Proposta nao encontrada.")
    if isinstance(exc, service.InvalidProposalStatusError):
        return structured_error(
            409, "INVALID_PROPOSAL_STATUS", "A proposta nao esta em um status que permita isto."
        )
    if isinstance(exc, service.DraftContentInvalidError):
        return structured_error(
            422,
            "DRAFT_CONTENT_INVALID",
            "O draft_content nao e valido contra o schema atual da entidade alvo.",
        )
    if isinstance(exc, service.ProposalTargetNotFoundError):
        return structured_error(
            409, "PROPOSAL_TARGET_NOT_FOUND", "O item alvo da atualizacao nao existe mais."
        )
    if isinstance(exc, service.ProposalApplyFailedError):
        return structured_error(
            500, "PROPOSAL_APPLY_FAILED", "Falha ao aplicar a proposta; ela segue pendente."
        )
    raise exc


@router.get("/proposals", response_model=ProposalListResponse)
async def list_proposals(
    status: ProposalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    cursor: int = Query(default=0, ge=0),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProposalListResponse:
    return ProposalListResponse(
        proposals=await service.list_proposals(db, status, limit, cursor),
        approval_summary=await service.approval_summary(db),
    )


@router.get("/proposals/{proposal_id}", response_model=FeedbackProposal)
async def get_proposal(proposal_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        return await service.get_proposal(db, proposal_id)
    except service.ProposalNotFoundError as exc:
        return _map_service_error(exc)


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=FeedbackProposal,
    dependencies=[Depends(require_llm_api_key)],
)
async def approve_proposal(
    proposal_id: str,
    x_reviewer_id: str | None = Header(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        reviewer = _reviewer_id(x_reviewer_id)
    except ReviewerIdRequired:
        return _reviewer_required()
    try:
        return await service.approve_proposal(db, proposal_id, reviewer)
    except Exception as exc:  # noqa: BLE001 -- mapeia erros de dominio; o resto propaga
        return _map_service_error(exc)


@router.post("/proposals/{proposal_id}/reject", response_model=FeedbackProposal)
async def reject_proposal(
    proposal_id: str,
    payload: RejectRequest,
    x_reviewer_id: str | None = Header(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        reviewer = _reviewer_id(x_reviewer_id)
    except ReviewerIdRequired:
        return _reviewer_required()
    try:
        return await service.reject_proposal(db, proposal_id, payload.rejection_reason, reviewer)
    except Exception as exc:  # noqa: BLE001
        return _map_service_error(exc)


@router.post("/proposals/{proposal_id}/mark-applied", response_model=FeedbackProposal)
async def mark_applied(
    proposal_id: str,
    x_reviewer_id: str | None = Header(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        reviewer = _reviewer_id(x_reviewer_id)
    except ReviewerIdRequired:
        return _reviewer_required()
    try:
        return await service.mark_proposal_applied(db, proposal_id, reviewer)
    except Exception as exc:  # noqa: BLE001
        return _map_service_error(exc)


# --- Execucoes do Feedback Agent (US4) ------------------------------------------------------


@router.post("/run", response_model=FeedbackAgentRun, dependencies=[Depends(require_llm_api_key)])
async def run_now(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Forca uma execucao imediata (sincrona nesta versao), fora do agendamento."""
    try:
        return await run_feedback_agent(db, TriggerType.MANUAL)
    except FeedbackAgentRunInProgress:
        return structured_error(
            409,
            "FEEDBACK_AGENT_RUN_IN_PROGRESS",
            "Ja existe uma execucao do Feedback Agent em andamento.",
        )


@router.get("/runs", response_model=list[FeedbackAgentRun])
async def list_runs(
    status: RunStatus | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    cursor: int = Query(default=0, ge=0),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[FeedbackAgentRun]:
    return await FeedbackAgentRunsRepository(db).list_recent(status, limit, cursor)


@router.get("/runs/{run_id}", response_model=FeedbackAgentRun)
async def get_run(run_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    run = await FeedbackAgentRunsRepository(db).get(run_id)
    if run is None:
        return structured_error(404, "RUN_NOT_FOUND", "Execucao nao encontrada.")
    return run
