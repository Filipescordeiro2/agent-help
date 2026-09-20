"""T060: aprovacao aplica a mudanca real, revalida o draft e compensa falhas (spec FR-015)."""

from __future__ import annotations

import pytest

import app.agent.feedback_agent.apply as apply_module
from app.agent.feedback_agent import service
from app.agent.feedback_validation.schemas import (
    KnowledgeDraft,
    KnowledgeUpdateDraft,
    PlaybookDraft,
    PlaybookStepDraft,
    PlaybookUpdateDraft,
    PromptAdjustmentDraft,
    SkillDraft,
    SkillUpdateDraft,
)
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from app.repository.knowledge_documents_repository import (
    KnowledgeDocumentsRepository,
)
from app.repository.playbooks_repository import PlaybooksRepository
from app.repository.skills_repository import SkillsRepository
from app.services.knowledge import ingest_document
from app.services.playbooks import create_playbook
from app.services.skills import create_skill

_PLAYBOOK = PlaybookDraft(
    name="Maquininha nao liga",
    objective="Restabelecer a maquininha.",
    symptoms=["nao liga"],
    steps=[PlaybookStepDraft(step_id="s1", instruction="Verificar bateria.")],
    success_criteria=["Liga."],
)
_SKILL = SkillDraft(
    name="Taxas", description="d", owning_agent="knowledge_agent", instructions="Cite a fonte."
)
_KNOWLEDGE = KnowledgeDraft(title="Taxas", source="feedback_agent", content="Conteudo novo.")


async def seed(db, action: ActionType, draft_content: dict, pid: str = "p1") -> FeedbackProposal:
    return await FeedbackProposalsRepository(db).insert(
        FeedbackProposal(
            proposal_id=pid,
            feedback_ids=["f1"],
            action_type=action,
            draft_content=draft_content,
            justificativa="j",
            confidence=0.8,
        )
    )


async def test_create_actions_create_the_real_entity_and_record_its_id(
    test_db, fake_embeddings
) -> None:
    playbook = await seed(test_db, ActionType.CREATE_PLAYBOOK, _PLAYBOOK.model_dump(), "pb")
    skill = await seed(test_db, ActionType.CREATE_SKILL, _SKILL.model_dump(), "sk")
    knowledge = await seed(test_db, ActionType.CREATE_KNOWLEDGE, _KNOWLEDGE.model_dump(), "kn")

    applied = [
        await service.approve_proposal(test_db, p.proposal_id, "rev")
        for p in (playbook, skill, knowledge)
    ]

    assert all(a.status == ProposalStatus.APPLIED and a.reviewed_by == "rev" for a in applied)
    assert all(a.reviewed_at is not None and a.applied_entity_id for a in applied)
    assert (await PlaybooksRepository(test_db).get(applied[0].applied_entity_id)).name == (
        "Maquininha nao liga"
    )
    assert (await SkillsRepository(test_db).get(applied[1].applied_entity_id)).name == "Taxas"
    assert (
        await KnowledgeDocumentsRepository(test_db).get(applied[2].applied_entity_id)
    ).title == "Taxas"


async def test_update_actions_update_the_existing_entity(test_db, fake_embeddings) -> None:
    skill = await create_skill(test_db, **_SKILL.model_dump())
    playbook = await create_playbook(test_db, **_PLAYBOOK.model_dump(mode="json"))
    document = await ingest_document(test_db, **_KNOWLEDGE.model_dump())

    new_skill = SkillUpdateDraft(
        target_id=skill.skill_id, **{**_SKILL.model_dump(), "instructions": "Nova."}
    )
    new_playbook = PlaybookUpdateDraft(
        target_id=playbook.playbook_id, **{**_PLAYBOOK.model_dump(), "objective": "Novo objetivo."}
    )
    new_doc = KnowledgeUpdateDraft(
        target_id=document.document_id, **{**_KNOWLEDGE.model_dump(), "title": "Titulo novo"}
    )
    for pid, action, draft in (
        ("us", ActionType.UPDATE_SKILL, new_skill),
        ("up", ActionType.UPDATE_PLAYBOOK, new_playbook),
        ("uk", ActionType.UPDATE_KNOWLEDGE, new_doc),
    ):
        await seed(test_db, action, draft.model_dump(mode="json"), pid)
        approved = await service.approve_proposal(test_db, pid, "rev")
        assert approved.status == ProposalStatus.APPLIED

    stored_skill = await SkillsRepository(test_db).get(skill.skill_id)
    assert stored_skill.instructions == "Nova." and stored_skill.version == skill.version + 1
    assert (await PlaybooksRepository(test_db).get(playbook.playbook_id)).objective == (
        "Novo objetivo."
    )
    assert (await KnowledgeDocumentsRepository(test_db).get(document.document_id)).title == (
        "Titulo novo"
    )


async def test_invalid_draft_is_refused_without_any_write_and_stays_pending(
    test_db, fake_embeddings
) -> None:
    await seed(test_db, ActionType.CREATE_PLAYBOOK, {"name": "so o nome"})

    with pytest.raises(service.DraftContentInvalidError):
        await service.approve_proposal(test_db, "p1", "rev")

    proposal = await FeedbackProposalsRepository(test_db).get("p1")
    assert proposal.status == ProposalStatus.PENDING_HUMAN_REVIEW and proposal.reviewed_by is None
    assert await test_db["playbooks"].count_documents({}) == 0


async def test_update_with_a_missing_target_is_refused_and_stays_pending(
    test_db, fake_embeddings
) -> None:
    draft = SkillUpdateDraft(target_id="nao-existe", **_SKILL.model_dump())
    await seed(test_db, ActionType.UPDATE_SKILL, draft.model_dump())

    with pytest.raises(service.ProposalTargetNotFoundError):
        await service.approve_proposal(test_db, "p1", "rev")

    proposal = await FeedbackProposalsRepository(test_db).get("p1")
    assert proposal.status == ProposalStatus.PENDING_HUMAN_REVIEW


async def test_service_failure_during_apply_restores_the_pending_state(
    test_db, fake_embeddings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_a, **_k):
        raise RuntimeError("banco indisponivel")

    monkeypatch.setattr(apply_module.skills_service, "create_skill", broken)
    await seed(test_db, ActionType.CREATE_SKILL, _SKILL.model_dump())

    with pytest.raises(service.ProposalApplyFailedError):
        await service.approve_proposal(test_db, "p1", "rev")

    proposal = await FeedbackProposalsRepository(test_db).get("p1")
    assert proposal.status == ProposalStatus.PENDING_HUMAN_REVIEW
    assert proposal.reviewed_by is None and proposal.reviewed_at is None
    assert proposal.applied_entity_id is None


async def test_prompt_adjustment_ends_in_approved_and_writes_nothing(
    test_db, fake_embeddings
) -> None:
    draft = PromptAdjustmentDraft(target_prompt="knowledge", proposed_change="Exigir fonte.")
    await seed(test_db, ActionType.PROMPT_ADJUSTMENT, draft.model_dump())

    approved = await service.approve_proposal(test_db, "p1", "rev")

    assert approved.status == ProposalStatus.APPROVED
    assert approved.applied_entity_id is None and approved.reviewed_by == "rev"
    for collection in ("skills", "playbooks", "knowledge_documents"):
        assert await test_db[collection].count_documents({}) == 0


async def test_reject_never_applies_and_records_the_reason(test_db, fake_embeddings) -> None:
    await seed(test_db, ActionType.CREATE_SKILL, _SKILL.model_dump())

    rejected = await service.reject_proposal(test_db, "p1", "duplicada com outra skill", "rev")

    assert rejected.status == ProposalStatus.REJECTED
    assert rejected.rejection_reason == "duplicada com outra skill"
    assert await test_db["skills"].count_documents({}) == 0


async def test_review_emits_audit_events_and_metrics(test_db, fake_embeddings) -> None:
    from prometheus_client import generate_latest

    from app.observability import otel

    otel.init_telemetry()
    await seed(test_db, ActionType.CREATE_SKILL, _SKILL.model_dump())
    await service.approve_proposal(test_db, "p1", "rev")

    events = await test_db["audit_events"].find({"event_type": "proposal_reviewed"}).to_list(10)
    assert {e["status"] for e in events} == {"APPROVED", "APPLIED"}
    assert all(e["safe_metadata"]["proposal_id"] == "p1" for e in events)
    assert (
        'getnet_feedback_proposals_total{action_type="CREATE_SKILL"' in generate_latest().decode()
    )
