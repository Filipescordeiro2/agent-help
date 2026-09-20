"""Sincroniza as definicoes (arquivos YAML) com o MongoDB no boot: grava e gera embeddings.

- **Idempotente**: cada registro guarda `metadata.content_hash`; se o arquivo nao mudou, nada e
  reescrito (edicoes feitas pela API ficam ate o arquivo mudar). Arquivo alterado => registro
  atualizado, versao incrementada e embedding regerado.
- Skills e Playbooks recebem embedding (para as buscas semanticas). Keywords nao tem embedding.
- **Chave do LLM por requisicao**: no boot geralmente nao ha chave (ela vem no cabecalho
  `X-API-Key-LLM`). Nesse caso os registros sao gravados SEM embedding e ficam pendentes; o primeiro
  request que trouxer a chave completa os embeddings (`embed_pending_if_possible`). No modo fake
  (ou com `OPENROUTER_API_KEY` no ambiente) os embeddings saem ja no boot.
- Nunca derruba a aplicacao: falhas de embedding sao registradas e tentadas de novo depois.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.definitions.loader import (
    Definitions,
    KeywordDef,
    PlaybookDef,
    SkillDef,
    WebSourceDef,
    content_hash,
    load_definitions,
)
from app.config.settings import get_settings
from app.llm.credentials import has_api_key
from app.repository.keywords_repository import Keyword, KeywordsRepository, normalize_term
from app.repository.playbooks_repository import (
    Playbook,
    PlaybooksRepository,
    PlaybookStatus,
    PlaybookStep,
)
from app.repository.skills_repository import Skill, SkillsRepository
from app.repository.web_sources_repository import (
    WebSource,
    WebSourcesRepository,
    normalize_url,
)
from app.services import playbooks as playbooks_service
from app.services import skills as skills_service

logger = structlog.get_logger(__name__)

SOURCE = "definitions"
_RETRY_COOLDOWN_SECONDS = 60.0

_pending_embeddings = False
_last_failure_at = 0.0
_embed_lock: asyncio.Lock | None = None


@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    embedded: int = 0
    pending_embeddings: int = 0


def _lock() -> asyncio.Lock:
    global _embed_lock
    if _embed_lock is None:
        _embed_lock = asyncio.Lock()
    return _embed_lock


def reset_state() -> None:
    """Limpa o estado em memoria (usado pelos testes)."""
    global _pending_embeddings, _last_failure_at, _embed_lock
    _pending_embeddings = False
    _last_failure_at = 0.0
    _embed_lock = None


def _skill_metadata(definition: SkillDef, digest: str) -> dict:
    return {
        "source": SOURCE,
        "content_hash": digest,
        "definition_id": definition.id,
        "always_apply": definition.always_apply,
    }


async def _sync_skill(db: AsyncIOMotorDatabase, definition: SkillDef, report: SyncReport) -> None:
    repo = SkillsRepository(db)
    digest = content_hash(definition)
    existing = await repo.get(definition.id)
    if existing is not None and existing.metadata.get("content_hash") == digest:
        report.unchanged += 1
        return
    fields = {
        "name": definition.name,
        "description": definition.description,
        "owning_agent": definition.owning_agent,
        "keywords": definition.keywords,
        "instructions": definition.instructions,
        "allowed_tools": definition.allowed_tools,
        "enabled": definition.enabled,
        "metadata": _skill_metadata(definition, digest),
    }
    if existing is None:
        await repo.insert(Skill(skill_id=definition.id, **fields))
        report.created += 1
    else:
        await repo.update(
            definition.id, {**fields, "embedding": [], "version": existing.version + 1}
        )
        report.updated += 1


async def _sync_playbook(
    db: AsyncIOMotorDatabase, definition: PlaybookDef, report: SyncReport
) -> None:
    repo = PlaybooksRepository(db)
    digest = content_hash(definition)
    existing = await repo.get(definition.id)
    if existing is not None and existing.metadata.get("content_hash") == digest:
        report.unchanged += 1
        return
    fields = {
        "name": definition.name,
        "objective": definition.objective,
        "symptoms": definition.symptoms,
        "prerequisites": definition.prerequisites,
        "steps": [PlaybookStep(**s.model_dump()) for s in definition.steps],
        "decision_points": definition.decision_points,
        "authorized_tools": definition.authorized_tools,
        "exceptions": definition.exceptions,
        "escalation_rules": definition.escalation_rules,
        "success_criteria": definition.success_criteria,
        "closure_criteria": definition.closure_criteria,
        "status": PlaybookStatus(definition.status),
        "metadata": {
            "source": SOURCE,
            "content_hash": digest,
            "definition_id": definition.id,
            "owning_agent": definition.owning_agent,
            "always_apply": definition.always_apply,
            "parent": definition.parent,
            "when_to_use": definition.when_to_use,
            "related_skills": definition.skills,
        },
    }
    if existing is None:
        await repo.insert(Playbook(playbook_id=definition.id, **fields))
        report.created += 1
    else:
        updates = {
            **fields,
            "steps": [s.model_dump() for s in fields["steps"]],
            "status": definition.status,
            "embedding": [],
            "version": str(int(existing.version) + 1),
        }
        await repo.update(definition.id, updates)
        report.updated += 1


async def _sync_keyword(
    db: AsyncIOMotorDatabase, definition: KeywordDef, report: SyncReport
) -> None:
    repo = KeywordsRepository(db)
    fields = {
        "term": definition.term,
        "normalized_term": normalize_term(definition.term),
        "synonyms": definition.synonyms,
        "weight": definition.weight,
        "related_intents": definition.related_intents,
        "associated_documents": definition.associated_documents,
        "associated_skills": definition.associated_skills,
        "associated_playbooks": definition.associated_playbooks,
    }
    existing = await repo.get(definition.id)
    if existing is None:
        await repo.insert(Keyword(keyword_id=definition.id, **fields))
        report.created += 1
    elif any(getattr(existing, key) != value for key, value in fields.items()):
        await repo.update(definition.id, fields)
        report.updated += 1
    else:
        report.unchanged += 1


async def _sync_web_source(
    db: AsyncIOMotorDatabase, definition: WebSourceDef, report: SyncReport
) -> None:
    repo = WebSourcesRepository(db)
    digest = content_hash(definition)
    existing = await repo.get(definition.id)
    if existing is not None and existing.metadata.get("content_hash") == digest:
        report.unchanged += 1
        return
    fields = {
        "name": definition.name,
        "url": definition.url,
        "normalized_url": normalize_url(definition.url),
        "description": definition.description,
        "usage": definition.usage,
        "topics": definition.topics,
        "priority": definition.priority,
        "enabled": definition.enabled,
        "origin": "default",
        "metadata": {"source": SOURCE, "content_hash": digest},
    }
    if existing is None:
        await repo.insert(WebSource(source_id=definition.id, **fields))
        report.created += 1
    else:
        await repo.update(definition.id, fields)
        report.updated += 1


def _can_embed() -> bool:
    return get_settings().llm_provider == "fake" or has_api_key()


async def _missing_embeddings(db: AsyncIOMotorDatabase) -> tuple[list[str], list[str]]:
    skills = await SkillsRepository(db).list(
        filters={"metadata.source": SOURCE, "embedding": []}, limit=1000
    )
    playbooks = await PlaybooksRepository(db).list(
        filters={"metadata.source": SOURCE, "embedding": []}, limit=1000
    )
    return [s.skill_id for s in skills], [p.playbook_id for p in playbooks]


async def embed_missing(db: AsyncIOMotorDatabase) -> tuple[int, int]:
    """Gera os embeddings que faltam. Retorna (gerados, ainda_pendentes)."""
    global _pending_embeddings, _last_failure_at
    skill_ids, playbook_ids = await _missing_embeddings(db)
    total = len(skill_ids) + len(playbook_ids)
    if total == 0:
        _pending_embeddings = False
        return 0, 0
    if not _can_embed():
        _pending_embeddings = True
        return 0, total

    done = 0
    try:
        # Mesmo laco de eventos do Motor (nada de threads); o embedding e uma chamada HTTP curta.
        for skill_id in skill_ids:
            await skills_service.regenerate_embedding(db, skill_id)
            done += 1
        for playbook_id in playbook_ids:
            await playbooks_service.regenerate_embedding(db, playbook_id)
            done += 1
    except Exception as exc:  # noqa: BLE001 -- nunca derruba o boot/atendimento
        _pending_embeddings = True
        _last_failure_at = time.monotonic()
        logger.warning("definitions_embedding_failed", error_type=type(exc).__name__)
        return done, total - done
    _pending_embeddings = False
    return done, 0


async def sync_definitions(db: AsyncIOMotorDatabase) -> SyncReport:
    """Boot: grava/atualiza Skills, Playbooks e Keywords e gera os embeddings possiveis."""
    settings = get_settings()
    report = SyncReport()
    if not settings.agent_definitions_enabled:
        logger.info("agent_definitions_disabled")
        return report

    definitions: Definitions = load_definitions()
    for skill in definitions.skills:
        await _sync_skill(db, skill, report)
    for playbook in definitions.playbooks:
        await _sync_playbook(db, playbook, report)
    for keyword in definitions.keywords:
        await _sync_keyword(db, keyword, report)
    for web_source in definitions.web_sources:
        await _sync_web_source(db, web_source, report)

    report.embedded, report.pending_embeddings = await embed_missing(db)
    logger.info(
        "agent_definitions_synced",
        created=report.created,
        updated=report.updated,
        unchanged=report.unchanged,
        embedded=report.embedded,
        pending_embeddings=report.pending_embeddings,
    )
    return report


async def embed_pending_if_possible(db: AsyncIOMotorDatabase) -> None:
    """Quando uma requisicao traz a chave do LLM, completa os embeddings pendentes do boot."""
    if not _pending_embeddings or not _can_embed():
        return
    if _last_failure_at and time.monotonic() - _last_failure_at < _RETRY_COOLDOWN_SECONDS:
        return
    async with _lock():
        if not _pending_embeddings:
            return
        await embed_missing(db)
