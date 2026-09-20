"""T063: autorizacao e concorrencia nas revisoes de propostas (Principios V e XII)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.feedback_validation.schemas import PlaybookDraft, PlaybookStepDraft
from app.repository.feedback_proposals_repository import (
    ActionType,
    FeedbackProposal,
    FeedbackProposalsRepository,
    ProposalStatus,
)
from tests.conftest import _build_test_app

_PLAYBOOK = PlaybookDraft(
    name="Concorrencia",
    objective="Testar.",
    symptoms=["x"],
    steps=[PlaybookStepDraft(step_id="s", instruction="i")],
    success_criteria=["ok"],
)


async def seed(
    db, pid: str, action: ActionType, draft: dict, status=ProposalStatus.PENDING_HUMAN_REVIEW
):
    await FeedbackProposalsRepository(db).insert(
        FeedbackProposal(
            proposal_id=pid,
            feedback_ids=["f"],
            status=status,
            action_type=action,
            draft_content=draft,
            justificativa="j",
            confidence=0.8,
        )
    )


_ENDPOINTS = (
    ("get", "/api/v1/feedback-agent/proposals", None),
    ("get", "/api/v1/feedback-agent/proposals/p1", None),
    ("post", "/api/v1/feedback-agent/proposals/p1/approve", None),
    ("post", "/api/v1/feedback-agent/proposals/p1/reject", {"rejection_reason": "x"}),
    ("post", "/api/v1/feedback-agent/proposals/p1/mark-applied", None),
)


@pytest.mark.parametrize(("method", "url", "body"), _ENDPOINTS)
async def test_every_endpoint_requires_the_internal_token(
    test_db, internal_token, fake_embeddings, method: str, url: str, body
) -> None:
    await seed(test_db, "p1", ActionType.CREATE_PLAYBOOK, _PLAYBOOK.model_dump())
    anonymous = TestClient(_build_test_app())

    response = getattr(anonymous, method)(url, **({"json": body} if body else {}))

    assert response.status_code == 403
    assert response.json()["metadata"]["error_code"] == "UNAUTHORIZED_CHANNEL"


@pytest.mark.parametrize("suffix", ["approve", "reject", "mark-applied"])
async def test_review_operations_require_the_reviewer_header(
    client: TestClient, test_db, suffix: str
) -> None:
    await seed(test_db, "p1", ActionType.CREATE_PLAYBOOK, _PLAYBOOK.model_dump())
    kwargs = {"json": {"rejection_reason": "x"}} if suffix == "reject" else {}

    for headers in ({}, {"X-Reviewer-Id": "   "}, {"X-Reviewer-Id": "x" * 500}):
        response = client.post(
            f"/api/v1/feedback-agent/proposals/p1/{suffix}", headers=headers, **kwargs
        )
        assert response.status_code == 400
        assert response.json()["metadata"]["error_code"] == "REVIEWER_ID_REQUIRED"

    stored = client.get("/api/v1/feedback-agent/proposals/p1").json()
    assert stored["status"] == "PENDING_HUMAN_REVIEW" and stored["reviewed_by"] is None


async def test_reviewer_identity_comes_only_from_the_header_never_from_the_body(
    client: TestClient, test_db
) -> None:
    await seed(test_db, "p1", ActionType.CREATE_PLAYBOOK, _PLAYBOOK.model_dump())

    response = client.post(
        "/api/v1/feedback-agent/proposals/p1/reject",
        headers={"X-Reviewer-Id": "revisor_real"},
        json={"rejection_reason": "x", "reviewed_by": "atacante", "status": "APPLIED"},
    )

    body = response.json()
    assert body["reviewed_by"] == "revisor_real" and body["status"] == "REJECTED"


async def test_concurrent_approvals_create_exactly_one_entity(
    test_db, internal_token, fake_embeddings
) -> None:
    await seed(test_db, "p1", ActionType.CREATE_PLAYBOOK, _PLAYBOOK.model_dump())
    transport = httpx.ASGITransport(app=_build_test_app())
    headers = {"X-Internal-Service-Token": internal_token, "X-Reviewer-Id": "rev"}

    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers) as c:
        responses = await asyncio.gather(
            *[c.post("/api/v1/feedback-agent/proposals/p1/approve") for _ in range(3)]
        )

    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409, 409]
    assert await test_db["playbooks"].count_documents({"name": "Concorrencia"}) == 1


async def test_concurrent_mark_applied_confirms_only_once(
    test_db, internal_token, fake_embeddings
) -> None:
    await seed(
        test_db,
        "pa",
        ActionType.PROMPT_ADJUSTMENT,
        {"target_prompt": "knowledge", "proposed_change": "x"},
        ProposalStatus.APPROVED,
    )
    transport = httpx.ASGITransport(app=_build_test_app())
    headers = {"X-Internal-Service-Token": internal_token}

    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers) as c:
        responses = await asyncio.gather(
            c.post(
                "/api/v1/feedback-agent/proposals/pa/mark-applied", headers={"X-Reviewer-Id": "a"}
            ),
            c.post(
                "/api/v1/feedback-agent/proposals/pa/mark-applied", headers={"X-Reviewer-Id": "b"}
            ),
        )

    assert sorted(r.status_code for r in responses) == [200, 409]
