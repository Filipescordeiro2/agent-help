"""T054: teste unitario da decisao de alvo unico do Router Agent (LLM mockado)."""

from __future__ import annotations

import pytest

import app.agent.router.agent as router_agent_module
from app.agent.router.agent import RouterAgent
from app.schemas.router import Intent, RouterDecision


def _patch_structured_output(monkeypatch: pytest.MonkeyPatch, decision: RouterDecision) -> None:
    async def fake_get_structured_output(_schema, _messages) -> RouterDecision:
        return decision

    monkeypatch.setattr(router_agent_module, "get_structured_output", fake_get_structured_output)


@pytest.mark.parametrize(
    "intent,target_agent",
    [
        (Intent.KNOWLEDGE, "knowledge_agent"),
        (Intent.CUSTOMER_SUPPORT, "customer_support_agent"),
        (Intent.UNKNOWN, None),
    ],
)
async def test_router_agent_returns_decision_from_llm(
    monkeypatch: pytest.MonkeyPatch, intent: Intent, target_agent: str | None
) -> None:
    decision = RouterDecision(
        intent=intent,
        target_agent=target_agent,
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    _patch_structured_output(monkeypatch, decision)

    agent = RouterAgent()
    result = await agent.decide("mensagem qualquer")

    assert result.intent == intent
    assert result.target_agent == target_agent


async def test_router_agent_blocked_intent_is_never_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = RouterDecision(
        intent=Intent.SECURITY_BLOCKED,
        target_agent=None,
        confidence=0.1,
        requires_clarification=False,
        reason_code="X",
    )
    _patch_structured_output(monkeypatch, decision)

    agent = RouterAgent()
    result = await agent.decide("mensagem maliciosa")

    assert result.intent == Intent.SECURITY_BLOCKED


async def test_router_agent_downgrades_low_confidence_decision_to_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent="knowledge_agent",
        confidence=0.2,
        requires_clarification=False,
        reason_code="X",
    )
    _patch_structured_output(monkeypatch, decision)

    agent = RouterAgent(confidence_threshold=0.55)
    result = await agent.decide("mensagem ambigua")

    assert result.intent == Intent.CLARIFICATION_REQUIRED
    assert result.requires_clarification is True


async def test_router_agent_keeps_high_confidence_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.95,
        requires_clarification=False,
        reason_code="DEVICE_CONNECTION_PROBLEM",
    )
    _patch_structured_output(monkeypatch, decision)

    agent = RouterAgent(confidence_threshold=0.55)
    result = await agent.decide("minha maquininha nao conecta")

    assert result.intent == Intent.CUSTOMER_SUPPORT
