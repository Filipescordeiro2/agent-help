"""Homologacao de URLs: o agente so consulta paginas de fontes web CADASTRADAS e habilitadas.

Um link colado pelo cliente que nao seja uma dessas paginas nunca e acessado; o cliente recebe
uma explicacao gentil e, quando possivel, a resposta vem das fontes oficiais."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.rag.web_context as web_context
import app.services.knowledge as knowledge_service
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.config.settings import get_settings
from app.rag.loaders.web import WebPage
from app.rag.web_context import is_homologated_url
from app.repository.web_sources_repository import WebSource, normalize_url
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision

OFFICIAL = "https://ajuda.exemplo.com/taxas"
OUTSIDE = "https://site-de-terceiros.com/promocao"
RELEVANT = "A taxa da maquininha no debito e de 1,99% por transacao."
PAGE_TEXT = "\n\n".join(
    [RELEVANT.ljust(700, " ")]
    + [
        f"Assunto irrelevante {i}: politica de privacidade e cookies.".ljust(700, " ")
        for i in range(6)
    ]
)
FRIENDLY = "só consulto páginas oficiais homologadas"


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(get_settings(), "web_access_enabled", True)
    monkeypatch.setattr(get_settings(), "web_min_score", 0.5)
    state: dict = {"urls": []}

    async def fake_fetch(url: str) -> WebPage:
        state["urls"].append(url)
        return WebPage(url=url, title="Taxas da Getnet", text=PAGE_TEXT, links=[])

    monkeypatch.setattr(web_context, "fetch_web_page", fake_fetch)
    monkeypatch.setattr(knowledge_service, "fetch_web_page", fake_fetch)
    return state


@pytest.fixture(autouse=True)
def _llm(fake_llm: dict) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="PRODUCT_QUESTION",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(
        message="A taxa no debito e de 1,99%.", grounded_in_sources=True
    )


def _register(client: TestClient, url: str, **overrides) -> dict:
    payload = {
        "name": f"Fonte {url}",
        "url": url,
        "description": "Site oficial de teste com taxas.",
        "usage": "Use para duvidas sobre taxas e produtos.",
        "topics": ["taxa", "maquininha"],
        **overrides,
    }
    response = client.post("/api/v1/web-sources", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _ask(client: TestClient, text: str) -> tuple[dict, str]:
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]
    body = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"message": text, "user_id": "u1"}
    ).json()
    return body, sid


def test_link_outside_the_registered_sources_is_never_opened(
    client: TestClient, web: dict, test_db
) -> None:
    body, _ = _ask(client, f"Segundo {OUTSIDE} qual a taxa da maquininha?")

    assert web["urls"] == []  # nem tentou acessar
    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert FRIENDLY in body["message"]
    assert "central de atendimento" in body["message"]
    assert asyncio.run(test_db["knowledge_documents"].count_documents({})) == 0


def test_blocked_link_still_gets_an_answer_from_the_official_sources(
    client: TestClient, web: dict
) -> None:
    _register(client, OFFICIAL)

    body, _ = _ask(client, f"Segundo {OUTSIDE} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == [OFFICIAL]  # foi a fonte homologada, nunca o link de fora
    assert OUTSIDE not in web["urls"]
    assert FRIENDLY in body["message"]
    assert "fontes oficiais" in body["message"]
    assert body["sources"][0]["url"] == OFFICIAL


def test_blocked_link_is_flagged_even_when_the_base_already_answers(
    client: TestClient, web: dict
) -> None:
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": RELEVANT, "product": "maquininha"},
    )

    body, _ = _ask(client, f"Segundo {OUTSIDE} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == []
    assert FRIENDLY in body["message"]


@pytest.mark.parametrize(
    "pasted",
    [
        OFFICIAL,
        OFFICIAL + "/",
        "http://ajuda.exemplo.com/taxas",
        "https://AJUDA.exemplo.com/taxas#precos",
        OFFICIAL + "?utm_source=email&utm_campaign=x",
    ],
)
def test_the_registered_page_is_allowed_in_any_equivalent_form(
    client: TestClient, web: dict, pasted: str
) -> None:
    _register(client, OFFICIAL)

    body, _ = _ask(client, f"Segundo {pasted} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert len(web["urls"]) == 1  # a pagina homologada foi lida
    assert FRIENDLY not in body["message"]


def test_a_disabled_source_is_not_homologated(client: TestClient, web: dict) -> None:
    _register(client, OFFICIAL, enabled=False)

    body, _ = _ask(client, f"Segundo {OFFICIAL} qual a taxa da maquininha?")

    assert web["urls"] == []
    assert FRIENDLY in body["message"]


def test_the_policy_can_be_turned_off_for_experiments(
    client: TestClient, web: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "web_only_registered_urls", False)

    body, _ = _ask(client, f"Segundo {OUTSIDE} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == [OUTSIDE]
    assert FRIENDLY not in body["message"]


def test_the_audit_explains_that_the_link_was_not_consulted(
    client: TestClient, web: dict
) -> None:
    _, sid = _ask(client, f"Segundo {OUTSIDE} qual a taxa da maquininha?")

    audit = client.get(f"/api/v1/audit/sessions/{sid}").json()
    text = "\n".join(audit["turns"][0]["explanation"])

    assert "NAO sao de fontes homologadas" in text
    assert OUTSIDE in text


def test_an_operator_can_still_ingest_a_specific_url(client: TestClient, web: dict) -> None:
    response = client.post(
        "/api/v1/knowledge/ingest-url", json={"url": OUTSIDE, "query": "taxa da maquininha"}
    )

    assert response.status_code == 200, response.text
    assert web["urls"] == [OUTSIDE]


def _source(url: str, *, enabled: bool = True) -> WebSource:
    return WebSource(
        source_id="s1",
        name="n",
        url=url,
        normalized_url=normalize_url(url),
        description="d",
        usage="u",
        enabled=enabled,
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a.com/x", True),
        ("https://a.com/x/", True),
        ("http://a.com/x", True),
        ("https://A.com/x#topo", True),
        ("https://a.com/x?utm_source=z", True),
        ("https://a.com/x?id=2", False),
        ("https://a.com/y", False),
        ("https://b.com/x", False),
        ("https://a.com.evil.com/x", False),
        ("nao e uma url", False),
    ],
)
def test_is_homologated_url(url: str, expected: bool) -> None:
    assert is_homologated_url(url, [_source("https://a.com/x")]) is expected


def test_disabled_sources_never_homologate() -> None:
    disabled = _source("https://a.com/x", enabled=False)
    assert is_homologated_url("https://a.com/x", [disabled]) is False
