"""T050: run do Feedback Agent (US2) -- feedback -> proposta PENDING_HUMAN_REVIEW, sem aplicar nada."""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

import app.agent.feedback_agent.nodes as nodes_module
from app.agent.feedback_agent.runner import run_feedback_agent
from app.llm.fake_defaults import _classification_default
from app.observability import otel
from app.repository.feedback_agent_runs_repository import RunStatus, TriggerType
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from tests.feedback_helpers import add_feedback, classifier_handler, patch_agent_llm

_PLAYBOOK_FEEDBACK = "Nao existe passo a passo para maquininha que nao liga de jeito nenhum"


@pytest.fixture(autouse=True)
def _default_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    otel.init_telemetry()
    patch_agent_llm(
        monkeypatch, classifier_handler(lambda c: _classification_default(f"comentario: {c}"))
    )


async def _snapshot(db) -> dict[str, list[dict]]:
    names = ("feedback", "skills", "playbooks", "knowledge_documents", "knowledge_chunks")
    return {n: await db[n].find({}).to_list(length=1000) for n in names}


async def test_specific_feedback_creates_a_pending_proposal_with_ready_draft(test_db) -> None:
    feedback = await add_feedback(test_db, _PLAYBOOK_FEEDBACK)

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert run.status == RunStatus.COMPLETED and run.trigger_type == TriggerType.MANUAL
    proposals = await FeedbackProposalsRepository(test_db).list_by_status()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.status == ProposalStatus.PENDING_HUMAN_REVIEW
    assert proposal.action_type == ActionType.CREATE_PLAYBOOK
    assert proposal.feedback_ids == [feedback.feedback_id]
    assert 0.0 <= proposal.confidence <= 1.0 and proposal.justificativa
    for section in ("objective", "symptoms", "steps", "success_criteria"):
        assert proposal.draft_content[section]
    assert run.proposal_ids == [proposal.proposal_id]
    assert run.proposals_created_count == 1 and run.feedback_processed_count == 1
    assert run.processed_feedback_ids == [feedback.feedback_id]
    assert run.finished_at is not None
    assert 'action_type="CREATE_PLAYBOOK"' in generate_latest().decode()


async def test_noise_feedback_creates_no_proposal_but_is_marked_processed(test_db) -> None:
    feedback = await add_feedback(test_db, "ruim")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert await FeedbackProposalsRepository(test_db).list_by_status() == []
    assert run.proposals_created_count == 0
    assert run.processed_feedback_ids == [feedback.feedback_id]


async def test_rerunning_does_not_duplicate_proposals(test_db) -> None:
    await add_feedback(test_db, _PLAYBOOK_FEEDBACK)

    first = await run_feedback_agent(test_db, TriggerType.MANUAL)
    second = await run_feedback_agent(test_db, TriggerType.SCHEDULED)

    assert first.proposals_created_count == 1
    assert second.proposals_created_count == 0 and second.feedback_processed_count == 0
    assert len(await FeedbackProposalsRepository(test_db).list_by_status()) == 1


async def test_run_never_changes_feedback_or_applies_anything(test_db) -> None:
    await add_feedback(test_db, _PLAYBOOK_FEEDBACK)
    await add_feedback(test_db, "Falta uma skill para responder sobre taxas de forma padrao")
    await add_feedback(test_db, "ruim")
    before = await _snapshot(test_db)

    await run_feedback_agent(test_db, TriggerType.MANUAL)
    after = await _snapshot(test_db)

    assert after == before  # feedback intacto; nenhuma Skill/Playbook/documento criado
    assert len(await FeedbackProposalsRepository(test_db).list_by_status()) == 2


async def test_failed_run_is_recorded_without_stack_trace(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_db, **_kwargs):
        raise RuntimeError("segredo interno: sk-should-not-leak")

    monkeypatch.setattr(nodes_module, "list_feedback", broken)

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert run.status == RunStatus.FAILED and run.finished_at is not None
    assert run.error_summary and "sk-should-not-leak" not in run.error_summary
    assert "Traceback" not in run.error_summary


async def test_classification_failure_leaves_the_feedback_for_the_next_run(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_):
        raise RuntimeError("llm down")

    patch_agent_llm(monkeypatch, broken)
    feedback = await add_feedback(test_db, _PLAYBOOK_FEEDBACK)

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert run.status == RunStatus.COMPLETED and run.processed_feedback_ids == []
    patch_agent_llm(
        monkeypatch, classifier_handler(lambda c: _classification_default(f"comentario: {c}"))
    )
    retry = await run_feedback_agent(test_db, TriggerType.MANUAL)
    assert retry.processed_feedback_ids == [feedback.feedback_id]
    assert retry.proposals_created_count == 1
