"""Teste de integracao do middleware de rate limiting por user_id/IP (T134, plan.md §Boas
praticas) -- mitiga uso excessivo de chamadas repetidas."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings


def test_requests_beyond_the_limit_are_rejected_with_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_max_requests", 3)

    responses = [
        client.post("/api/v1/sessions", json={"user_id": "client_rate_limited"}) for _ in range(4)
    ]

    assert [r.status_code for r in responses[:3]] == [200, 200, 200]
    assert responses[3].status_code == 429
    assert responses[3].json()["metadata"]["error_code"] == "RATE_LIMIT_EXCEEDED"


def test_different_users_have_independent_rate_limit_buckets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)

    first_user = client.post("/api/v1/sessions", json={"user_id": "client_a"})
    second_user = client.post("/api/v1/sessions", json={"user_id": "client_b"})

    assert first_user.status_code == 200
    assert second_user.status_code == 200


def test_health_endpoint_is_never_rate_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)

    responses = [client.get("/health") for _ in range(5)]

    assert all(r.status_code == 200 for r in responses)
