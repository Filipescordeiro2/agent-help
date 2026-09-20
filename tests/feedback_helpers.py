"""Helpers compartilhados pelos testes do Feedback Agent."""

from __future__ import annotations

import pytest

import app.agent.feedback_validation.agent as agent_module
from app.services.feedback import create_feedback


async def add_feedback(db, comment: str, *, classification: str = "KNOWLEDGE_GAP", **kwargs):
    defaults = {
        "user_id": "client_xpto",
        "session_id": "s1",
        "message_id": "m1",
        "execution_id": "e1",
        "problem_classification": classification,
        "comment": comment,
    }
    defaults.update(kwargs)
    return await create_feedback(db, **defaults)


def patch_agent_llm(monkeypatch: pytest.MonkeyPatch, handler) -> list[tuple[type, list]]:
    """Substitui `get_structured_output` do Feedback Agent por `handler(schema, messages)`.

    Devolve a lista (schema, messages) das chamadas para assercoes."""
    calls: list[tuple[type, list]] = []

    async def fake(schema, messages, **_kwargs):
        calls.append((schema, messages))
        return handler(schema, messages)

    monkeypatch.setattr(agent_module, "get_structured_output", fake)
    return calls


def last_user_text(messages: list[dict]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user")


def classifier_handler(mapping):
    """`handler(schema, messages)` que classifica pelo comentario (`mapping(comment)`) e delega
    os drafts aos defaults deterministicos do modo fake."""
    import app.llm.fake_defaults  # noqa: F401 -- registra os defaults deterministicos
    from app.agent.feedback_validation.schemas import FeedbackClassification
    from app.llm.openrouter_client import _FAKE_DEFAULTS

    def handler(schema, messages):
        text = last_user_text(messages)
        if schema is FeedbackClassification:
            comment = next(
                line.removeprefix("comentario:").strip()
                for line in text.splitlines()
                if line.startswith("comentario:")
            )
            return mapping(comment)
        return _FAKE_DEFAULTS[schema](text)

    return handler
