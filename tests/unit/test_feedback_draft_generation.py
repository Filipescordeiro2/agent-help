"""T048: geracao de draft_content completo, pronto para aplicacao (spec FR-004)."""

from __future__ import annotations

import pytest

from app.agent.feedback_validation.agent import FeedbackValidationAgent, extract_draft_content
from app.agent.feedback_validation.schemas import (
    DRAFT_SCHEMAS,
    DecisionPointDraft,
    KnowledgeDraft,
    KnowledgeUpdateDraft,
    PlaybookDraft,
    PlaybookStepDraft,
    PlaybookUpdateDraft,
    PromptAdjustmentDraft,
    SkillDraft,
    SkillUpdateDraft,
)
from app.repository.feedback_proposals_repository import ActionType
from app.schemas.agent_response import Status
from app.services.knowledge import ingest_document
from app.services.playbooks import create_playbook
from app.services.skills import create_skill
from tests.feedback_helpers import add_feedback, patch_agent_llm

_PLAYBOOK = PlaybookDraft(
    name="Maquininha nao liga",
    objective="Restabelecer o funcionamento da maquininha.",
    symptoms=["nao liga", "tela apagada"],
    prerequisites=["device_id conhecido"],
    steps=[
        PlaybookStepDraft(
            step_id="check", instruction="Verificar a bateria.", tool="get_device_status"
        )
    ],
    decision_points=[
        DecisionPointDraft(question="Bateria carregada?", if_yes="advise", if_no="escalate")
    ],
    authorized_tools=["get_device_status"],
    escalation_rules=["Escalar se persistir."],
    success_criteria=["Dispositivo liga."],
)
_SKILL = SkillDraft(
    name="Taxas", description="d", owning_agent="knowledge_agent", instructions="Cite a fonte."
)
_KNOWLEDGE = KnowledgeDraft(title="Taxas", source="feedback_agent", content="Conteudo revisavel.")

_DRAFTS = {
    ActionType.CREATE_PLAYBOOK: _PLAYBOOK,
    ActionType.UPDATE_PLAYBOOK: PlaybookUpdateDraft(target_id="pb1", **_PLAYBOOK.model_dump()),
    ActionType.CREATE_SKILL: _SKILL,
    ActionType.UPDATE_SKILL: SkillUpdateDraft(target_id="sk1", **_SKILL.model_dump()),
    ActionType.CREATE_KNOWLEDGE: _KNOWLEDGE,
    ActionType.UPDATE_KNOWLEDGE: KnowledgeUpdateDraft(target_id="kd1", **_KNOWLEDGE.model_dump()),
    ActionType.PROMPT_ADJUSTMENT: PromptAdjustmentDraft(
        target_prompt="knowledge", proposed_change="Exigir citacao da fonte."
    ),
}


@pytest.mark.parametrize("action", list(_DRAFTS))
async def test_draft_is_complete_and_matches_the_target_schema(
    test_db, monkeypatch: pytest.MonkeyPatch, action: ActionType
) -> None:
    patch_agent_llm(monkeypatch, lambda schema, _m: _DRAFTS[action])
    feedback = await add_feedback(test_db, "falta conteudo para este caso especifico")

    response = await FeedbackValidationAgent().draft(action, [feedback], confidence=0.8)
    draft = extract_draft_content(response)

    assert response.status == Status.OK and response.metadata.confidence == 0.8
    assert draft is not None
    DRAFT_SCHEMAS[action].model_validate(draft)
    if action.name.startswith("UPDATE_"):
        assert draft["target_id"]


async def test_playbook_draft_has_every_playbook_section(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_agent_llm(monkeypatch, lambda *_: _PLAYBOOK)
    feedback = await add_feedback(test_db, "falta playbook para maquininha que nao liga")
    draft = extract_draft_content(
        await FeedbackValidationAgent().draft(ActionType.CREATE_PLAYBOOK, [feedback])
    )
    for section in (
        "objective",
        "symptoms",
        "steps",
        "decision_points",
        "authorized_tools",
        "success_criteria",
    ):
        assert draft[section], section


async def test_create_drafts_are_directly_applicable_by_the_existing_services(
    test_db, fake_embeddings
) -> None:
    playbook = await create_playbook(test_db, **_PLAYBOOK.model_dump(mode="json"))
    skill = await create_skill(test_db, **_SKILL.model_dump(mode="json"))
    document = await ingest_document(test_db, **_KNOWLEDGE.model_dump(mode="json"))

    assert playbook.name == _PLAYBOOK.name and playbook.steps[0].step_id == "check"
    assert skill.name == "Taxas"
    assert document.title == "Taxas"


async def test_update_draft_requires_the_target_id() -> None:
    with pytest.raises(ValueError):
        SkillUpdateDraft.model_validate(_SKILL.model_dump())


async def test_draft_failure_becomes_an_error_response(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_):
        raise RuntimeError("llm down")

    patch_agent_llm(monkeypatch, boom)
    feedback = await add_feedback(test_db, "comentario com conteudo suficiente")
    response = await FeedbackValidationAgent().draft(ActionType.CREATE_SKILL, [feedback])
    assert response.status == Status.ERROR and extract_draft_content(response) is None


async def test_no_action_has_no_draft_schema(test_db) -> None:
    feedback = await add_feedback(test_db, "comentario com conteudo suficiente")
    response = await FeedbackValidationAgent().draft(ActionType.NO_ACTION, [feedback])
    assert response.status == Status.ERROR
    assert response.metadata.error_code == "NO_DRAFT_FOR_ACTION"
