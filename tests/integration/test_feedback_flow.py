"""T118: teste de integracao para submissao e consulta/analytics de feedback (spec FR-039 a
FR-041)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_submit_and_retrieve_feedback(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/feedback",
        json={
            "user_id": "client_xpto",
            "session_id": "s1",
            "message_id": "m1",
            "execution_id": "e1",
            "problem_classification": "RESOLVED",
            "agent_invoked": "knowledge_agent",
            "rating": 5.0,
            "comment": "resposta otima",
        },
    )
    assert create_response.status_code == 200
    feedback_id = create_response.json()["feedback_id"]

    list_response = client.get("/api/v1/feedback")
    assert list_response.status_code == 200
    assert any(f["feedback_id"] == feedback_id for f in list_response.json())


def test_feedback_analytics_reflects_submitted_feedback(client: TestClient) -> None:
    client.post(
        "/api/v1/feedback",
        json={
            "user_id": "client_xpto",
            "session_id": "s1",
            "message_id": "m1",
            "execution_id": "e1",
            "problem_classification": "ROUTING_FAILURE",
            "agent_invoked": "router_agent",
        },
    )

    analytics = client.get("/api/v1/feedback/analytics").json()

    assert analytics["total"] >= 1
    assert analytics["by_problem_classification"]["ROUTING_FAILURE"] >= 1


def test_feedback_rejects_invalid_problem_classification(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={
            "user_id": "u",
            "session_id": "s",
            "message_id": "m",
            "execution_id": "e",
            "problem_classification": "NOT_VALID",
        },
    )
    assert response.status_code >= 400
