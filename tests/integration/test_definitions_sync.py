"""Boot: definicoes (YAML) -> MongoDB com embeddings; efeito no prompt do agente."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.agent.knowledge.agent as knowledge_agent_module
import app.main as app_main
from app.agent.definitions import sync
from app.agent.definitions.loader import DEFAULT_DIR
from app.agent.definitions.sync import SOURCE, embed_pending_if_possible, sync_definitions
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.agent.playbook_selection import format_playbooks_as_guidance, select_guidance_playbooks
from app.agent.skill_selection import select_applicable_skills
from app.config.settings import get_settings
from app.llm.credentials import reset_request_api_key, set_request_api_key
from app.repository.mongodb import client as mongodb_client
from app.repository.playbooks_repository import PlaybooksRepository
from app.schemas.router import Intent, RouterDecision
from app.services import playbooks as playbooks_service
from app.services import skills as skills_service


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "agent_definitions_enabled", True)
    sync.reset_state()
    yield
    sync.reset_state()


def _all(db, collection: str) -> list[dict]:
    return asyncio.run(db[collection].find({}).to_list(1000))


def _run(coro):
    return asyncio.run(coro)


# --- boot ----------------------------------------------------------------------------------------


def test_boot_saves_skills_playbooks_and_keywords_with_embeddings(test_db) -> None:
    report = _run(sync_definitions(test_db))

    skills, playbooks, keywords = (_all(test_db, c) for c in ("skills", "playbooks", "keywords"))
    assert (len(skills), len(playbooks), len(keywords)) == (11, 9, 22)
    web_sources = _all(test_db, "web_sources")
    assert report.created == 11 + 9 + 22 + 51 and report.pending_embeddings == 0
    assert len(web_sources) == 51
    # os tres sites principais + as paginas da Central de Ajuda
    urls = {s["url"] for s in web_sources}
    assert {
        "https://site.getnet.com.br/",
        "https://www.getnet.net/pt/",
        "https://docs.globalgetnet.com/pt/products/online-payments/regional-api/swagger#description/introduction",
        "https://site.getnet.com.br/get-ajuda-taxas/taxas-por-transacao/",
        "https://site.getnet.com.br/link-de-pagamento/",
    } <= urls
    assert all(s["origin"] == "default" and s["enabled"] and s["usage"] for s in web_sources)
    assert all(s["embedding"] and s["metadata"]["source"] == SOURCE for s in skills)
    assert all(p["embedding"] and p["metadata"]["source"] == SOURCE for p in playbooks)
    assert {k["normalized_term"] for k in keywords} >= {"taxa", "prazo", "maquininha", "pix"}
    assert all(k["keyword_id"].startswith("kw_") for k in keywords)


def test_running_the_boot_twice_is_idempotent_and_does_not_re_embed(test_db, monkeypatch) -> None:
    _run(sync_definitions(test_db))
    calls: list[str] = []
    real = skills_service.embed_text
    monkeypatch.setattr(skills_service, "embed_text", lambda t: calls.append(t) or real(t))

    report = _run(sync_definitions(test_db))

    assert (report.created, report.updated, report.unchanged) == (0, 0, 11 + 9 + 22 + 51)
    assert calls == []
    assert len(_all(test_db, "skills")) == 11 and len(_all(test_db, "keywords")) == 22


def test_changed_file_updates_the_record_bumps_the_version_and_re_embeds(
    test_db, tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "defs"
    shutil.copytree(DEFAULT_DIR, root, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    monkeypatch.setattr(get_settings(), "agent_definitions_dir", str(root))
    _run(sync_definitions(test_db))
    before = next(s for s in _all(test_db, "skills") if s["skill_id"] == "skill_taxas_e_prazos")

    path = root / "skills" / "taxas_e_prazos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n  - Novo criterio de teste.\n", encoding="utf-8"
    )
    report = _run(sync_definitions(test_db))

    after = next(s for s in _all(test_db, "skills") if s["skill_id"] == "skill_taxas_e_prazos")
    assert report.updated == 1 and report.unchanged == 11 + 9 + 22 + 51 - 1
    assert after["version"] == before["version"] + 1
    assert "Novo criterio de teste" in after["instructions"]
    assert after["embedding"] and after["embedding"] != before["embedding"]


def test_disabled_flag_writes_nothing(test_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "agent_definitions_enabled", False)

    _run(sync_definitions(test_db))

    assert all(not _all(test_db, c) for c in ("skills", "playbooks", "keywords"))


# --- chave do LLM so chega por requisicao ---------------------------------------------------------


def test_without_a_key_records_are_saved_pending_and_embedded_on_the_first_keyed_request(
    test_db, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "openrouter")
    monkeypatch.setattr(skills_service, "embed_text", lambda _t: [0.1] * 8)
    monkeypatch.setattr(playbooks_service, "embed_text", lambda _t: [0.2] * 8)

    report = _run(sync_definitions(test_db))  # boot sem chave

    assert report.pending_embeddings == 11 + 9 and report.embedded == 0
    assert all(not s["embedding"] for s in _all(test_db, "skills"))

    async def request_with_key() -> None:
        token = set_request_api_key("sk-or-teste")
        try:
            await embed_pending_if_possible(test_db)
        finally:
            reset_request_api_key(token)

    _run(request_with_key())

    assert all(s["embedding"] for s in _all(test_db, "skills"))
    assert all(p["embedding"] for p in _all(test_db, "playbooks"))


def test_a_request_without_key_does_not_try_to_embed(test_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "openrouter")
    _run(sync_definitions(test_db))
    monkeypatch.setattr(skills_service, "embed_text", lambda _t: pytest.fail("nao deveria embedar"))

    _run(embed_pending_if_possible(test_db))

    assert all(not s["embedding"] for s in _all(test_db, "skills"))


def test_embedding_failure_never_breaks_and_stays_pending(test_db, monkeypatch) -> None:
    def boom(_t):
        raise RuntimeError("provedor fora do ar")

    monkeypatch.setattr(skills_service, "embed_text", boom)  # provider fake: tenta embedar no boot

    report = _run(sync_definitions(test_db))

    assert report.embedded == 0 and report.pending_embeddings > 0
    assert len(_all(test_db, "skills")) == 11  # o conteudo foi salvo mesmo assim


# --- boot da aplicacao ---------------------------------------------------------------------------


def _boot(test_db, monkeypatch) -> None:
    async def _noop(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(app_main, "create_indexes", _noop)
    monkeypatch.setattr(mongodb_client, "connect", lambda: test_db)
    with TestClient(app_main.app) as client:
        assert client.get("/health").status_code == 200


def test_application_boot_loads_only_the_agent_definitions(test_db, monkeypatch) -> None:
    _boot(test_db, monkeypatch)

    names = _run(test_db.list_collection_names())
    populated = {n for n in names if _run(test_db[n].count_documents({}))}
    # so a configuracao do agente (nada de dados de exemplo)
    assert populated == {"skills", "playbooks", "keywords", "web_sources"}
    assert len(_all(test_db, "skills")) == 11


def test_invalid_definitions_never_stop_the_application(test_db, monkeypatch, tmp_path) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "ruim.yaml").write_text("id: sem-campos-obrigatorios", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "agent_definitions_dir", str(tmp_path))

    _boot(test_db, monkeypatch)  # sobe normalmente

    assert not _all(test_db, "skills")


# --- efeito no agente ----------------------------------------------------------------------------


def test_always_apply_skills_are_selected_without_similarity_and_scoped_by_agent(test_db) -> None:
    _run(sync_definitions(test_db))

    knowledge = _run(select_applicable_skills(test_db, owning_agent="knowledge_agent", query="zzz"))
    support = _run(
        select_applicable_skills(test_db, owning_agent="customer_support_agent", query="zzz")
    )

    assert {s.skill_id for s in knowledge} >= {
        "skill_atendimento_n1_duvidas",
        "skill_resposta_fundamentada",
    }
    assert [s.skill_id for s in support] == ["skill_atendimento_n1_suporte_tecnico"]


def test_doubts_playbook_belongs_to_the_knowledge_agent_only(test_db) -> None:
    _run(sync_definitions(test_db))

    guidance = _run(select_guidance_playbooks(test_db, owning_agent="knowledge_agent"))
    ids = {p.playbook_id for p in guidance}
    assert "pb_atendimento_duvidas_n1" in ids and len(ids) == 9  # principal + 8 sub-playbooks
    main = next(p for p in guidance if p.metadata.get("always_apply"))
    assert main.playbook_id == "pb_atendimento_duvidas_n1"
    subs = [p for p in guidance if p.metadata.get("parent") == main.playbook_id]
    assert len(subs) == 8 and all(p.metadata.get("when_to_use") for p in subs)
    assert _run(select_guidance_playbooks(test_db, owning_agent="customer_support_agent")) == []
    # o Customer Support Agent nao o escolhe, mesmo que a mensagem case com os sintomas dele
    assert (
        _run(PlaybooksRepository(test_db).find_matching_symptom("tenho uma duvida, como funciona?"))
        is None
    )

    text = format_playbooks_as_guidance([main])
    assert "consultar_base [ferramenta: search_knowledge]" in text
    assert "consultar_web [ferramenta: search_web_pages]" in text


def test_knowledge_agent_prompt_carries_the_skills_and_the_playbook(
    client: TestClient, fake_llm: dict, test_db, monkeypatch
) -> None:
    _run(sync_definitions(test_db))
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": "A taxa da maquininha e 1,99%."},
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="Q",
    )
    prompts: list[str] = []
    original = knowledge_agent_module.get_structured_output

    async def spy(schema, messages, **kwargs):
        if schema is KnowledgeAnswerDraft:
            prompts.append(messages[0]["content"])
            return KnowledgeAnswerDraft(message="1,99%", grounded_in_sources=True)
        return await original(schema, messages, **kwargs)

    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", spy)
    sid = client.post("/api/v1/sessions", headers={"X-User-Id": "u"}).json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers={"X-User-Id": "u"},
        json={"message": "Qual a taxa da maquininha?"},
    ).json()

    assert body["status"] == "OK"
    prompt = prompts[0]
    assert "suporte N1 (atendimento) da Getnet" in prompt  # skill always_apply
    assert "Resposta fundamentada" in prompt or "reformulacao direta da fonte" in prompt
    assert "Procedimento de atendimento (Playbook 'Atendimento N1 - duvidas de clientes')" in prompt
    assert "consultar_web [ferramenta: search_web_pages]" in prompt
