"""Escolha do procedimento (Playbook), das Skills e das Keywords para uma mensagem.

Como o agente decide "que tipo de solicitacao e essa":

0. **Playbook principal** (`always_apply`) -- sempre vale: descreve o fluxo comum e lista os
   sub-playbooks com QUANDO usar cada um.
1. **Keywords** -- termos e sinonimos do dominio (ex.: "pix", "estornar", "senha") encontrados na
   mensagem. Cada Keyword aponta para os Playbooks e Skills do assunto.
2. **Playbook do assunto** -- os apontados pelas Keywords (ou, sem Keyword, o mais parecido por
   similaridade). Somam-se ao Playbook geral (`always_apply`), que descreve o fluxo comum de
   qualquer duvida (entender -> base -> web -> responder).
3. **Skills** -- as do Playbook, as das Keywords, as `always_apply` (postura/tom) e a mais
   parecida por similaridade.

Sem nenhum Playbook de assunto o agente NAO fica sem rumo: entra o modo "sem playbook", em que
ele raciocina sobre tudo que conseguiu reunir (historico, base e paginas) e decide se da para
responder (ver `NO_PLAYBOOK_GUIDANCE`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.playbook_selection import format_playbook_block
from app.agent.skill_selection import format_skills_as_guidance, select_applicable_skills
from app.rag.entity_search import semantic_search_entities
from app.repository.keywords_repository import Keyword, KeywordsRepository
from app.repository.playbooks_repository import Playbook, PlaybooksRepository, PlaybookStatus
from app.repository.skills_repository import Skill, SkillsRepository

MODE_SPECIFIC = "playbook_especifico"
MODE_NO_PLAYBOOK = "sem_playbook"

# Similaridade minima para escolher um Playbook por semelhanca quando nenhuma Keyword bateu.
PLAYBOOK_MIN_SCORE = 0.55
MAX_TOPIC_PLAYBOOKS = 2

NO_PLAYBOOK_GUIDANCE = (
    "Nenhum sub-playbook cobre este assunto: siga so o Playbook principal e raciocine sozinho, "
    "com cuidado:\n"
    "1. Entenda o que o cliente quer e reuna o maximo de informacao: a mensagem, o historico da "
    "conversa e TODO o contexto recuperado (base e paginas) acima.\n"
    "2. Avalie se, com o que ha, DA para responder com seguranca. Se sim, responda usando so o "
    "contexto, citando o que a fonte diz e o que ela NAO especifica.\n"
    "3. Se a resposta depende de um dado que so o cliente sabe (modelo da maquininha, plano, "
    "modalidade, codigo de erro), faca UMA pergunta objetiva em vez de supor.\n"
    "4. Se o contexto nao cobre a duvida, nao invente: diga o que nao encontrou e oriente a "
    "central de atendimento."
)


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9+\s-]", " ", folded)


def _contains_term(normalized_text: str, term: str) -> bool:
    normalized_term = _normalize(term).strip()
    if not normalized_term:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?:s|es)?(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def match_keywords(message: str, keywords: list[Keyword]) -> list[Keyword]:
    """Keywords cujo termo ou sinonimo aparece na mensagem (maior peso primeiro)."""
    text = _normalize(message)
    matched = [k for k in keywords if any(_contains_term(text, t) for t in (k.term, *k.synonyms))]
    return sorted(matched, key=lambda k: -k.weight)


@dataclass
class TopicSelection:
    keywords: list[Keyword] = field(default_factory=list)
    general_playbooks: list[Playbook] = field(default_factory=list)  # principal(is)
    topic_playbooks: list[Playbook] = field(default_factory=list)  # sub-playbook(s) escolhido(s)
    sub_playbooks: list[Playbook] = field(default_factory=list)  # todos os sub-playbooks
    skills: list[Skill] = field(default_factory=list)
    topic_reason: str | None = None  # "keyword" | "similaridade" | None

    @property
    def mode(self) -> str:
        return MODE_SPECIFIC if self.topic_playbooks else MODE_NO_PLAYBOOK

    @property
    def playbooks(self) -> list[Playbook]:
        return [*self.general_playbooks, *self.topic_playbooks]

    def guidance(self) -> str:
        """Blocos do prompt: Playbook principal (com o mapa de sub-playbooks), o sub-playbook
        escolhido, Skills e, sem sub-playbook do assunto, o modo de raciocinio livre."""
        parts: list[str] = []
        for main in self.general_playbooks:
            block = format_playbook_block(main)
            if self.sub_playbooks:
                block += "\n\n" + self._sub_playbook_map()
            parts.append(block)
        if self.topic_playbooks:
            parts.append(
                "Sub-playbook escolhido para ESTA solicitacao (siga-o alem do principal):\n"
                + "\n\n".join(format_playbook_block(p) for p in self.topic_playbooks)
            )
        parts.append(format_skills_as_guidance(self.skills))
        if self.mode == MODE_NO_PLAYBOOK:
            parts.append(NO_PLAYBOOK_GUIDANCE)
        return "\n\n".join(p for p in parts if p)

    def _sub_playbook_map(self) -> str:
        chosen = {p.playbook_id for p in self.topic_playbooks}
        lines = ["Sub-playbooks (um procedimento por assunto) e quando usar cada um:"]
        for playbook in self.sub_playbooks:
            mark = " <- ESCOLHIDO" if playbook.playbook_id in chosen else ""
            when = playbook.metadata.get("when_to_use") or playbook.objective
            lines.append(f"- {playbook.name} [{playbook.playbook_id}]{mark}: {when}")
        return "\n".join(lines)

    def trace_details(self) -> dict:
        return {
            "keywords_matched": [k.term for k in self.keywords],
            "playbook_mode": self.mode,
            "playbook_reason": self.topic_reason,
            "playbooks_applied": [p.playbook_id for p in self.playbooks],
            "main_playbooks": [p.playbook_id for p in self.general_playbooks],
            "topic_playbooks": [p.playbook_id for p in self.topic_playbooks],
            "skills_applied": [s.skill_id for s in self.skills],
        }


async def select_topic(
    db: AsyncIOMotorDatabase, *, owning_agent: str, message: str
) -> TopicSelection:
    keywords = match_keywords(message, await KeywordsRepository(db).list(limit=1000))
    active = await PlaybooksRepository(db).list(
        filters={"status": PlaybookStatus.ACTIVE.value}, limit=200
    )
    mine = [p for p in active if p.metadata.get("owning_agent") == owning_agent]
    general = [p for p in mine if p.metadata.get("always_apply") is True]
    topical = [p for p in mine if p not in general]

    selected: list[Playbook] = []
    reason: str | None = None
    by_id = {p.playbook_id: p for p in topical}
    for keyword in keywords:  # ja em ordem de peso
        for playbook_id in keyword.associated_playbooks:
            playbook = by_id.get(playbook_id)
            if playbook is not None and playbook not in selected:
                selected.append(playbook)
    if selected:
        reason = "keyword"
    else:
        embedded = [p for p in topical if p.embedding]
        if embedded:
            found = semantic_search_entities(
                message,
                embedded,
                id_field="playbook_id",
                content_field="objective",
                metadata_fn=lambda p: {"name": p.name},
                top_k=1,
                min_score=PLAYBOOK_MIN_SCORE,
            )
            selected = [by_id[r.document_id] for r in found if r.document_id in by_id]
            reason = "similaridade" if selected else None
    selected = selected[:MAX_TOPIC_PLAYBOOKS]

    skills = await select_applicable_skills(db, owning_agent=owning_agent, query=message)
    skills_repo = SkillsRepository(db)
    wanted_ids: list[str] = []
    for playbook in selected:
        wanted_ids += playbook.metadata.get("related_skills", [])
    for keyword in keywords:
        wanted_ids += keyword.associated_skills
    known = {s.skill_id for s in skills}
    for skill_id in dict.fromkeys(wanted_ids):
        if skill_id in known:
            continue
        skill = await skills_repo.get(skill_id)
        if skill is not None and skill.enabled and skill.owning_agent == owning_agent:
            skills.append(skill)
            known.add(skill_id)

    return TopicSelection(
        keywords=keywords,
        general_playbooks=general,
        topic_playbooks=selected,
        sub_playbooks=topical,
        skills=skills,
        topic_reason=reason,
    )
