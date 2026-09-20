"""Arquivos de definicao do agente (Skills, Playbooks, Keywords): validos e coerentes entre si."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.agent.definitions.loader import DEFAULT_DIR, DefinitionsError, load_definitions

SKILL = {
    "id": "s1",
    "name": "S",
    "description": "d",
    "owning_agent": "knowledge_agent",
    "instructions": "faca x",
}
PLAYBOOK = {
    "id": "p1",
    "name": "P",
    "objective": "o",
    "authorized_tools": ["search_knowledge"],
    "steps": [
        {"step_id": "a", "instruction": "i", "tool": "search_knowledge", "on_success": "b"},
        {"step_id": "b", "instruction": "j", "on_success": "closed", "on_failure": "escalate"},
    ],
}
KEYWORDS = {"keywords": [{"id": "k1", "term": "Taxa", "associated_skills": ["s1"]}]}


def _write(root: Path, skill=SKILL, playbook=PLAYBOOK, keywords=KEYWORDS) -> Path:
    for folder, payload, name in (
        ("skills", skill, "s.yaml"),
        ("playbooks", playbook, "p.yaml"),
        ("keywords", keywords, "k.yaml"),
    ):
        (root / folder).mkdir(parents=True, exist_ok=True)
        if payload is not None:
            (root / folder / name).write_text(yaml.safe_dump(payload), encoding="utf-8")
    return root


def _problems(root: Path) -> list[str]:
    with pytest.raises(DefinitionsError) as exc:
        load_definitions(root)
    return exc.value.problems


# --- os arquivos empacotados ---------------------------------------------------------------------


def test_shipped_definitions_are_valid_and_cover_the_n1_agent() -> None:
    defs = load_definitions(DEFAULT_DIR)

    assert {s.owning_agent for s in defs.skills} == {"knowledge_agent", "customer_support_agent"}
    assert {"skill_atendimento_n1_duvidas", "skill_resposta_fundamentada"} <= {
        s.id for s in defs.skills if s.always_apply
    }
    assert len(defs.keywords) >= 10
    assert all(s.instructions.strip() for s in defs.skills)


def test_shipped_doubts_playbook_follows_understand_base_web_answer() -> None:
    playbook = next(
        p for p in load_definitions(DEFAULT_DIR).playbooks if p.id == "pb_atendimento_duvidas_n1"
    )

    assert playbook.owning_agent == "knowledge_agent"
    order = [s.step_id for s in playbook.steps]
    assert (
        order.index("entender_duvida")
        < order.index("consultar_base")
        < order.index("consultar_web")
    )
    assert order.index("consultar_web") < order.index("responder")
    by_id = {s.step_id: s for s in playbook.steps}
    # base responde -> responde direto; base nao responde -> vai a web; web falha -> escala
    assert by_id["consultar_base"].tool == "search_knowledge"
    assert (by_id["consultar_base"].on_success, by_id["consultar_base"].on_failure) == (
        "responder",
        "consultar_web",
    )
    assert by_id["consultar_web"].tool == "search_web_pages"
    assert (by_id["consultar_web"].on_success, by_id["consultar_web"].on_failure) == (
        "responder",
        "escalar",
    )


def test_shipped_files_are_utf8_and_yaml_only() -> None:
    files = [p for p in DEFAULT_DIR.rglob("*") if p.is_file() and p.suffix in {".yaml", ".yml"}]
    assert len(files) >= 6
    for path in files:
        path.read_text(encoding="utf-8")


# --- validacao -----------------------------------------------------------------------------------


def test_a_consistent_set_loads(tmp_path: Path) -> None:
    defs = load_definitions(_write(tmp_path))
    assert [s.id for s in defs.skills] == ["s1"] and len(defs.playbooks) == 1


def test_missing_required_field_is_reported_with_the_file_name(tmp_path: Path) -> None:
    bad = {k: v for k, v in SKILL.items() if k != "instructions"}
    problems = _problems(_write(tmp_path, skill=bad))
    assert any("s.yaml" in p and "instructions" in p for p in problems)


def test_playbook_step_pointing_to_a_missing_step_is_rejected(tmp_path: Path) -> None:
    broken = {
        **PLAYBOOK,
        "steps": [{"step_id": "a", "instruction": "i", "on_success": "nao_existe"}],
    }
    assert any("nao_existe" in p for p in _problems(_write(tmp_path, playbook=broken)))


def test_unknown_or_unauthorized_tools_are_rejected(tmp_path: Path) -> None:
    unknown = {**PLAYBOOK, "authorized_tools": ["ferramenta_fantasma"]}
    assert any("ferramenta_fantasma" in p for p in _problems(_write(tmp_path, playbook=unknown)))

    unauthorized = {**PLAYBOOK, "authorized_tools": []}
    assert any(
        "nao esta em authorized_tools" in p
        for p in _problems(_write(tmp_path, playbook=unauthorized))
    )


def test_keyword_pointing_to_a_missing_skill_or_playbook_is_rejected(tmp_path: Path) -> None:
    kw = {
        "keywords": [
            {"id": "k1", "term": "x", "associated_skills": ["sX"], "associated_playbooks": ["pX"]}
        ]
    }
    problems = _problems(_write(tmp_path, keywords=kw))
    assert any("sX" in p for p in problems) and any("pX" in p for p in problems)


def test_duplicate_ids_and_terms_are_rejected(tmp_path: Path) -> None:
    root = _write(tmp_path)
    (root / "skills" / "s2.yaml").write_text(yaml.safe_dump(SKILL), encoding="utf-8")
    kw = {"keywords": [{"id": "k1", "term": "Taxa"}, {"id": "k2", "term": " taxa "}]}
    (root / "keywords" / "k.yaml").write_text(yaml.safe_dump(kw), encoding="utf-8")

    problems = _problems(root)

    assert any("id duplicado 's1'" in p for p in problems)
    assert any("term duplicado" in p for p in problems)


def test_all_problems_are_reported_at_once(tmp_path: Path) -> None:
    broken_skill = {k: v for k, v in SKILL.items() if k != "name"}
    broken_pb = {**PLAYBOOK, "authorized_tools": ["x"]}
    assert len(_problems(_write(tmp_path, skill=broken_skill, playbook=broken_pb))) >= 3


def test_malformed_yaml_is_reported(tmp_path: Path) -> None:
    root = _write(tmp_path)
    (root / "skills" / "s.yaml").write_text("id: [nao fechado", encoding="utf-8")
    assert any("s.yaml" in p for p in _problems(root))


# URLs da Central de Ajuda enviadas pelo cliente (com repeticoes e ancoras) -- todas precisam estar
# cadastradas como fontes web padrao do agente.
HELP_URLS = """
https://site.getnet.com.br/link-de-pagamento/
https://site.getnet.com.br/get-ajuda-taxas/taxas-por-transacao/
https://site.getnet.com.br/get-ajuda-configuracoes/como-configurar-o-wi-fi-na-sua-maquininha-getnet/
https://site.getnet.com.br/get-ajuda-configuracoes/como-consultar-minhas-vendas/
https://site.getnet.com.br/get-ajuda-configuracoes/configuracoes-da-sua-maquininha/
https://site.getnet.com.br/get-ajuda-cadastro/como-trocar-o-domicilio-bancario/
https://site.getnet.com.br/get-ajuda-estorno/como-estornar-uma-venda-na-maquininha-getnet/
https://site.getnet.com.br/get-ajuda-estorno/como-estornar-uma-venda-pelo-aplicativo-getnet-brasil/
https://site.getnet.com.br/get-ajuda-estorno/o-que-e-chargeback/
https://site.getnet.com.br/get-ajuda-cadastro/como-atualizar-seus-dados-cadastrais-no-aplicativo-getnet-brasil/
https://site.getnet.com.br/get-ajuda-cadastro/como-finalizar-seu-cadastro-no-aplicativo-getnet-brasil-e-comecar-a-aceitar-pagamentos/
https://site.getnet.com.br/get-ajuda-cadastro/como-redefinir-a-senha-aplicativo/
https://site.getnet.com.br/get-ajuda-pix/como-habilitar-o-recebimento-de-vendas-via-pix-no-aplicativo-getnet-brasil/
https://site.getnet.com.br/get-ajuda-pix/como-fazer-uma-venda-com-pix-na-maquininha/
https://site.getnet.com.br/get-ajuda-pix/como-utilizo-o-pix-por-biometria/
https://site.getnet.com.br/get-ajuda-pix/como-habilitar-pix-pelo-portal/
https://site.getnet.com.br/get-ajuda-get-tap/como-habilitar-e-vender-com-pix-no-get-tap/
https://site.getnet.com.br/get-ajuda-maquininha/como-fazer-uma-venda-na-maquininha-getnet/
https://site.getnet.com.br/get-ajuda-erros-maquininha/
https://site.getnet.com.br/get-ajuda-maquininha/como-pedir-uma-maquininha-adicional/
https://site.getnet.com.br/get-ajuda-maquininha/adquirindo-nossos-produtos-e-servicos/
https://site.getnet.com.br/get-ajuda-maquininha/solucoes-get-smart/
https://site.getnet.com.br/get-ajuda-maquininha/como-acessa-o-manual-da-sua-maquininha-getnet/
https://site.getnet.com.br/get-ajuda-maquininha/simulador-calculadora-de-vendas/
https://site.getnet.com.br/get-ajuda-maquininha/como-solicitar-a-manutencao-da-maquininha-getnet/
https://site.getnet.com.br/get-ajuda-receba-ja/plano-de-recebimento-reduzido/#o-que-e-o-plano-de-recebimento-reduzido
https://site.getnet.com.br/get-ajuda-receba-ja/plano-de-recebimento-reduzido/#como-contratar
https://site.getnet.com.br/get-ajuda-receba-ja/plano-de-recebimento-reduzido/#o-que-acontece-se-eu-cancelar-o-plano
https://www.getnet.net/pt/
https://docs.globalgetnet.com/pt/products/online-payments/regional-api/swagger#description/introduction
https://site.getnet.com.br/
""".split()


def test_every_help_url_sent_by_the_customer_is_a_default_web_source() -> None:
    from app.repository.web_sources_repository import normalize_url

    registered = {normalize_url(s.url) for s in load_definitions(DEFAULT_DIR).web_sources}
    missing = sorted({normalize_url(u) for u in HELP_URLS} - registered)
    assert missing == []


def test_every_web_source_says_what_it_is_when_to_use_it_and_its_topics() -> None:
    for source in load_definitions(DEFAULT_DIR).web_sources:
        assert len(source.description) > 40 and len(source.usage) > 20, source.id
        assert len(source.topics) >= 3, source.id
        assert source.url.startswith("https://"), source.id
