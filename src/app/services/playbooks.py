"""Servico de Playbooks -- CRUD, versionamento e regeracao de embedding (spec FR-027 a FR-029)."""

from __future__ import annotations

import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.llm.embeddings_client import embed_text
from app.repository.playbooks_repository import (
    Playbook,
    PlaybooksRepository,
    PlaybookStatus,
    PlaybookStep,
)


class PlaybookNotFoundError(Exception):
    pass


def _embedding_text(name: str, objective: str, symptoms: list[str]) -> str:
    return " ".join([name, objective, *symptoms])


async def create_playbook(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    objective: str,
    symptoms: list[str],
    prerequisites: list[str],
    steps: list[dict],
    decision_points: list[dict],
    authorized_tools: list[str],
    exceptions: list[str],
    escalation_rules: list[str],
    success_criteria: list[str],
    closure_criteria: list[str],
    status: str = PlaybookStatus.ACTIVE.value,
    metadata: dict | None = None,
) -> Playbook:
    playbook = Playbook(
        playbook_id=str(uuid.uuid4()),
        name=name,
        objective=objective,
        symptoms=symptoms,
        prerequisites=prerequisites,
        steps=[PlaybookStep(**s) for s in steps],
        decision_points=decision_points,
        authorized_tools=authorized_tools,
        exceptions=exceptions,
        escalation_rules=escalation_rules,
        success_criteria=success_criteria,
        closure_criteria=closure_criteria,
        status=PlaybookStatus(status),
        metadata=metadata or {},
        embedding=embed_text(_embedding_text(name, objective, symptoms)),
    )
    return await PlaybooksRepository(db).insert(playbook)


async def update_playbook(
    db: AsyncIOMotorDatabase,
    playbook_id: str,
    *,
    name: str,
    objective: str,
    symptoms: list[str],
    prerequisites: list[str],
    steps: list[dict],
    decision_points: list[dict],
    authorized_tools: list[str],
    exceptions: list[str],
    escalation_rules: list[str],
    success_criteria: list[str],
    closure_criteria: list[str],
    status: str,
    metadata: dict | None = None,
) -> Playbook:
    repo = PlaybooksRepository(db)
    existing = await repo.get(playbook_id)
    if existing is None:
        raise PlaybookNotFoundError(playbook_id)

    content_changed = symptoms != existing.symptoms or objective != existing.objective
    embedding = (
        embed_text(_embedding_text(name, objective, symptoms))
        if content_changed
        else existing.embedding
    )

    updated = await repo.update(
        playbook_id,
        {
            "name": name,
            "objective": objective,
            "symptoms": symptoms,
            "prerequisites": prerequisites,
            "steps": [PlaybookStep(**s).model_dump() for s in steps],
            "decision_points": decision_points,
            "authorized_tools": authorized_tools,
            "exceptions": exceptions,
            "escalation_rules": escalation_rules,
            "success_criteria": success_criteria,
            "closure_criteria": closure_criteria,
            "status": status,
            "metadata": metadata or {},
            "embedding": embedding,
            "version": str(int(existing.version) + 1),
        },
    )
    assert updated is not None
    return updated


async def regenerate_embedding(db: AsyncIOMotorDatabase, playbook_id: str) -> Playbook:
    repo = PlaybooksRepository(db)
    playbook = await repo.get(playbook_id)
    if playbook is None:
        raise PlaybookNotFoundError(playbook_id)
    embedding = embed_text(_embedding_text(playbook.name, playbook.objective, playbook.symptoms))
    updated = await repo.update(playbook_id, {"embedding": embedding})
    assert updated is not None
    return updated


async def delete_playbook(db: AsyncIOMotorDatabase, playbook_id: str) -> bool:
    return await PlaybooksRepository(db).delete(playbook_id)
