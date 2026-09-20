"""user_id / session_id (e afins) nos cabecalhos; o corpo segue aceito, mas obsoleto."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.schemas.router import Intent, RouterDecision

U = {"X-User-Id": "user-h1"}


def _llm(fake_llm: dict) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="Q",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(message="ok", grounded_in_sources=False)


# --- sessoes -------------------------------------------------------------------------------------


def test_session_is_created_from_headers_with_no_body(client: TestClient) -> None:
    response = client.post("/api/v1/sessions", headers={**U, "X-Channel": "whatsapp"})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user-h1" and body["channel"] == "whatsapp"


def test_session_still_accepts_the_body_for_backward_compatibility(client: TestClient) -> None:
    body = client.post("/api/v1/sessions", json={"user_id": "user-b", "channel": "web"}).json()
    assert body["user_id"] == "user-b" and body["channel"] == "web"


def test_missing_user_id_is_a_clear_400(client: TestClient) -> None:
    response = client.post("/api/v1/sessions")

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["metadata"]["error_code"] == "USER_ID_REQUIRED"
    assert "X-User-Id" in body["message"]


def test_header_and_body_that_disagree_are_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/sessions", headers=U, json={"user_id": "outro"})

    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "IDENTITY_MISMATCH"


def test_header_and_body_that_agree_are_fine(client: TestClient) -> None:
    response = client.post("/api/v1/sessions", headers=U, json={"user_id": "user-h1"})
    assert response.status_code == 200


@pytest.mark.parametrize("bad", ["x" * 300, "a\x01b"])
def test_invalid_identifiers_are_rejected(client: TestClient, bad: str) -> None:
    # caracteres de controle nao passam nem como valor de cabecalho; o tamanho e validado por nos
    if "\x01" in bad:
        pytest.skip("o cliente HTTP ja recusa caracteres de controle em cabecalhos")
    response = client.post("/api/v1/sessions", headers={"X-User-Id": bad})
    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "IDENTITY_INVALID"


# --- mensagens -----------------------------------------------------------------------------------


def test_message_uses_user_and_session_from_headers_and_only_the_text_in_the_body(
    client: TestClient, fake_llm: dict
) -> None:
    _llm(fake_llm)
    sid = client.post("/api/v1/sessions", headers=U).json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers={**U, "X-Session-Id": sid},
        json={"message": "Qual a taxa?"},
    )

    assert response.status_code == 200
    assert response.json()["agent"] == "knowledge_agent"


def test_message_without_any_user_id_is_rejected(client: TestClient, fake_llm: dict) -> None:
    _llm(fake_llm)
    sid = client.post("/api/v1/sessions", headers=U).json()["session_id"]

    response = client.post(f"/api/v1/sessions/{sid}/messages", json={"message": "oi"})

    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "USER_ID_REQUIRED"


def test_session_header_that_differs_from_the_url_is_rejected(
    client: TestClient, fake_llm: dict
) -> None:
    _llm(fake_llm)
    sid = client.post("/api/v1/sessions", headers=U).json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers={**U, "X-Session-Id": "outra-sessao"},
        json={"message": "oi"},
    )

    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "IDENTITY_MISMATCH"


def test_message_body_identity_still_works_and_must_match_headers(
    client: TestClient, fake_llm: dict
) -> None:
    _llm(fake_llm)
    sid = client.post("/api/v1/sessions", json={"user_id": "user-b"}).json()["session_id"]

    ok = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"message": "oi", "user_id": "user-b"}
    )
    clash = client.post(
        f"/api/v1/sessions/{sid}/messages",
        headers={"X-User-Id": "user-x"},
        json={"message": "oi", "user_id": "user-b"},
    )

    assert ok.status_code == 200
    assert clash.status_code == 400


# --- feedback / memoria --------------------------------------------------------------------------


def test_feedback_ids_can_all_come_from_headers(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        headers={
            **U,
            "X-Session-Id": "s-1",
            "X-Message-Id": "m-1",
            "X-Execution-Id": "e-1",
        },
        json={"problem_classification": "KNOWLEDGE_GAP", "comment": "faltou conteudo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["user_id"], body["session_id"], body["message_id"], body["execution_id"]) == (
        "user-h1",
        "s-1",
        "m-1",
        "e-1",
    )


def test_feedback_missing_ids_names_the_missing_field(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback", headers=U, json={"problem_classification": "KNOWLEDGE_GAP"}
    )

    assert response.status_code == 400
    assert response.json()["metadata"]["error_code"] == "SESSION_ID_REQUIRED"


def test_memory_takes_the_user_from_the_header(client: TestClient) -> None:
    created = client.post(
        "/api/v1/memory",
        headers={**U, "X-Session-Id": "s-9"},
        json={"type": "long_term", "content": "prefere chat", "origin": "manual"},
    )
    assert created.status_code == 200
    assert created.json()["user_id"] == "user-h1"

    listed = client.get("/api/v1/memory", headers=U)
    assert listed.status_code == 200 and len(listed.json()) == 1

    other = client.get("/api/v1/memory", headers={"X-User-Id": "outro-usuario"})
    assert other.json() == []


# --- rate limit / OpenAPI ------------------------------------------------------------------------


def test_rate_limit_is_keyed_by_the_user_header(client: TestClient, monkeypatch) -> None:
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_max_requests", 2)
    codes = [
        client.post("/api/v1/sessions", headers={"X-User-Id": "limitado"}).status_code
        for _ in range(3)
    ]
    other = client.post("/api/v1/sessions", headers={"X-User-Id": "livre"}).status_code

    assert codes == [200, 200, 429]
    assert other == 200


def test_openapi_documents_the_headers_and_marks_body_ids_as_deprecated() -> None:
    from app.main import app

    schema = app.openapi()
    post_message = schema["paths"]["/api/v1/sessions/{session_id}/messages"]["post"]
    header_names = {p["name"] for p in post_message["parameters"] if p["in"] == "header"}
    assert {"X-User-Id", "X-Session-Id"} <= header_names

    request = schema["components"]["schemas"]["MessageRequest"]["properties"]
    assert request["user_id"].get("deprecated") is True
    assert request["session_id"].get("deprecated") is True


def test_duplicate_keyword_term_is_a_clear_409_not_a_500(client: TestClient, test_db) -> None:
    import asyncio

    # o indice unico e criado no boot (create_indexes); o mongomock dos testes nao passa por la
    asyncio.run(test_db["keywords"].create_index([("normalized_term", 1)], unique=True))
    body = {"term": "Taxa", "synonyms": ["tarifa"]}
    assert client.post("/api/v1/keywords", json=body).status_code == 200

    clash = client.post("/api/v1/keywords", json={"term": " taxa "})

    assert clash.status_code == 409
    assert clash.json()["metadata"]["error_code"] == "KEYWORD_ALREADY_EXISTS"
