"""Nos do StateGraph do Feedback Agent (classifica -> agrupa -> gera draft -> persiste).

Este modulo NAO importa `app.skills`, `app.playbooks` nem `app.knowledge`: nenhum no aplica
mudancas (Constitution Principio XII) -- so `agent/feedback_agent/apply.py`, apos aprovacao humana.
Cada no roda sob o timeout `node_timeout_seconds` (por chamada de LLM) e o lote e limitado por
`feedback_agent_max_batch_size` (Principio I: nenhum laco sem limite).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent.state import FeedbackAgentState
from app.agent.feedback_validation.agent import (
    FeedbackValidationAgent,
    extract_classification,
    extract_draft_content,
)
from app.agent.feedback_validation.schemas import FeedbackClassification
from app.config.settings import get_settings
from app.observability.audit import safe_emit_audit_event
from app.observability.metrics import get_instruments
from app.repository.feedback_agent_runs_repository import (
    FeedbackAgentRunsRepository,
)
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from app.repository.feedback_repository import Feedback
from app.repository.knowledge_documents_repository import (
    KnowledgeDocumentsRepository,
)
from app.repository.playbooks_repository import PlaybooksRepository
from app.repository.skills_repository import SkillsRepository
from app.security.scanners import get_input_scanner, get_output_scanner
from app.services.feedback import list_feedback
from app.utils.text import normalize_topic

logger = structlog.get_logger(__name__)

_MAX_JUSTIFICATIVA_CHARS = 500


def _flatten_text(value: Any) -> str:
    """Concatena todos os valores de texto de um draft (para varredura de seguranca)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_flatten_text(v) for v in value.values())
    if isinstance(value, list | tuple):
        return "\n".join(_flatten_text(v) for v in value)
    return ""


async def _build_catalog(db: AsyncIOMotorDatabase) -> dict[str, list[dict[str, str]]]:
    """Catalogo somente-leitura (ids e nomes) para o agente decidir entre criar e atualizar."""
    skills = await SkillsRepository(db).list(limit=200)
    playbooks = await PlaybooksRepository(db).list(limit=200)
    documents = await KnowledgeDocumentsRepository(db).list(limit=200)
    return {
        "skills": [{"id": s.skill_id, "name": s.name} for s in skills],
        "playbooks": [{"id": p.playbook_id, "name": p.name} for p in playbooks],
        "knowledge": [{"id": d.document_id, "title": d.title} for d in documents],
    }


async def load_pending_feedback(state: FeedbackAgentState, *, db: AsyncIOMotorDatabase) -> Any:
    settings = get_settings()
    processed = await FeedbackAgentRunsRepository(db).all_processed_feedback_ids()
    feedbacks = await list_feedback(db)
    pending = sorted(
        (f for f in feedbacks if f.feedback_id not in processed), key=lambda f: f.created_at
    )[: settings.feedback_agent_max_batch_size]
    state["pending_feedback"] = pending
    state["catalog"] = await _build_catalog(db) if pending else {}
    state["classified"] = []
    state["groups"] = []
    state["drafts"] = []
    state["proposals"] = []
    state["processed_ids"] = []
    state["discarded_count"] = 0
    state["blocked"] = {}
    return state


def route_after_load(state: FeedbackAgentState) -> str:
    return "classify" if state.get("pending_feedback") else "done"


async def classify_feedback(state: FeedbackAgentState, *, agent: FeedbackValidationAgent) -> Any:
    settings = get_settings()
    classified: list[tuple[Feedback, FeedbackClassification]] = []
    for feedback in state.get("pending_feedback", []):
        try:
            response = await asyncio.wait_for(
                agent.classify(feedback, state.get("catalog"), execution_id=state["run_id"]),
                timeout=settings.node_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("feedback_classification_timeout")
            continue  # nao marca como processado: sera reavaliado no proximo run
        classification = extract_classification(response)
        if classification is None:
            continue  # falha de classificacao: reavaliado no proximo run
        if not classification.is_actionable or classification.action_type == ActionType.NO_ACTION:
            state["discarded_count"] = state.get("discarded_count", 0) + 1
            state["processed_ids"].append(feedback.feedback_id)
            continue
        classified.append((feedback, classification))
    state["classified"] = classified
    return state


async def group_actionable(state: FeedbackAgentState) -> Any:
    """Agrupa feedbacks acionaveis por (action_type, tema-alvo normalizado)."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for feedback, classification in state.get("classified", []):
        topic = normalize_topic(classification.target_topic)
        # Sem tema nao ha equivalencia: cada feedback forma seu proprio grupo.
        key = (classification.action_type.value, topic or f"__solo__{feedback.feedback_id}")
        group = groups.setdefault(
            key,
            {
                "action_type": classification.action_type,
                "target_topic": topic,
                "feedbacks": [],
                "classifications": [],
            },
        )
        group["feedbacks"].append(feedback)
        group["classifications"].append(classification)
    state["groups"] = list(groups.values())
    return state


async def _resolve_equivalence(
    proposals_repo: FeedbackProposalsRepository, group: dict[str, Any]
) -> bool:
    """True quando o grupo foi absorvido por uma proposta ativa (spec FR-018)."""
    feedback_ids = [f.feedback_id for f in group["feedbacks"]]

    covered = {
        fid
        for proposal in await proposals_repo.find_active_for_feedback_ids(feedback_ids)
        for fid in proposal.feedback_ids
    }
    if covered.issuperset(feedback_ids):
        return True

    for _attempt in range(2):
        equivalent = await proposals_repo.find_active_by_equivalence(
            group["action_type"], group["target_topic"]
        )
        if equivalent is None:
            return False
        if equivalent.status != ProposalStatus.PENDING_HUMAN_REVIEW:
            return True  # equivalente a proposta APPROVED: apenas marca como processado
        if await proposals_repo.add_feedback_ids(equivalent.proposal_id, feedback_ids):
            return True
        # Corrida: a proposta deixou de estar pendente entre a consulta e a uniao -- reconsulta.
    return True


async def draft_proposals(
    state: FeedbackAgentState, *, db: AsyncIOMotorDatabase, agent: FeedbackValidationAgent
) -> Any:
    settings = get_settings()
    proposals_repo = FeedbackProposalsRepository(db)
    drafts: list[dict[str, Any]] = []
    for group in state.get("groups", []):
        feedback_ids = [f.feedback_id for f in group["feedbacks"]]
        if await _resolve_equivalence(proposals_repo, group):
            state["processed_ids"].extend(feedback_ids)
            continue

        confidence = sum(c.confidence for c in group["classifications"]) / len(
            group["classifications"]
        )
        try:
            response = await asyncio.wait_for(
                agent.draft(
                    group["action_type"],
                    group["feedbacks"],
                    state.get("catalog"),
                    confidence=confidence,
                    execution_id=state["run_id"],
                ),
                timeout=settings.node_timeout_seconds,
            )
        except TimeoutError:
            continue  # reavaliado no proximo run
        draft_content = extract_draft_content(response)
        if draft_content is None:
            continue

        # Principio VIII: o draft e gerado a partir de texto nao confiavel -- varre o conteudo
        # antes de persistir. Draft reprovado nunca vira proposta e nunca e logado.
        text = _flatten_text(draft_content)
        scan = get_input_scanner().scan(text)
        if scan.is_safe:
            scan = get_output_scanner().scan(text)
        if not scan.is_safe:
            reason = scan.reason_code or "DRAFT_BLOCKED"
            for fid in feedback_ids:
                state["blocked"][fid] = reason
            state["processed_ids"].extend(feedback_ids)
            get_instruments().agent_errors.add(1, {"agent": agent.name, "error_code": reason})
            await safe_emit_audit_event(
                actor="agent",
                actor_name=agent.name,
                event_type="draft_blocked",
                status="blocked",
                error_code=reason,
                safe_metadata={"action_type": group["action_type"].value},
            )
            continue

        justificativa = " | ".join(
            dict.fromkeys(c.justificativa for c in group["classifications"])
        )[:_MAX_JUSTIFICATIVA_CHARS]
        drafts.append(
            {
                "group": group,
                "draft_content": draft_content,
                "justificativa": justificativa,
                "confidence": min(max(confidence, 0.0), 1.0),
            }
        )
    state["drafts"] = drafts
    return state


async def persist_proposals(state: FeedbackAgentState, *, db: AsyncIOMotorDatabase) -> Any:
    proposals_repo = FeedbackProposalsRepository(db)
    runs_repo = FeedbackAgentRunsRepository(db)
    for item in state.get("drafts", []):
        group = item["group"]
        feedback_ids = [f.feedback_id for f in group["feedbacks"]]
        if await _resolve_equivalence(proposals_repo, group):  # corrida entre draft e persist
            state["processed_ids"].extend(feedback_ids)
            continue

        proposal = FeedbackProposal(
            proposal_id=str(uuid.uuid4()),
            feedback_ids=feedback_ids,
            status=ProposalStatus.PENDING_HUMAN_REVIEW,
            action_type=group["action_type"],
            draft_content=item["draft_content"],
            justificativa=item["justificativa"],
            confidence=item["confidence"],
            target_topic=group["target_topic"],
        )
        await proposals_repo.insert(proposal)
        await runs_repo.add_proposal_id(state["run_id"], proposal.proposal_id)
        get_instruments().feedback_proposals.add(
            1,
            {
                "action_type": proposal.action_type.value,
                "status": ProposalStatus.PENDING_HUMAN_REVIEW.value,
            },
        )
        state["proposals"].append(proposal.proposal_id)
        state["processed_ids"].extend(feedback_ids)
    return state
