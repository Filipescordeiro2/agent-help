"""Aplicacao de uma proposta JA aprovada por um humano (Constitution Principio XII).

Este e o UNICO modulo do Feedback Agent autorizado a importar os servicos de escrita de Skills,
Playbooks e Conhecimento. Aplicar e CRUD deterministico (nao e orquestracao de agente, ver
research.md #12): o `draft_content` e REVALIDADO contra o schema atual da entidade alvo no
momento da aprovacao (research.md #3) e so entao os `service.py` existentes sao chamados.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ValidationError

from app.agent.feedback_validation.schemas import DRAFT_SCHEMAS
from app.repository.feedback_proposals_repository import ActionType, FeedbackProposal
from app.repository.knowledge_documents_repository import (
    KnowledgeDocumentsRepository,
)
from app.repository.playbooks_repository import PlaybooksRepository
from app.repository.skills_repository import SkillsRepository
from app.services import knowledge as knowledge_service
from app.services import playbooks as playbooks_service
from app.services import skills as skills_service


class DraftContentInvalidError(Exception):
    """`draft_content` nao valida contra o schema atual da entidade alvo."""


class ProposalTargetNotFoundError(Exception):
    """Proposta de atualizacao cujo alvo nao existe (mais)."""


def validate_draft(proposal: FeedbackProposal) -> BaseModel:
    schema = DRAFT_SCHEMAS.get(proposal.action_type)
    if schema is None:
        raise DraftContentInvalidError(f"action_type sem draft aplicavel: {proposal.action_type}")
    try:
        return schema.model_validate(proposal.draft_content)
    except ValidationError as exc:
        raise DraftContentInvalidError(str(exc)) from exc


async def ensure_target_exists(
    db: AsyncIOMotorDatabase, action_type: ActionType, draft: BaseModel
) -> None:
    """Falha cedo (antes de qualquer mudanca de estado) quando o alvo de um UPDATE nao existe."""
    if not action_type.value.startswith("UPDATE_"):
        return
    target_id = draft.target_id  # type: ignore[attr-defined]
    if action_type == ActionType.UPDATE_SKILL:
        exists = await SkillsRepository(db).get(target_id) is not None
    elif action_type == ActionType.UPDATE_PLAYBOOK:
        exists = await PlaybooksRepository(db).get(target_id) is not None
    else:
        exists = await KnowledgeDocumentsRepository(db).get(target_id) is not None
    if not exists:
        raise ProposalTargetNotFoundError(target_id)


async def apply_draft(
    db: AsyncIOMotorDatabase, action_type: ActionType, draft: BaseModel
) -> str | None:
    """Aplica o draft ja validado e devolve o id da entidade criada/atualizada.

    `PROMPT_ADJUSTMENT` nao tem alvo aplicavel em runtime (prompts sao arquivos versionados):
    devolve `None` e a aplicacao permanece manual (spec FR-015)."""
    payload: dict[str, Any] = draft.model_dump(mode="json")
    try:
        if action_type == ActionType.CREATE_SKILL:
            return (await skills_service.create_skill(db, **payload)).skill_id
        if action_type == ActionType.CREATE_PLAYBOOK:
            return (await playbooks_service.create_playbook(db, **payload)).playbook_id
        if action_type == ActionType.CREATE_KNOWLEDGE:
            return (await knowledge_service.ingest_document(db, **payload)).document_id

        if action_type in (
            ActionType.UPDATE_SKILL,
            ActionType.UPDATE_PLAYBOOK,
            ActionType.UPDATE_KNOWLEDGE,
        ):
            target_id = payload.pop("target_id")
            if action_type == ActionType.UPDATE_SKILL:
                await skills_service.update_skill(db, target_id, **payload)
            elif action_type == ActionType.UPDATE_PLAYBOOK:
                await playbooks_service.update_playbook(db, target_id, **payload)
            else:
                await knowledge_service.update_document(db, target_id, **payload)
            return target_id
    except (
        skills_service.SkillNotFoundError,
        playbooks_service.PlaybookNotFoundError,
        knowledge_service.KnowledgeDocumentNotFoundError,
    ) as exc:
        raise ProposalTargetNotFoundError(str(exc)) from exc
    return None
