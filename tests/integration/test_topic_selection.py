"""Escolha do sub-playbook por Keywords, playbook principal e modo sem playbook."""

from __future__ import annotations

import asyncio

import pytest

from app.agent import topic_selection
from app.agent.definitions import sync
from app.agent.definitions.sync import sync_definitions
from app.agent.topic_selection import MODE_NO_PLAYBOOK, MODE_SPECIFIC, match_keywords, select_topic
from app.config.settings import get_settings
from app.repository.keywords_repository import Keyword


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "agent_definitions_enabled", True)
    sync.reset_state()
    yield
    sync.reset_state()


def _kw(term: str, synonyms: list[str], weight: float = 1.0) -> Keyword:
    return Keyword(
        keyword_id=term, term=term, normalized_term=term, synonyms=synonyms, weight=weight
    )


def test_keywords_match_terms_synonyms_accents_and_plurals_by_weight() -> None:
    keywords = [_kw("pix", ["qr code"], 0.9), _kw("estorno", ["estornar"], 1.3), _kw("taxa", [])]

    matched = match_keywords("Como ESTORNAR uma venda? Aceito Pix e QR Code", keywords)

    assert [k.term for k in matched] == ["estorno", "pix"]  # maior peso primeiro
    assert match_keywords("quais as taxas?", keywords)[0].term == "taxa"  # plural
    assert match_keywords("pixel art", keywords) == []  # palavra inteira


def _run(coro):
    return asyncio.run(coro)


def test_pix_question_gets_main_playbook_plus_the_pix_sub_playbook(test_db) -> None:
    _run(sync_definitions(test_db))

    topic = _run(
        select_topic(test_db, owning_agent="knowledge_agent", message="Como habilito o Pix?")
    )

    assert topic.mode == MODE_SPECIFIC and topic.topic_reason == "keyword"
    assert [p.playbook_id for p in topic.general_playbooks] == ["pb_atendimento_duvidas_n1"]
    assert [p.playbook_id for p in topic.topic_playbooks] == ["pb_pix"]
    assert "skill_pix" in {s.skill_id for s in topic.skills}  # skill do sub-playbook/keyword
    guidance = topic.guidance()
    assert "Sub-playbooks (um procedimento por assunto) e quando usar cada um" in guidance
    assert "[pb_pix] <- ESCOLHIDO" in guidance and "Sub-playbook escolhido" in guidance


def test_error_code_question_uses_the_error_sub_playbook(test_db) -> None:
    _run(sync_definitions(test_db))

    topic = _run(select_topic(test_db, owning_agent="knowledge_agent", message="deu erro ADQ 1-87"))

    assert [p.playbook_id for p in topic.topic_playbooks][0] == "pb_erros_da_maquininha"


def test_unmapped_subject_falls_back_to_reasoning_without_a_sub_playbook(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Os embeddings do provedor fake nao tem semantica: desliga a escolha por similaridade.
    monkeypatch.setattr(topic_selection, "PLAYBOOK_MIN_SCORE", 1.1)
    _run(sync_definitions(test_db))

    topic = _run(select_topic(test_db, owning_agent="knowledge_agent", message="Tem cashback?"))

    assert topic.mode == MODE_NO_PLAYBOOK and topic.topic_playbooks == []
    assert [p.playbook_id for p in topic.general_playbooks] == ["pb_atendimento_duvidas_n1"]
    assert "Nenhum sub-playbook cobre este assunto" in topic.guidance()
    assert topic.trace_details()["playbook_mode"] == "sem_playbook"
