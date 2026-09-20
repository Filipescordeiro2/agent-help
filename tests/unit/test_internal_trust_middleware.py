"""T056: teste unitario do middleware de fronteira de confianca (FR-046).

Constroi uma aplicacao FastAPI minima reaproveitando o middleware real de app/main.py, sem
disparar o lifespan completo (que exige MongoDB real) -- isola o comportamento do middleware.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import internal_trust_boundary_middleware


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_service_token", "valid-token")

    test_app = FastAPI()
    test_app.middleware("http")(internal_trust_boundary_middleware)

    @test_app.get("/health")
    async def health():  # noqa: ANN202
        return {"status": "OK"}

    @test_app.get("/ready")
    async def ready():  # noqa: ANN202
        return {"status": "OK"}

    @test_app.get("/api/v1/sessions/123")
    async def protected():  # noqa: ANN202
        return {"status": "OK"}

    return TestClient(test_app)


def test_protected_route_rejects_missing_token(client: TestClient) -> None:
    response = client.get("/api/v1/sessions/123")
    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["metadata"]["error_code"] == "UNAUTHORIZED_CHANNEL"


def test_protected_route_rejects_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/sessions/123", headers={"X-Internal-Service-Token": "wrong-token"}
    )
    assert response.status_code == 403


def test_protected_route_allows_valid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/sessions/123", headers={"X-Internal-Service-Token": "valid-token"}
    )
    assert response.status_code == 200


def test_health_and_ready_are_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
