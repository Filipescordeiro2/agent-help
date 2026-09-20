"""RAG web: base primeiro; sem resposta na base, acessa a URL, salva SO o relevante e reaproveita."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.rag.web_context as web_context
import app.routers.routes_web_sources as routes_web_sources
import app.services.knowledge as knowledge_service
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.config.settings import get_settings
from app.rag.loaders.web import WebFetchError, WebPage
from app.repository.web_sources_repository import WebSourcesRepository
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision

URL = "https://ajuda.exemplo.com/taxas"
# Uma pagina longa: so o trecho sobre a taxa da maquininha e relevante para a pergunta.
RELEVANT = "A taxa da maquininha no debito e de 1,99% por transacao."
PAGE_TEXT = "\n\n".join(
    [RELEVANT.ljust(700, " ")]
    + [
        f"Assunto irrelevante numero {i}: politica de privacidade, cookies e contato.".ljust(
            700, " "
        )
        for i in range(6)
    ]
)


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch):
    """Pagina web simulada; conta quantas vezes o 'site' foi acessado."""
    monkeypatch.setattr(get_settings(), "web_access_enabled", True)
    monkeypatch.setattr(get_settings(), "web_min_score", 0.5)
    state = {"fetches": 0, "error": None, "urls": [], "pages": {}, "links": {}, "errors": {}}

    async def fake_fetch(url: str) -> WebPage:
        state["fetches"] += 1
        state["urls"].append(url)
        if url in state["errors"]:
            raise state["errors"][url]
        if state["error"]:
            raise state["error"]
        return WebPage(
            url=url,
            title="Taxas da Getnet",
            text=state["pages"].get(url, PAGE_TEXT),
            links=state["links"].get(url, []),
        )

    monkeypatch.setattr(web_context, "fetch_web_page", fake_fetch)
    monkeypatch.setattr(knowledge_service, "fetch_web_page", fake_fetch)
    monkeypatch.setattr(routes_web_sources, "fetch_web_page", fake_fetch)
    return state


def _knowledge_llm(fake_llm: dict) -> None:
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


def _ask(client: TestClient, text: str) -> dict:
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]
    return client.post(
        f"/api/v1/sessions/{sid}/messages", json={"message": text, "user_id": "u1"}
    ).json()


def _count(test_db, collection: str, filters: dict | None = None) -> int:
    return asyncio.run(test_db[collection].count_documents(filters or {}))


def test_kb_miss_goes_to_the_site_and_saves_only_the_relevant_chunks(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["fetches"] == 1
    # salvou na base vetorial so o trecho relevante -- nao a pagina inteira
    docs = asyncio.run(test_db["knowledge_documents"].find({"source": URL}).to_list(10))
    assert len(docs) == 1
    assert docs[0]["metadata"]["doc_type"] == "web"
    assert docs[0]["metadata"]["fetched_at"]
    chunks = asyncio.run(
        test_db["knowledge_chunks"].find({"document_id": docs[0]["document_id"]}).to_list(50)
    )
    assert len(chunks) == 1  # a pagina tem varios trechos; so o relevante foi salvo
    assert RELEVANT in chunks[0]["content"]
    assert all(c["embedding"] and c["metadata"]["source"] == URL for c in chunks)
    # a resposta cita a fonte web com ids reais da base e a URL de origem
    assert body["sources"][0]["url"] == URL
    assert body["sources"][0]["document_id"] == docs[0]["document_id"]


def test_second_question_is_answered_from_the_base_without_visiting_the_site(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)
    _ask(client, f"Segundo {URL} qual a taxa da maquininha?")
    assert web["fetches"] == 1

    # mesma pergunta, agora SEM URL: ja esta na base semantica
    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["fetches"] == 1  # o site nao foi acessado de novo
    assert body["sources"][0]["score"] > 0


def test_question_with_url_already_answered_by_the_base_does_not_visit_the_site(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": RELEVANT, "product": "maquininha"},
    )

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["fetches"] == 0  # a base ja respondia: nao foi ao site


def test_repeating_the_question_does_not_duplicate_saved_chunks(
    client: TestClient, fake_llm: dict, web: dict, test_db, monkeypatch
) -> None:
    _knowledge_llm(fake_llm)
    _ask(client, f"Segundo {URL} qual a taxa da maquininha?")
    first = _count(test_db, "knowledge_chunks")
    # reaplica a persistencia dos mesmos trechos: nada deve ser duplicado
    chunks, _ = asyncio.run(
        web_context.build_web_context(f"qual a taxa da maquininha? {URL}", top_k=5, min_score=0.5)
    )
    asyncio.run(web_context.persist_web_findings(test_db, chunks))

    assert _count(test_db, "knowledge_chunks") == first
    assert _count(test_db, "knowledge_documents", {"source": URL}) == 1


def test_unreachable_page_returns_insufficient_context_with_the_reason(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)
    web["error"] = WebFetchError("WEB_URL_NOT_ALLOWED", "Endereco de rede nao permitido.")

    body = _ask(client, "Segundo http://10.0.0.5/admin qual a taxa da maquininha?")

    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    # ao cliente: so a orientacao (o motivo tecnico fica na auditoria)
    assert "Nao consegui acessar o link informado." in body["message"]
    assert "central de atendimento" in body["message"]
    assert "Endereco de rede" not in body["message"]
    assert body["metadata"]["grounding_score"] is None
    assert _count(test_db, "knowledge_documents") == 0  # nada foi salvo


def test_irrelevant_page_saves_nothing(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)

    body = _ask(client, f"Segundo {URL} como resolver problema de conexao?")

    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert _count(test_db, "knowledge_documents") == 0
    assert _count(test_db, "knowledge_chunks") == 0


def test_web_access_can_be_disabled(
    client: TestClient, fake_llm: dict, web: dict, monkeypatch, test_db
) -> None:
    _knowledge_llm(fake_llm)
    monkeypatch.setattr(get_settings(), "web_access_enabled", False)

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert web["fetches"] == 0


# --- /knowledge/ingest-url -----------------------------------------------------------------------


def test_ingest_url_with_query_saves_only_the_relevant_chunks(
    client: TestClient, web: dict, test_db
) -> None:
    response = client.post(
        "/api/v1/knowledge/ingest-url",
        json={"url": URL, "query": "qual a taxa da maquininha?", "product": "maquininha"},
    )

    assert response.status_code == 200
    document = response.json()
    assert document["source"] == URL and document["product"] == "maquininha"
    assert _count(test_db, "knowledge_chunks", {"document_id": document["document_id"]}) == 1


def test_ingest_url_without_query_saves_the_whole_page_and_is_idempotent(
    client: TestClient, web: dict, test_db
) -> None:
    first = client.post("/api/v1/knowledge/ingest-url", json={"url": URL}).json()
    second = client.post("/api/v1/knowledge/ingest-url", json={"url": URL}).json()

    assert first["document_id"] == second["document_id"]
    assert _count(test_db, "knowledge_documents", {"source": URL}) == 1
    total = _count(test_db, "knowledge_chunks", {"document_id": first["document_id"]})
    assert total >= 5  # pagina inteira (varios chunks), sem duplicar na 2a chamada


def test_ingest_url_rejects_internal_addresses_with_a_clear_error(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge/ingest-url", json={"url": "http://169.254.169.254/latest"}
    )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["metadata"]["error_code"] == "WEB_URL_NOT_ALLOWED"


def test_ingest_url_requires_the_llm_key_in_real_mode(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "openrouter")

    response = client.post("/api/v1/knowledge/ingest-url", json={"url": URL})

    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "LLM_API_KEY_REQUIRED"


def test_message_the_router_could_not_classify_still_reaches_the_web_flow(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    """O Router devolve 'esclarecimento', mas a URL na mensagem leva ao Knowledge Agent."""
    _knowledge_llm(fake_llm)
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CLARIFICATION_REQUIRED,
        confidence=0.3,
        requires_clarification=True,
        reason_code="UNCLEAR",
    )

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["agent"] == "knowledge_agent"
    assert body["status"] == Status.OK.value
    assert web["fetches"] == 1


# --- fontes web cadastradas (o agente vai sozinho, sem URL na mensagem) ---------------------------

SOURCE_A = "https://a.exemplo.com/ajuda"
SOURCE_B = "https://b.exemplo.com/taxas"
SOURCE_C = "https://c.exemplo.com/faq"
IRRELEVANT_PAGE = "Assunto irrelevante: politica de privacidade, cookies e contato."


def _add_source(client: TestClient, url: str, **overrides) -> dict:
    payload = {
        "name": f"Fonte {url}",
        "url": url,
        "description": "Site de teste com informacoes de produtos.",
        "usage": "Use para duvidas sobre taxas e produtos.",
        "topics": ["taxa", "maquininha"],
        **overrides,
    }
    response = client.post("/api/v1/web-sources", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_base_miss_without_url_goes_to_the_registered_source_and_saves_the_relevant_part(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_B)

    body = _ask(client, "Qual a taxa da maquininha?")  # sem URL na mensagem

    assert body["status"] == Status.OK.value
    assert web["urls"] == [SOURCE_B]
    assert body["sources"][0]["url"] == SOURCE_B
    docs = asyncio.run(test_db["knowledge_documents"].find({"source": SOURCE_B}).to_list(5))
    assert len(docs) == 1 and docs[0]["metadata"]["doc_type"] == "web"
    assert _count(test_db, "knowledge_chunks", {"document_id": docs[0]["document_id"]}) == 1


def test_next_question_on_the_same_topic_no_longer_goes_to_the_site(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_B)
    _ask(client, "Qual a taxa da maquininha?")
    assert web["fetches"] == 1

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["fetches"] == 1  # respondida pela base semantica


def test_when_it_does_not_know_it_hits_all_sources_and_sees_which_has_the_information(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)
    for url in (SOURCE_A, SOURCE_B, SOURCE_C):
        _add_source(client, url)
    web["pages"][SOURCE_A] = IRRELEVANT_PAGE  # nada sobre o tema

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert set(web["urls"]) == {SOURCE_A, SOURCE_B, SOURCE_C}  # bateu nas tres
    cited = {s["url"] for s in body["sources"]}
    assert cited <= {SOURCE_B, SOURCE_C} and cited  # so as que de fato tinham a informacao
    # estatisticas: quais fontes ajudaram
    stats = {
        s.url: (s.consulted_count, s.useful_count)
        for s in asyncio.run(WebSourcesRepository(test_db).list_all())
    }
    assert stats[SOURCE_A] == (1, 0)
    assert stats[SOURCE_B][0] == 1 and stats[SOURCE_C][0] == 1
    assert stats[SOURCE_B][1] + stats[SOURCE_C][1] >= 1


def test_a_failing_source_does_not_stop_the_others(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_A)
    _add_source(client, SOURCE_B)
    web["errors"][SOURCE_A] = WebFetchError("WEB_FETCH_FAILED", "Falha ao acessar a pagina.")

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert body["sources"][0]["url"] == SOURCE_B


def test_nobody_knows_so_the_customer_is_sent_to_the_contact_center(
    client: TestClient, fake_llm: dict, web: dict, test_db
) -> None:
    _knowledge_llm(fake_llm)
    for url in (SOURCE_A, SOURCE_B):
        _add_source(client, url)
        web["pages"][url] = IRRELEVANT_PAGE

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert "central de atendimento" in body["message"]
    assert body["metadata"]["grounding_score"] is None
    assert _count(test_db, "knowledge_documents") == 0  # nada irrelevante foi salvo


def test_no_url_and_no_registered_source_means_the_contact_center_message(
    client: TestClient, fake_llm: dict, web: dict, monkeypatch
) -> None:
    _knowledge_llm(fake_llm)
    monkeypatch.setattr(get_settings(), "support_contact_message", "Ligue para a central 0800-XXX.")

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert body["message"] == "Ligue para a central 0800-XXX."
    assert web["fetches"] == 0


def test_registered_sources_are_not_used_when_the_base_already_answers(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_B)
    client.post(
        "/api/v1/knowledge/documents",
        json={"title": "Taxas", "source": "manual", "content": RELEVANT},
    )

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["fetches"] == 0


def test_disabled_sources_are_never_consulted(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_A, enabled=False)
    _add_source(client, SOURCE_B)

    _ask(client, "Qual a taxa da maquininha?")

    assert web["urls"] == [SOURCE_B]


def test_url_in_the_message_takes_priority_over_registered_sources(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_B)

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == [URL]  # a fonte cadastrada nao foi necessaria


def test_registered_sources_are_consulted_if_the_message_url_has_nothing_relevant(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_B)
    web["pages"][URL] = IRRELEVANT_PAGE

    body = _ask(client, f"Segundo {URL} qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == [URL, SOURCE_B]


def test_homepage_without_the_answer_follows_matching_links_of_the_same_site(
    client: TestClient, fake_llm: dict, web: dict
) -> None:
    _knowledge_llm(fake_llm)
    _add_source(client, SOURCE_A)
    detail = "https://a.exemplo.com/maquininha/taxas"
    web["pages"][SOURCE_A] = IRRELEVANT_PAGE  # a home so tem vitrine/menu
    web["links"][SOURCE_A] = [
        (detail, "Taxas da maquininha"),
        ("https://a.exemplo.com/privacidade", "Politica de privacidade"),
    ]

    body = _ask(client, "Qual a taxa da maquininha?")

    assert body["status"] == Status.OK.value
    assert web["urls"] == [SOURCE_A, detail]  # seguiu so o link que combina com a pergunta
    assert body["sources"][0]["url"] == detail


def test_with_more_sources_than_the_limit_the_most_relevant_ones_are_chosen(
    client: TestClient, fake_llm: dict, web: dict, monkeypatch
) -> None:
    _knowledge_llm(fake_llm)
    monkeypatch.setattr(get_settings(), "web_max_sources_per_question", 2)
    _add_source(
        client, SOURCE_A, topics=["clima"], description="Previsao do tempo.", usage="Clima."
    )
    _add_source(client, SOURCE_B, topics=["taxa", "maquininha"])
    _add_source(client, SOURCE_C, topics=["taxa"], description="Taxas de cartao.", usage="Taxas.")

    _ask(client, "Qual a taxa da maquininha?")

    assert set(web["urls"]) == {SOURCE_B, SOURCE_C}


# --- API das fontes web ----------------------------------------------------------------------------


def test_web_source_api_crud_and_duplicate_protection(client: TestClient) -> None:
    created = _add_source(client, "https://novo.exemplo.com/docs", priority=5)

    assert created["origin"] == "api" and created["consulted_count"] == 0
    assert client.get(f"/api/v1/web-sources/{created['source_id']}").json()["url"].endswith("/docs")
    duplicate = client.post(
        "/api/v1/web-sources",
        json={
            "name": "x",
            "url": "https://novo.exemplo.com/docs/",
            "description": "d",
            "usage": "u",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["metadata"]["error_code"] == "WEB_SOURCE_ALREADY_EXISTS"

    updated = client.put(
        f"/api/v1/web-sources/{created['source_id']}",
        json={
            "name": "Docs",
            "url": "https://novo.exemplo.com/docs",
            "description": "Docs novos",
            "usage": "Use para APIs",
            "topics": ["api"],
            "priority": 1,
            "enabled": False,
        },
    ).json()
    assert updated["enabled"] is False and updated["priority"] == 1 and updated["topics"] == ["api"]
    assert client.get("/api/v1/web-sources", params={"enabled": True}).json() == []
    assert client.delete(f"/api/v1/web-sources/{created['source_id']}").status_code == 200
    assert client.get(f"/api/v1/web-sources/{created['source_id']}").status_code == 404


@pytest.mark.parametrize(
    "url",
    ["ftp://x.com/a", "http://user:pw@x.com/", "http://x.com:8080/", "nao e url"],
)
def test_web_source_api_rejects_unsafe_urls(client: TestClient, url: str) -> None:
    response = client.post(
        "/api/v1/web-sources",
        json={"name": "x", "url": url, "description": "d", "usage": "u"},
    )
    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"].startswith("WEB_URL_")


def test_web_source_test_endpoint_shows_what_the_agent_would_read(
    client: TestClient, web: dict
) -> None:
    source = _add_source(client, SOURCE_B)
    web["links"][SOURCE_B] = [("https://b.exemplo.com/x", "x")]

    result = client.post(f"/api/v1/web-sources/{source['source_id']}/test").json()

    assert result["ok"] is True and result["links_found"] == 1 and "taxa" in result["preview"]
    web["errors"][SOURCE_B] = WebFetchError("WEB_FETCH_FAILED", "Falha ao acessar a pagina.")
    failed = client.post(f"/api/v1/web-sources/{source['source_id']}/test").json()
    assert failed["ok"] is False and failed["error_code"] == "WEB_FETCH_FAILED"
