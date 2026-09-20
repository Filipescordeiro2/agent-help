"""Estado tipado do StateGraph do Feedback Agent (Constitution Principio I)."""

from __future__ import annotations

from typing import Any, TypedDict


class FeedbackAgentState(TypedDict, total=False):
    run_id: str
    pending_feedback: list[Any]  # list[Feedback]
    catalog: dict[str, list[dict[str, str]]]
    classified: list[Any]  # list[tuple[Feedback, FeedbackClassification]]
    groups: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    proposals: list[str]  # ids das FeedbackProposal criadas
    processed_ids: list[str]
    discarded_count: int
    blocked: dict[str, str]  # feedback_id -> reason_code do scanner (nunca o texto bloqueado)
