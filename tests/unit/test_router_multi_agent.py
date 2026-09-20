"""T095: teste unitario da extensao do Router Agent para MULTI_AGENT (target_sequence) e
CLARIFICATION_REQUIRED (limiar de confianca configuravel) -- spec FR-008 a FR-010."""

from __future__ import annotations

import pytest

import app.agent.router.agent as router_agent_module
from app.agent.router.agent import RouterAgent
from app.schemas.router import Intent, RouterDecision


def _patch(monkeypatch: pytest.MonkeyPatch, decision: RouterDecision) -> None:
    async def fake_get_structured_output(_schema, _messages) -> RouterDecision:
        return decision

    monkeypatch.setattr(router_agent_module, "get_structured_output", fake_get_structured_output)


async def test_multi_agent_decision_preserves_target_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = RouterDecision(
        intent=Intent.MULTI_AGENT,
        target_agent=None,
        target_sequence=["knowledge_agent", "customer_support_agent"],
        confidence=0.9,
        requires_clarification=False,
        reason_code="MIXED_DOMAIN_REQUEST",
    )
    _patch(monkeypatch, decision)

    agent = RouterAgent(confidence_threshold=0.55)
    result = await agent.decide("qual a taxa e minha maquininha nao conecta")

    assert result.intent == Intent.MULTI_AGENT
    assert result.target_sequence == ["knowledge_agent", "customer_support_agent"]


async def test_low_confidence_multi_agent_decision_is_downgraded_to_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = RouterDecision(
        intent=Intent.MULTI_AGENT,
        target_agent=None,
        target_sequence=["knowledge_agent", "customer_support_agent"],
        confidence=0.3,
        requires_clarification=False,
        reason_code="MIXED_DOMAIN_REQUEST",
    )
    _patch(monkeypatch, decision)

    agent = RouterAgent(confidence_threshold=0.55)
    result = await agent.decide("mensagem ambigua envolvendo varios assuntos")

    assert result.intent == Intent.CLARIFICATION_REQUIRED
    assert result.requires_clarification is True


async def test_unknown_intent_with_high_confidence_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quando o Router tem confianca suficiente de que a mensagem esta fora do escopo, a
    intencao permanece UNKNOWN em vez de virar um encaminhamento incorreto (US3 Acceptance
    Scenario 3) -- o no de esclarecimento (clarification_node) trata ambas de forma unificada
    no contrato de saida (status CLARIFICATION_REQUIRED), mas com mensagens diferentes."""
    decision = RouterDecision(
        intent=Intent.UNKNOWN,
        target_agent=None,
        confidence=0.9,
        requires_clarification=False,
        reason_code="OUT_OF_SCOPE",
    )
    _patch(monkeypatch, decision)

    agent = RouterAgent(confidence_threshold=0.55)
    result = await agent.decide("pergunta totalmente fora do escopo suportado")

    assert result.intent == Intent.UNKNOWN
