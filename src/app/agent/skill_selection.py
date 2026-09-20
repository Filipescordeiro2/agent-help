"""Selecao semantica de Skills aplicaveis a uma mensagem, respeitando `enabled=false`
(spec FR-025, US5 Acceptance Scenario 2) -- reutilizado pelo Knowledge e Customer Support
Agent para incorporar instrucoes de Skills relevantes sem alteracao de codigo."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.rag.entity_search import semantic_search_entities
from app.repository.skills_repository import Skill, SkillsRepository

DEFAULT_SKILL_MIN_SCORE = 0.5
DEFAULT_SKILL_TOP_K = 1


async def select_applicable_skills(
    db: AsyncIOMotorDatabase,
    *,
    owning_agent: str,
    query: str,
    top_k: int = DEFAULT_SKILL_TOP_K,
    min_score: float = DEFAULT_SKILL_MIN_SCORE,
) -> list[Skill]:
    # list_enabled() ja filtra enabled=True na consulta -- uma Skill desabilitada nunca chega
    # a ser comparada/selecionada (US5 Acceptance Scenario 2).
    enabled_skills = await SkillsRepository(db).list_enabled()
    candidates = [s for s in enabled_skills if s.owning_agent == owning_agent]
    if not candidates:
        return []

    # Skills `always_apply` (postura/tom do atendimento) entram sempre, sem depender de
    # similaridade; as demais sao escolhidas por busca semantica.
    always = [s for s in candidates if s.metadata.get("always_apply") is True]
    candidates = [s for s in candidates if s not in always]
    if not candidates:
        return always

    results = semantic_search_entities(
        query,
        candidates,
        id_field="skill_id",
        content_field="instructions",
        metadata_fn=lambda s: {"name": s.name},
        top_k=top_k,
        min_score=min_score,
    )
    by_id = {s.skill_id: s for s in candidates}
    return [*always, *(by_id[r.document_id] for r in results if r.document_id in by_id)]


def format_skills_as_guidance(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [f"- [{s.name}] {s.instructions}" for s in skills]
    return "Orientacoes de Skills aplicaveis a esta solicitacao:\n" + "\n".join(lines)
