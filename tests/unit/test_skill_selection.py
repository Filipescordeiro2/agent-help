"""T110: teste unitario da selecao semantica de Skills respeitando `enabled=false`
(spec FR-025, US5 Acceptance Scenario 2)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.rag.entity_search as entity_search_module
from app.agent.skill_selection import format_skills_as_guidance, select_applicable_skills
from app.repository.skills_repository import Skill, SkillsRepository


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Vetor deterministico: 1.0 se a palavra 'conexao' aparece no texto, senao 0.0."""

    def _vector(text: str) -> list[float]:
        return [1.0, 0.0] if "conexao" in text.lower() else [0.0, 1.0]

    monkeypatch.setattr(entity_search_module, "embed_text", _vector)


async def _make_skill(
    db, *, name: str, owning_agent: str, enabled: bool, instructions: str
) -> Skill:
    skill = Skill(
        skill_id=name,
        name=name,
        description=name,
        owning_agent=owning_agent,
        instructions=instructions,
        enabled=enabled,
        embedding=[1.0, 0.0] if "conexao" in instructions.lower() else [0.0, 1.0],
    )
    return await SkillsRepository(db).insert(skill)


async def test_disabled_skill_is_never_selected(db) -> None:
    await _make_skill(
        db,
        name="skill_disabled",
        owning_agent="customer_support_agent",
        enabled=False,
        instructions="verifique a conexao wifi",
    )

    selected = await select_applicable_skills(
        db, owning_agent="customer_support_agent", query="problema de conexao", min_score=0.5
    )

    assert selected == []


async def test_enabled_matching_skill_is_selected(db) -> None:
    await _make_skill(
        db,
        name="skill_enabled",
        owning_agent="customer_support_agent",
        enabled=True,
        instructions="verifique a conexao wifi",
    )

    selected = await select_applicable_skills(
        db, owning_agent="customer_support_agent", query="problema de conexao", min_score=0.5
    )

    assert len(selected) == 1
    assert selected[0].skill_id == "skill_enabled"


async def test_skill_from_a_different_agent_is_never_selected(db) -> None:
    await _make_skill(
        db,
        name="skill_other_agent",
        owning_agent="knowledge_agent",
        enabled=True,
        instructions="verifique a conexao wifi",
    )

    selected = await select_applicable_skills(
        db, owning_agent="customer_support_agent", query="problema de conexao", min_score=0.5
    )

    assert selected == []


def test_format_skills_as_guidance_empty_list_returns_empty_string() -> None:
    assert format_skills_as_guidance([]) == ""


def test_format_skills_as_guidance_includes_skill_name_and_instructions() -> None:
    skill = Skill(
        skill_id="s1",
        name="Diagnostico",
        description="d",
        owning_agent="a",
        instructions="verifique o sinal",
        embedding=[],
    )
    guidance = format_skills_as_guidance([skill])
    assert "Diagnostico" in guidance
    assert "verifique o sinal" in guidance
