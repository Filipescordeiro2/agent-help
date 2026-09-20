"""Swagger/OpenAPI da plataforma: publico, com o esquema de seguranca do token interno."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def schema() -> dict:
    return TestClient(app).get("/openapi.json").json()


def test_swagger_ui_and_openapi_are_served_without_a_token() -> None:
    client = TestClient(app)
    docs = client.get("/docs")
    assert docs.status_code == 200 and "swagger" in docs.text.lower()
    assert client.get("/openapi.json").status_code == 200


def test_openapi_declares_the_internal_token_security_scheme(schema: dict) -> None:
    scheme = schema["components"]["securitySchemes"]["InternalServiceToken"]
    assert scheme == {
        "type": "apiKey",
        "in": "header",
        "name": "X-Internal-Service-Token",
        "description": scheme["description"],
    }
    assert schema["info"]["title"] == "Getnet Multiagent Support Platform"
    assert "Authorize" in schema["info"]["description"]


def test_protected_operations_require_the_scheme_and_public_ones_do_not(schema: dict) -> None:
    paths = schema["paths"]
    for public in ("/health", "/ready"):
        assert "security" not in paths[public]["get"]
    for protected in (
        ("/api/v1/metrics/tpm", "get"),
        ("/api/v1/feedback-agent/proposals/{proposal_id}/reject", "post"),
        ("/metrics", "get"),
    ):
        path, method = protected
        assert paths[path][method]["security"] == [{"InternalServiceToken": []}], path
    # rotas que usam LLM/embeddings exigem tambem a chave do OpenRouter (X-API-Key-LLM)
    for llm_route in (
        ("/api/v1/sessions/{session_id}/messages", "post"),
        ("/api/v1/feedback-agent/proposals/{proposal_id}/approve", "post"),
        ("/api/v1/feedback-agent/run", "post"),
    ):
        path, method = llm_route
        assert paths[path][method]["security"] == [{"InternalServiceToken": [], "LLMApiKey": []}], (
            path
        )


def test_feature_002_routes_are_documented(schema: dict) -> None:
    paths = schema["paths"]
    for expected in (
        "/api/v1/feedback-agent/run",
        "/api/v1/feedback-agent/runs",
        "/api/v1/feedback-agent/runs/{run_id}",
        "/api/v1/feedback-agent/proposals",
        "/api/v1/feedback-agent/proposals/{proposal_id}",
        "/api/v1/feedback-agent/proposals/{proposal_id}/approve",
        "/api/v1/feedback-agent/proposals/{proposal_id}/reject",
        "/api/v1/feedback-agent/proposals/{proposal_id}/mark-applied",
        "/api/v1/metrics/tpm",
        "/api/v1/metrics/rpm",
        "/api/v1/metrics/latency",
        "/api/v1/metrics/sessions-per-user",
        "/api/v1/metrics/tokens-per-user-session",
        "/api/v1/metrics/grounding-scores",
        "/metrics",
    ):
        assert expected in paths, expected


def test_reviewer_header_is_documented_on_review_operations(schema: dict) -> None:
    operation = schema["paths"]["/api/v1/feedback-agent/proposals/{proposal_id}/approve"]["post"]
    names = {p["name"] for p in operation["parameters"]}
    assert "x-reviewer-id" in {n.lower() for n in names}


def test_tags_are_described(schema: dict) -> None:
    described = {t["name"] for t in schema["tags"]}
    used = {tag for ops in schema["paths"].values() for op in ops.values() for tag in op["tags"]}
    assert used <= described
