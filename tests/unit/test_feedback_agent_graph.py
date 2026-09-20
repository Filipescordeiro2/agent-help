"""T049: agrupamento, equivalencia (spec FR-018), rastreio de processados e lote do Feedback Agent."""

from __future__ import annotations

import pytest

from app.agent.feedback_agent.nodes import group_actionable
from app.agent.feedback_agent.runner import run_feedback_agent
from app.agent.feedback_validation.schemas import FeedbackClassification
from app.config.settings import get_settings
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
from app.utils.text import normalize_topic
from tests.feedback_helpers import add_feedback, classifier_handler, patch_agent_llm


def actionable(topic: str | None, action: ActionType = ActionType.CREATE_PLAYBOOK):
    return FeedbackClassification(
        is_actionable=True,
        action_type=action,
        target_topic=topic,
        justificativa="lacuna",
        confidence=0.8,
    )


def by_topic(topic_map: dict[str, str | None], action: ActionType = ActionType.CREATE_PLAYBOOK):
    def mapping(comment: str):
        if comment not in topic_map:
            return FeedbackClassification(
                is_actionable=False, discard_reason="NOISE", justificativa="j", confidence=0.5
            )
        return actionable(topic_map[comment], action)

    return mapping


def proposal(pid, feedback_ids, status=ProposalStatus.PENDING_HUMAN_REVIEW, **kwargs):
    return FeedbackProposal(
        proposal_id=pid,
        feedback_ids=feedback_ids,
        status=status,
        action_type=kwargs.pop("action_type", ActionType.CREATE_PLAYBOOK),
        draft_content={"name": "x"},
        justificativa="j",
        confidence=0.7,
        **kwargs,
    )


def test_normalize_topic_ignores_case_accents_punctuation_and_spaces() -> None:
    assert normalize_topic("Maquininha não LIGA!") == normalize_topic("maquininha   nao liga")
    assert normalize_topic("  Cartão... de  crédito? ") == "cartao de credito"
    assert normalize_topic("") is None and normalize_topic(None) is None
    assert normalize_topic("!!! ...") is None


async def test_group_actionable_merges_equivalent_topics_only() -> None:
    from app.repository.feedback_repository import Feedback

    def fb(fid: str) -> Feedback:
        return Feedback(
            feedback_id=fid,
            user_id="u",
            session_id="s",
            message_id="m",
            execution_id="e",
            problem_classification="KNOWLEDGE_GAP",
        )

    state = {
        "classified": [
            (fb("a"), actionable("Maquininha nao liga!")),
            (fb("b"), actionable("maquininha NAO liga")),
            (fb("c"), actionable("outro tema")),
            (fb("d"), actionable(None)),
            (fb("e"), actionable(None)),
            (fb("f"), actionable("maquininha nao liga", ActionType.CREATE_SKILL)),
        ]
    }
    result = await group_actionable(state)
    sizes = sorted(len(g["feedbacks"]) for g in result["groups"])
    assert sizes == [1, 1, 1, 1, 2]  # so a e b se juntam; sem tema nao ha equivalencia
    merged = next(g for g in result["groups"] if len(g["feedbacks"]) == 2)
    assert {f.feedback_id for f in merged["feedbacks"]} == {"a", "b"}


async def test_equivalent_feedbacks_in_one_run_become_a_single_proposal(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(
        monkeypatch,
        classifier_handler(
            by_topic(
                {
                    "comentario um sobre liga": "Maquininha nao liga",
                    "outro texto liga": "maquininha NÃO liga",
                }
            )
        ),
    )
    f1 = await add_feedback(test_db, "comentario um sobre liga")
    f2 = await add_feedback(test_db, "outro texto liga")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    proposals = await FeedbackProposalsRepository(test_db).list_by_status()
    assert len(proposals) == 1 and run.proposals_created_count == 1
    assert set(proposals[0].feedback_ids) == {f1.feedback_id, f2.feedback_id}
    assert proposals[0].target_topic == "maquininha nao liga"
    assert set(run.processed_feedback_ids) == {f1.feedback_id, f2.feedback_id}


async def test_feedback_already_covered_by_active_proposal_creates_nothing(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(monkeypatch, classifier_handler(by_topic({"comentario coberto": "tema z"})))
    feedback = await add_feedback(test_db, "comentario coberto")
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(proposal("existing", [feedback.feedback_id], target_topic="outro"))

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert len(await repo.list_by_status()) == 1 and run.proposals_created_count == 0
    assert feedback.feedback_id in run.processed_feedback_ids


async def test_new_equivalent_feedback_is_appended_to_the_pending_proposal(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(monkeypatch, classifier_handler(by_topic({"novo feedback": "tema alvo"})))
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(proposal("pend", ["old"], target_topic="tema alvo"))
    new_feedback = await add_feedback(test_db, "novo feedback")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    stored = await repo.get("pend")
    assert set(stored.feedback_ids) == {"old", new_feedback.feedback_id}
    assert len(await repo.list_by_status()) == 1 and run.proposals_created_count == 0
    assert new_feedback.feedback_id in run.processed_feedback_ids


async def test_equivalent_to_an_approved_proposal_only_marks_processed(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(monkeypatch, classifier_handler(by_topic({"novo feedback": "tema alvo"})))
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(proposal("appr", ["old"], ProposalStatus.APPROVED, target_topic="tema alvo"))
    new_feedback = await add_feedback(test_db, "novo feedback")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert (await repo.get("appr")).feedback_ids == ["old"]
    assert len(await repo.list_by_status()) == 1
    assert new_feedback.feedback_id in run.processed_feedback_ids


async def test_different_or_missing_topic_does_not_match_an_active_proposal(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(
        monkeypatch,
        classifier_handler(by_topic({"comentario a": "tema diferente", "comentario b": None})),
    )
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(proposal("pend", ["old"], target_topic="tema alvo"))
    await add_feedback(test_db, "comentario a")
    await add_feedback(test_db, "comentario b")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert run.proposals_created_count == 2 and len(await repo.list_by_status()) == 3


async def test_equivalence_race_falls_back_to_reviewed_path_without_duplicate(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(monkeypatch, classifier_handler(by_topic({"novo feedback": "tema alvo"})))
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(proposal("pend", ["old"], target_topic="tema alvo"))
    original = FeedbackProposalsRepository.add_feedback_ids
    flipped = {"done": False}

    async def racing(self, proposal_id, ids):
        if not flipped["done"]:
            flipped["done"] = True
            await self.claim_for_review(proposal_id, ProposalStatus.APPROVED, "rev")
        return await original(self, proposal_id, ids)

    monkeypatch.setattr(FeedbackProposalsRepository, "add_feedback_ids", racing)
    await add_feedback(test_db, "novo feedback")

    run = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert run.proposals_created_count == 0 and len(await repo.list_by_status()) == 1


async def test_prompt_adjustment_absorbs_until_applied_then_new_feedback_creates_proposal(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(
        monkeypatch,
        classifier_handler(
            by_topic({"feedback dois": "tema prompt"}, ActionType.PROMPT_ADJUSTMENT)
        ),
    )
    repo = FeedbackProposalsRepository(test_db)
    await repo.insert(
        proposal(
            "pa",
            ["old"],
            ProposalStatus.APPROVED,
            action_type=ActionType.PROMPT_ADJUSTMENT,
            target_topic="tema prompt",
        )
    )
    await add_feedback(test_db, "feedback dois")
    first = await run_feedback_agent(test_db, TriggerType.MANUAL)
    assert first.proposals_created_count == 0  # absorvido: ainda APPROVED (nao confirmada)

    await repo.mark_applied("pa", "rev-1")
    await add_feedback(test_db, "feedback dois")
    second = await run_feedback_agent(test_db, TriggerType.MANUAL)
    assert second.proposals_created_count == 1  # APPLIED deixou de ser ativa


async def test_feedbacks_processed_by_completed_runs_are_not_reclassified(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = patch_agent_llm(monkeypatch, classifier_handler(by_topic({})))
    old = await add_feedback(test_db, "ja processado antes")
    await add_feedback(test_db, "feedback novo aqui")
    await FeedbackAgentRunsRepository(test_db).insert(
        FeedbackAgentRun(
            run_id="prev",
            trigger_type=TriggerType.SCHEDULED,
            status=RunStatus.COMPLETED,
            processed_feedback_ids=[old.feedback_id],
        )
    )

    await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert len(calls) == 1 and "feedback novo aqui" in calls[0][1][1]["content"]


async def test_batch_is_limited_and_remaining_feedbacks_are_taken_next_run(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "feedback_agent_max_batch_size", 2)
    calls = patch_agent_llm(monkeypatch, classifier_handler(by_topic({})))
    for i in range(5):
        await add_feedback(test_db, f"comentario numero {i}")

    first = await run_feedback_agent(test_db, TriggerType.MANUAL)
    second = await run_feedback_agent(test_db, TriggerType.MANUAL)
    third = await run_feedback_agent(test_db, TriggerType.MANUAL)

    assert [first.feedback_processed_count, second.feedback_processed_count] == [2, 2]
    assert third.feedback_processed_count == 1 and len(calls) == 5


async def test_no_pending_feedback_completes_with_zero_proposals(test_db) -> None:
    run = await run_feedback_agent(test_db, TriggerType.SCHEDULED)
    assert run.status == RunStatus.COMPLETED
    assert run.trigger_type == TriggerType.SCHEDULED
    assert run.feedback_processed_count == 0 and run.proposals_created_count == 0
    assert run.finished_at is not None
