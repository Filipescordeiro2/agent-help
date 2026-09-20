"""Seguir links do mesmo site: extracao, filtro e selecao por relevancia a pergunta."""

from __future__ import annotations

import pytest

from app.rag.loaders.web import (
    WebPage,
    extract_page,
    same_site_links,
    select_relevant_links,
    tokens,
)
from app.rag.web_context import rank_sources
from app.repository.web_sources_repository import WebSource, normalize_url

BASE = "https://www.exemplo.com/pt/"


def test_extract_page_collects_anchor_text_and_href() -> None:
    html = (
        "<title>T</title><nav><a href='/menu'>Menu</a></nav>"
        "<p>Veja <a href='/pt/taxas'>Taxas da <b>maquininha</b></a> e "
        "<a href='https://outro.com/x'>Outro</a>.</p>"
    )

    title, text, links = extract_page("text/html", html)

    assert title == "T" and "Veja" in text
    hrefs = dict(links)
    assert hrefs["/pt/taxas"] == "Taxas da maquininha"
    assert "https://outro.com/x" in hrefs


def test_same_site_links_keeps_only_absolute_same_host_http_links() -> None:
    raw = [
        ("/pt/taxas#topo", "Taxas"),
        ("https://www.exemplo.com/pt/planos", "Planos"),
        ("https://outro.com/x", "Fora"),
        ("mailto:a@b.com", "Email"),
        ("javascript:void(0)", "JS"),
        ("#secao", "Ancora"),
        ("/pt/taxas", "Taxas de novo"),  # duplicada
        ("https://www.exemplo.com/pt/", "Home"),  # a propria pagina
        ("ftp://www.exemplo.com/a", "FTP"),
    ]

    links = same_site_links(BASE, raw)

    assert [url for url, _ in links] == [
        "https://www.exemplo.com/pt/taxas",
        "https://www.exemplo.com/pt/planos",
    ]


def test_tokens_fold_accents_and_drop_stopwords() -> None:
    assert tokens("Qual a taxa da Máquininha? Como funciona!") >= {"taxa", "funcio"}
    # conjugacoes e derivadas casam pela raiz
    assert tokens("como habilito") & tokens("habilitar o pix")
    assert tokens("estorno") & tokens("estornar uma venda")
    assert "que" not in tokens("o que é isso") and "como" not in tokens("como faço")
    assert "cartao" in tokens("Cartão de crédito")


def _page(links: list[tuple[str, str]]) -> WebPage:
    return WebPage(url=BASE, title="Home", text="vitrine", links=links)


def test_select_relevant_links_needs_words_in_common_and_ranks_by_overlap() -> None:
    page = _page(
        [
            ("https://www.exemplo.com/pt/privacidade", "Politica de privacidade"),
            ("https://www.exemplo.com/pt/maquininha", "Maquininha"),
            ("https://www.exemplo.com/pt/maquininha/taxas", "Taxas da maquininha"),
        ]
    )

    chosen = select_relevant_links(page, "Qual a taxa da maquininha?", limit=5)

    assert [u for u, _ in chosen][0].endswith("/maquininha/taxas")
    assert not any("privacidade" in u for u, _ in chosen)  # sem palavra em comum: nao segue


def test_select_relevant_links_respects_the_limit_and_never_browses_blindly() -> None:
    page = _page(
        [("https://www.exemplo.com/pt/a", "Alpha"), ("https://www.exemplo.com/pt/b", "Beta")]
    )
    assert select_relevant_links(page, "pergunta sem relacao", limit=5) == []
    assert select_relevant_links(page, "alpha beta", limit=1) != []
    assert len(select_relevant_links(page, "alpha beta", limit=1)) == 1
    assert select_relevant_links(page, "alpha", limit=0) == []


def _source(name: str, url: str, topics: list[str], priority: int = 100) -> WebSource:
    return WebSource(
        source_id=name,
        name=name,
        url=url,
        normalized_url=normalize_url(url),
        description=f"Site {name}",
        usage="Use para " + " ".join(topics),
        topics=topics,
        priority=priority,
    )


def test_rank_sources_returns_all_when_they_fit_and_the_most_relevant_otherwise() -> None:
    sources = [
        _source("clima", "https://a.com/", ["clima", "tempo"], 1),
        _source("taxas", "https://b.com/", ["taxa", "maquininha"], 5),
        _source("api", "https://c.com/", ["api", "integracao"], 3),
    ]
    assert rank_sources("qual a taxa?", sources, limit=5) == sources  # cabem todas: ordem original
    top = rank_sources("qual a taxa da maquininha?", sources, limit=1)
    assert [s.name for s in top] == ["taxas"]
    tie = rank_sources("assunto sem relacao", sources, limit=2)  # empate: menor prioridade primeiro
    assert [s.name for s in tie] == ["clima", "api"]


def test_normalize_url_ignores_fragment_trailing_slash_and_host_case() -> None:
    assert normalize_url("HTTPS://Site.Getnet.com.br/") == normalize_url(
        "https://site.getnet.com.br"
    )
    assert normalize_url("https://a.com/x#topo") == normalize_url("https://a.com/x/")
    assert normalize_url("https://a.com/x?p=1") != normalize_url("https://a.com/x")


def test_links_inside_nav_and_footer_are_collected_even_though_their_text_is_not_content() -> None:
    html = (
        "<nav><a href='/pt/taxas'>Taxas</a></nav><p>Conteudo</p>"
        "<footer><a href='/pt/contato'>Contato</a></footer>"
    )

    _title, text, links = extract_page("text/html", html)

    assert dict(links) == {"/pt/taxas": "Taxas", "/pt/contato": "Contato"}
    assert (
        "Taxas" not in text and "Contato" not in text
    )  # o texto de menu continua fora do conteudo


def _page_html(before: str, body: str) -> str:
    return f"<title>T</title><div>{before}</div><h1>Como fazer X</h1><p>{body}</p>"


def test_site_header_before_the_h1_is_dropped_when_it_is_a_minority_of_the_page() -> None:
    header = "Ja e cliente? Fale Conosco Get Ajuda 4002-4000 " * 8  # ~370 caracteres
    body = "Passo a passo detalhado do procedimento. " * 20

    _title, text, _links = extract_page("text/html", _page_html(header, body))

    assert text.startswith("Como fazer X") and "Fale Conosco" not in text
    assert "Passo a passo" in text


def test_text_before_the_h1_is_kept_when_short_or_when_it_is_most_of_the_page() -> None:
    short = extract_page("text/html", _page_html("Breve intro.", "Corpo do texto. " * 20))[1]
    assert short.startswith("Breve intro.")  # abaixo do minimo: nao e cabecalho

    long_intro = "Introducao importante sobre o produto e suas condicoes. " * 30
    kept = extract_page("text/html", _page_html(long_intro, "Fim."))[1]
    assert kept.startswith("Introducao importante")  # a maior parte da pagina: nao descarta

    no_h1 = extract_page("text/html", "<div>" + "Texto solto da pagina. " * 40 + "</div>")[1]
    assert no_h1.startswith("Texto solto")


def test_rank_sources_weights_rare_words_more_than_words_present_everywhere() -> None:
    generic = ["maquininha", "aplicativo"]
    sources = [
        _source("senha", "https://a.com/", ["senha", "esqueci", *generic]),
        _source("estorno", "https://b.com/", ["estorno", "cancelar", *generic]),
        _source("wifi", "https://c.com/", ["wifi", "rede", *generic]),
        _source("manual", "https://d.com/", ["manual", "guia", *generic]),
    ]

    # "aplicativo"/"maquininha" estao em todas: o que decide e "senha"
    top = rank_sources("esqueci a senha do aplicativo da maquininha", sources, limit=1)

    assert [s.name for s in top] == ["senha"]


def test_maquininha_error_codes_become_distinct_tokens() -> None:
    from app.rag.loaders.web import tokens

    assert "c187" in tokens("Erro ADQ 1-87 na maquininha")
    assert "c187" in tokens("erro adq 1 87")
    assert "c483" in tokens("ERRO ADQ 4-83") and "c187" not in tokens("ERRO ADQ 4-83")
    assert tokens("erro IDL 1-08") & {"c108"}


@pytest.mark.parametrize(
    "message",
    [
        "Minha maquininha mostra Erro ADQ 1-87",
        "apareceu IDL 1-04",
        "erro s-2074",
        "erro na leitora NFC",
    ],
)
def test_error_code_messages_are_routed_to_the_support_flow(message: str) -> None:
    from app.agent.router.agent import RouterAgent
    from app.schemas.router import Intent, RouterDecision

    support = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.85,
        requires_clarification=False,
        reason_code="DEVICE_ERROR",
    )
    decision = RouterAgent._apply_error_code_guard(support, message)
    assert decision.intent == Intent.CUSTOMER_SUPPORT
    assert decision.target_agent == "customer_support_agent"
    assert decision.reason_code == "ERROR_CODE_IN_MESSAGE"


def test_device_problem_without_error_code_stays_with_customer_support() -> None:
    from app.agent.router.agent import RouterAgent
    from app.schemas.router import Intent, RouterDecision

    support = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.85,
        requires_clarification=False,
        reason_code="DEVICE_DOWN",
    )
    assert (
        RouterAgent._apply_error_code_guard(support, "Minha maquininha nao liga").intent
        == Intent.CUSTOMER_SUPPORT
    )


def test_error_codes_are_extracted_from_questions_and_chunks() -> None:
    from app.rag.loaders.web import error_codes

    assert error_codes("estou com o erro Erro ADQ 4-91: erro na aprovação") == {"adq491"}
    assert error_codes("Erro ADQ 4-83: erro de integração com as bandeiras") == {"adq483"}
    assert error_codes("deu IDL 1-08 e depois S-2074") == {"idl108", "s2074"}
    assert error_codes("Como estornar uma venda em 4-91 dias?") == set()


@pytest.mark.parametrize(
    "message",
    ["quero falar com um atendente", "abre um chamado pra mim", "preciso de uma pessoa de verdade"],
)
def test_human_request_is_routed_to_the_support_flow(message: str) -> None:
    from app.agent.router.agent import RouterAgent
    from app.schemas.router import Intent, RouterDecision

    knowledge = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.7,
        requires_clarification=False,
        reason_code="INFO",
    )
    decision = RouterAgent._apply_human_request_guard(knowledge, message)
    assert decision.intent == Intent.CUSTOMER_SUPPORT and decision.reason_code == "HUMAN_REQUESTED"
