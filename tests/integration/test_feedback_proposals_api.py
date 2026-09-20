"""T062: API de propostas do Feedback Agent (US3) -- listar, detalhar, aprovar, rejeitar, confirmar."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent.feedback_validation.schemas import (
    PlaybookDraft,
    PlaybookStepDraft,
    PromptAdjustmentDraft,
    SkillDraft,
)
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from app.schemas.agent_response import AgentResponse, Status

_REVIEWER = {"X-Reviewer-Id": "revisor_1"}
_PLAYBOOK = PlaybookDraft(
    name="Maquininha nao liga",
    objective="Restabelecer a maquininha.",
    symptoms=["nao liga"],
    steps=[PlaybookStepDraft(step_id="s1", instruction="Verificar bateria.")],
    success_criteria=["Liga."],
)
_SKILL = SkillDraft(
    name="Taxas", description="d", owning_agent="knowledge_agent", instructions="Cite a fonte."
)


async def seed(
    db,
    pid: str,
    action: ActionType = ActionType.CREATE_PLAYBOOK,
    draft: dict | None = None,
    status: ProposalStatus = ProposalStatus.PENDING_HUMAN_REVIEW,
    **extra,
) -> None:
    default_draft = {
        ActionType.CREATE_PLAYBOOK: _PLAYBOOK.model_dump(),
        ActionType.CREATE_SKILL: _SKILL.model_dump(),
        ActionType.PROMPT_ADJUSTMENT: PromptAdjustmentDraft(
            target_prompt="knowledge", proposed_change="Exigir fonte."
        ).model_dump(),
    }.get(action, {})
    await FeedbackProposalsRepository(db).insert(
        FeedbackProposal(
            proposal_id=pid,
            feedback_ids=["f1"],
            status=status,
            action_type=action,
            draft_content=draft if draft is not None else default_draft,
            justificativa="j",
            confidence=0.8,
            **extra,
        )
    )


def _assert_structured_error(response, code: str) -> dict:
    body = response.json()
    AgentResponse.model_validate(body)
    assert body["status"] == Status.ERROR.value and body["metadata"]["error_code"] == code
    return body


async def test_list_filters_by_status_and_returns_the_approval_summary(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "pend")
    await seed(test_db, "appr", ActionType.CREATE_SKILL, status=ProposalStatus.APPLIED)
    await seed(test_db, "rej", status=ProposalStatus.REJECTED, rejection_reason="x")

    everything = client.get("/api/v1/feedback-agent/proposals").json()
    pending = client.get(
        "/api/v1/feedback-agent/proposals", params={"status": "PENDING_HUMAN_REVIEW"}
    ).json()

    assert len(everything["proposals"]) == 3
    assert [p["proposal_id"] for p in pending["proposals"]] == ["pend"]
    summary = pending["approval_summary"]  # agregado sobre TODAS, nao so a pagina filtrada
    assert summary == {
        "total_reviewed": 2,
        "approved_count": 1,
        "rejected_count": 1,
        "approval_rate": 0.5,
    }


async def test_detail_returns_the_proposal_or_a_structured_404(client: TestClient, test_db) -> None:
    await seed(test_db, "p1")

    found = client.get("/api/v1/feedback-agent/proposals/p1")
    missing = client.get("/api/v1/feedback-agent/proposals/nope")

    assert found.status_code == 200 and found.json()["proposal_id"] == "p1"
    assert missing.status_code == 404
    _assert_structured_error(missing, "PROPOSAL_NOT_FOUND")


async def test_approve_creates_the_real_playbook_and_marks_applied(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "p1")

    response = client.post("/api/v1/feedback-agent/proposals/p1/approve", headers=_REVIEWER)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPLIED" and body["reviewed_by"] == "revisor_1"
    assert body["reviewed_at"] and body["applied_entity_id"]
    playbook = client.get(f"/api/v1/playbooks/{body['applied_entity_id']}")
    assert playbook.status_code == 200 and playbook.json()["name"] == "Maquininha nao liga"


async def test_approve_skill_creates_the_skill(client: TestClient, test_db) -> None:
    await seed(test_db, "s1", ActionType.CREATE_SKILL)
    body = client.post("/api/v1/feedback-agent/proposals/s1/approve", headers=_REVIEWER).json()
    skill = client.get(f"/api/v1/skills/{body['applied_entity_id']}")
    assert skill.status_code == 200 and skill.json()["name"] == "Taxas"


async def test_reject_requires_a_reason_and_applies_nothing(client: TestClient, test_db) -> None:
    await seed(test_db, "p1")
    url = "/api/v1/feedback-agent/proposals/p1/reject"

    assert client.post(url, headers=_REVIEWER, json={}).status_code == 422
    assert client.post(url, headers=_REVIEWER, json={"rejection_reason": ""}).status_code == 422

    response = client.post(url, headers=_REVIEWER, json={"rejection_reason": "duplicada"})
    body = response.json()
    assert response.status_code == 200 and body["status"] == "REJECTED"
    assert body["rejection_reason"] == "duplicada" and body["reviewed_by"] == "revisor_1"
    assert client.get("/api/v1/playbooks").json() == [] or all(
        p["name"] != "Maquininha nao liga" for p in client.get("/api/v1/playbooks").json()
    )


async def test_invalid_transitions_return_409_with_structured_error(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "done", status=ProposalStatus.APPLIED)
    await seed(test_db, "gone", status=ProposalStatus.REJECTED, rejection_reason="x")

    for pid in ("done", "gone"):
        approve = client.post(f"/api/v1/feedback-agent/proposals/{pid}/approve", headers=_REVIEWER)
        reject = client.post(
            f"/api/v1/feedback-agent/proposals/{pid}/reject",
            headers=_REVIEWER,
            json={"rejection_reason": "x"},
        )
        for response in (approve, reject):
            assert response.status_code == 409
            _assert_structured_error(response, "INVALID_PROPOSAL_STATUS")
    assert client.get("/api/v1/feedback-agent/proposals/done").json()["status"] == "APPLIED"


async def test_invalid_draft_returns_structured_422_and_keeps_the_proposal_pending(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "bad", draft={"name": "incompleto"})

    response = client.post("/api/v1/feedback-agent/proposals/bad/approve", headers=_REVIEWER)

    assert response.status_code == 422
    _assert_structured_error(response, "DRAFT_CONTENT_INVALID")
    assert client.get("/api/v1/feedback-agent/proposals/bad").json()["status"] == (
        "PENDING_HUMAN_REVIEW"
    )


async def test_prompt_adjustment_flow_approved_then_mark_applied(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "pa", ActionType.PROMPT_ADJUSTMENT)

    approved = client.post("/api/v1/feedback-agent/proposals/pa/approve", headers=_REVIEWER).json()
    assert approved["status"] == "APPROVED" and approved["applied_entity_id"] is None

    confirmed = client.post(
        "/api/v1/feedback-agent/proposals/pa/mark-applied", headers={"X-Reviewer-Id": "revisor_2"}
    )
    body = confirmed.json()
    assert confirmed.status_code == 200 and body["status"] == "APPLIED"
    assert body["applied_confirmed_by"] == "revisor_2" and body["applied_at"]

    again = client.post("/api/v1/feedback-agent/proposals/pa/mark-applied", headers=_REVIEWER)
    assert again.status_code == 409
    _assert_structured_error(again, "INVALID_PROPOSAL_STATUS")


async def test_mark_applied_is_refused_for_other_action_types_and_statuses(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "skill_ok", ActionType.CREATE_SKILL, status=ProposalStatus.APPROVED)
    await seed(test_db, "pa_pending", ActionType.PROMPT_ADJUSTMENT)

    for pid in ("skill_ok", "pa_pending"):
        response = client.post(
            f"/api/v1/feedback-agent/proposals/{pid}/mark-applied", headers=_REVIEWER
        )
        assert response.status_code == 409
        _assert_structured_error(response, "INVALID_PROPOSAL_STATUS")

    missing = client.post("/api/v1/feedback-agent/proposals/nope/mark-applied", headers=_REVIEWER)
    assert missing.status_code == 404


async def test_approval_summary_reflects_reviews_after_the_fact(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "a", ActionType.CREATE_SKILL)
    await seed(test_db, "b")
    client.post("/api/v1/feedback-agent/proposals/a/approve", headers=_REVIEWER)
    client.post(
        "/api/v1/feedback-agent/proposals/b/reject",
        headers=_REVIEWER,
        json={"rejection_reason": "nao serve"},
    )

    summary = client.get("/api/v1/feedback-agent/proposals").json()["approval_summary"]

    assert summary["total_reviewed"] == 2 and summary["approval_rate"] == 0.5
