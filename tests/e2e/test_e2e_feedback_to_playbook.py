"""T072: e2e do Feedback Agent -- feedback -> run sob demanda -> proposta -> aprovacao -> entidade real."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm.fake_defaults import _classification_default
from tests.feedback_helpers import classifier_handler, patch_agent_llm

_REVIEWER = {"X-Reviewer-Id": "revisor_1"}


@pytest.fixture(autouse=True)
def _default_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_agent_llm(
        monkeypatch, classifier_handler(lambda c: _classification_default(f"comentario: {c}"))
    )


def _register_feedback(client: TestClient, comment: str) -> str:
    response = client.post(
        "/api/v1/feedback",
        json={
            "user_id": "client_xpto",
            "session_id": "s1",
            "message_id": "m1",
            "execution_id": "e1",
            "problem_classification": "KNOWLEDGE_GAP",
            "comment": comment,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["feedback_id"]


def test_feedback_becomes_a_real_playbook_only_after_human_approval(client: TestClient) -> None:
    feedback_id = _register_feedback(
        client, "Nao existe passo a passo para maquininha que nao liga de jeito nenhum"
    )
    playbooks_before = client.get("/api/v1/playbooks").json()

    run = client.post("/api/v1/feedback-agent/run").json()
    assert run["status"] == "COMPLETED" and run["proposals_created_count"] == 1
    assert client.get("/api/v1/playbooks").json() == playbooks_before  # nada aplicado ainda

    pending = client.get(
        "/api/v1/feedback-agent/proposals", params={"status": "PENDING_HUMAN_REVIEW"}
    ).json()["proposals"]
    assert len(pending) == 1
    proposal = pending[0]
    assert proposal["action_type"] == "CREATE_PLAYBOOK"
    assert proposal["feedback_ids"] == [feedback_id]
    for section in ("objective", "symptoms", "steps", "success_criteria"):
        assert proposal["draft_content"][section]

    approved = client.post(
        f"/api/v1/feedback-agent/proposals/{proposal['proposal_id']}/approve", headers=_REVIEWER
    ).json()
    assert approved["status"] == "APPLIED" and approved["reviewed_by"] == "revisor_1"

    created = client.get(f"/api/v1/playbooks/{approved['applied_entity_id']}")
    assert created.status_code == 200
    assert created.json()["name"] == proposal["draft_content"]["name"]


def test_feedback_becomes_a_real_skill_after_approval(client: TestClient) -> None:
    _register_feedback(client, "Falta uma skill para orientar sobre o cancelamento de vendas")
    client.post("/api/v1/feedback-agent/run")
    proposal = client.get("/api/v1/feedback-agent/proposals").json()["proposals"][0]
    assert proposal["action_type"] == "CREATE_SKILL"

    approved = client.post(
        f"/api/v1/feedback-agent/proposals/{proposal['proposal_id']}/approve", headers=_REVIEWER
    ).json()

    skill = client.get(f"/api/v1/skills/{approved['applied_entity_id']}")
    assert skill.status_code == 200 and skill.json()["name"] == proposal["draft_content"]["name"]


def test_noise_feedback_produces_no_proposal_and_a_rejected_one_creates_nothing(
    client: TestClient,
) -> None:
    _register_feedback(client, "ruim")
    run = client.post("/api/v1/feedback-agent/run").json()
    assert run["proposals_created_count"] == 0 and run["feedback_processed_count"] == 1
    assert client.get("/api/v1/feedback-agent/proposals").json()["proposals"] == []

    _register_feedback(client, "Nao existe passo a passo para maquininha que nao liga nunca mais")
    client.post("/api/v1/feedback-agent/run")
    proposal = client.get("/api/v1/feedback-agent/proposals").json()["proposals"][0]
    rejected = client.post(
        f"/api/v1/feedback-agent/proposals/{proposal['proposal_id']}/reject",
        headers=_REVIEWER,
        json={"rejection_reason": "ja coberto"},
    ).json()

    assert rejected["status"] == "REJECTED"
    assert client.get("/api/v1/playbooks").json() == client.get("/api/v1/playbooks").json()
    assert all(
        p["name"] != proposal["draft_content"]["name"]
        for p in client.get("/api/v1/playbooks").json()
    )
    summary = client.get("/api/v1/feedback-agent/proposals").json()["approval_summary"]
    assert summary["rejected_count"] == 1 and summary["approval_rate"] == 0.0
