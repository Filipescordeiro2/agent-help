"""T109: teste unitario dos servicos de Skills/Playbooks/Keywords -- validacao de campos
obrigatorios, incremento de versao, regeracao de embedding ao mudar instrucoes/keywords."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.services.keywords as keywords_service
import app.services.playbooks as playbooks_service
import app.services.skills as skills_service
from app.repository.keywords_repository import KeywordsRepository
from app.repository.playbooks_repository import PlaybooksRepository
from app.repository.skills_repository import SkillsRepository


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


# --- Skills --------------------------------------------------------------------------------


async def test_create_skill_computes_an_embedding(db) -> None:
    skill = await skills_service.create_skill(
        db,
        name="Diagnostico de conexao",
        description="Ajuda a diagnosticar problemas de conexao",
        owning_agent="customer_support_agent",
        keywords=["conexao", "wifi"],
        instructions="Verifique o sinal de wifi antes de orientar o cliente.",
        allowed_tools=["get_device_diagnostics"],
    )
    assert skill.embedding
    assert skill.version == 1


async def test_update_skill_increments_version(db) -> None:
    skill = await skills_service.create_skill(
        db,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=[],
        instructions="original",
        allowed_tools=[],
    )
    updated = await skills_service.update_skill(
        db,
        skill.skill_id,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=[],
        instructions="original",
        allowed_tools=[],
        enabled=False,
    )
    assert updated.version == 2
    assert updated.enabled is False


async def test_update_skill_regenerates_embedding_when_instructions_change(db) -> None:
    skill = await skills_service.create_skill(
        db,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=[],
        instructions="instrucao original",
        allowed_tools=[],
    )
    original_embedding = skill.embedding

    updated = await skills_service.update_skill(
        db,
        skill.skill_id,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=[],
        instructions="instrucao completamente diferente sobre outro assunto",
        allowed_tools=[],
        enabled=True,
    )

    assert updated.embedding != original_embedding


async def test_update_skill_keeps_embedding_when_instructions_and_keywords_unchanged(db) -> None:
    skill = await skills_service.create_skill(
        db,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=["a"],
        instructions="instrucao",
        allowed_tools=[],
    )
    original_embedding = skill.embedding

    updated = await skills_service.update_skill(
        db,
        skill.skill_id,
        name="Nome mudou mas nao instrucoes",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=["a"],
        instructions="instrucao",
        allowed_tools=[],
        enabled=True,
    )

    assert updated.embedding == original_embedding


async def test_update_skill_raises_when_not_found(db) -> None:
    with pytest.raises(skills_service.SkillNotFoundError):
        await skills_service.update_skill(
            db,
            "does-not-exist",
            name="X",
            description="Y",
            owning_agent="knowledge_agent",
            keywords=[],
            instructions="x",
            allowed_tools=[],
            enabled=True,
        )


async def test_delete_skill_removes_it(db) -> None:
    skill = await skills_service.create_skill(
        db,
        name="X",
        description="Y",
        owning_agent="knowledge_agent",
        keywords=[],
        instructions="x",
        allowed_tools=[],
    )
    deleted = await skills_service.delete_skill(db, skill.skill_id)
    assert deleted is True
    assert await SkillsRepository(db).get(skill.skill_id) is None


# --- Playbooks -------------------------------------------------------------------------------


async def test_create_playbook_computes_embedding(db) -> None:
    playbook = await playbooks_service.create_playbook(
        db,
        name="Problema X",
        objective="Resolver X",
        symptoms=["sintoma x"],
        prerequisites=[],
        steps=[{"step_id": "s1", "instruction": "faca isso", "tool": None}],
        decision_points=[],
        authorized_tools=[],
        exceptions=[],
        escalation_rules=[],
        success_criteria=[],
        closure_criteria=[],
    )
    assert playbook.embedding
    assert playbook.steps[0].step_id == "s1"


async def test_update_playbook_increments_version(db) -> None:
    playbook = await playbooks_service.create_playbook(
        db,
        name="P",
        objective="O",
        symptoms=["s"],
        prerequisites=[],
        steps=[],
        decision_points=[],
        authorized_tools=[],
        exceptions=[],
        escalation_rules=[],
        success_criteria=[],
        closure_criteria=[],
    )
    updated = await playbooks_service.update_playbook(
        db,
        playbook.playbook_id,
        name="P",
        objective="O",
        symptoms=["s"],
        prerequisites=[],
        steps=[],
        decision_points=[],
        authorized_tools=["new_tool"],
        exceptions=[],
        escalation_rules=[],
        success_criteria=[],
        closure_criteria=[],
        status="active",
    )
    assert int(updated.version) == int(playbook.version) + 1


async def test_update_playbook_raises_when_not_found(db) -> None:
    with pytest.raises(playbooks_service.PlaybookNotFoundError):
        await playbooks_service.update_playbook(
            db,
            "does-not-exist",
            name="P",
            objective="O",
            symptoms=[],
            prerequisites=[],
            steps=[],
            decision_points=[],
            authorized_tools=[],
            exceptions=[],
            escalation_rules=[],
            success_criteria=[],
            closure_criteria=[],
            status="active",
        )


async def test_delete_playbook_removes_it(db) -> None:
    playbook = await playbooks_service.create_playbook(
        db,
        name="P",
        objective="O",
        symptoms=[],
        prerequisites=[],
        steps=[],
        decision_points=[],
        authorized_tools=[],
        exceptions=[],
        escalation_rules=[],
        success_criteria=[],
        closure_criteria=[],
    )
    deleted = await playbooks_service.delete_playbook(db, playbook.playbook_id)
    assert deleted is True
    assert await PlaybooksRepository(db).get(playbook.playbook_id) is None


# --- Keywords --------------------------------------------------------------------------------


async def test_create_keyword_normalizes_term(db) -> None:
    keyword = await keywords_service.create_keyword(
        db,
        term="  Maquininha  ",
        synonyms=["POS"],
        weight=1.5,
        related_intents=["CUSTOMER_SUPPORT"],
        associated_documents=[],
        associated_skills=[],
        associated_playbooks=[],
    )
    assert keyword.normalized_term == "maquininha"


async def test_update_keyword_raises_when_not_found(db) -> None:
    with pytest.raises(keywords_service.KeywordNotFoundError):
        await keywords_service.update_keyword(
            db,
            "does-not-exist",
            term="x",
            synonyms=[],
            weight=1.0,
            related_intents=[],
            associated_documents=[],
            associated_skills=[],
            associated_playbooks=[],
        )


async def test_keyword_search_matches_synonyms(db) -> None:
    await keywords_service.create_keyword(
        db,
        term="maquininha",
        synonyms=["POS", "terminal de pagamento"],
        weight=1.0,
        related_intents=[],
        associated_documents=[],
        associated_skills=[],
        associated_playbooks=[],
    )
    results = await KeywordsRepository(db).search("terminal")
    assert len(results) == 1
