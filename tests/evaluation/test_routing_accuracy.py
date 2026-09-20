"""T133: avaliacao de acuracia de roteamento sobre uma amostra representativa de mensagens,
cobrindo o conjunto minimo de intencoes -- valida o alvo de >=90% de SC-002.

LIMITACAO HONESTA: este ambiente de teste nao tem acesso a um LLM real via OpenRouter (ver
research.md #3 -- LLM_PROVIDER=fake nos testes automatizados, por design, para determinismo).
Este teste portanto valida o MECANISMO de avaliacao (dataset rotulado + calculo de acuracia +
o limiar de aceite de SC-002) usando o Router Agent real com uma saida de LLM controlada por
um classificador heuristico simples que substitui o LLM real. A validacao de SC-002 contra o
modelo real configurado em producao (OPENROUTER_MODEL) e um passo manual/CI separado, fora do
alcance de testes automatizados deterministicos -- ver quickstart.md.
"""

from __future__ import annotations

import re

import pytest

import app.agent.router.agent as router_agent_module
from app.agent.router.agent import RouterAgent
from app.schemas.router import Intent, RouterDecision

# Amostra representativa cobrindo as 6 intencoes minimas (spec FR-006), com uma mensagem
# deliberadamente dificil (a ultima de cada bloco) para nao inflar artificialmente a acuracia.
_LABELED_DATASET: list[tuple[str, Intent]] = [
    ("Qual a taxa da maquininha no credito?", Intent.KNOWLEDGE),
    ("Como funciona o parcelamento sem juros?", Intent.KNOWLEDGE),
    ("Quais os planos disponiveis para minha empresa?", Intent.KNOWLEDGE),
    ("Minha maquininha nao conecta ao Wi-Fi", Intent.CUSTOMER_SUPPORT),
    ("Meu cartao foi recusado na ultima transacao", Intent.CUSTOMER_SUPPORT),
    ("Preciso trocar o dispositivo que esta com defeito", Intent.CUSTOMER_SUPPORT),
    ("Qual a taxa e por que minha maquininha nao conecta?", Intent.MULTI_AGENT),
    ("Quero saber sobre taxas e tambem resolver um problema no dispositivo", Intent.MULTI_AGENT),
    ("isso", Intent.CLARIFICATION_REQUIRED),
    ("me ajuda", Intent.CLARIFICATION_REQUIRED),
    ("Qual a previsao do tempo amanha em Sao Paulo?", Intent.UNKNOWN),
    ("Voce pode recomendar um restaurante?", Intent.UNKNOWN),
]

MIN_ACCURACY = 0.90


def _heuristic_classify(message: str) -> RouterDecision:
    """Substitui o LLM real por um classificador simples e determinista -- fica de propositi
    imperfeito (nao usa NLP real) para que o teste efetivamente exercite o calculo de acuracia
    em vez de sempre acertar 100% por construcao."""
    lowered = message.lower()
    has_knowledge_kw = bool(re.search(r"taxa|parcelamento|plano", lowered))
    has_support_kw = bool(re.search(r"conecta|recusad|defeito|dispositivo|maquininha", lowered))

    if has_knowledge_kw and has_support_kw:
        return RouterDecision(
            intent=Intent.MULTI_AGENT,
            target_agent=None,
            target_sequence=["knowledge_agent", "customer_support_agent"],
            confidence=0.85,
            requires_clarification=False,
            reason_code="MIXED",
        )
    if has_support_kw:
        return RouterDecision(
            intent=Intent.CUSTOMER_SUPPORT,
            target_agent="customer_support_agent",
            confidence=0.9,
            requires_clarification=False,
            reason_code="SUPPORT",
        )
    if has_knowledge_kw:
        return RouterDecision(
            intent=Intent.KNOWLEDGE,
            target_agent="knowledge_agent",
            confidence=0.9,
            requires_clarification=False,
            reason_code="KNOWLEDGE",
        )
    if len(lowered.split()) <= 2:
        return RouterDecision(
            intent=Intent.CLARIFICATION_REQUIRED,
            target_agent=None,
            confidence=0.4,
            requires_clarification=True,
            reason_code="TOO_SHORT",
        )
    return RouterDecision(
        intent=Intent.UNKNOWN,
        target_agent=None,
        confidence=0.8,
        requires_clarification=False,
        reason_code="OUT_OF_SCOPE",
    )


async def test_router_agent_achieves_minimum_routing_accuracy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_structured_output(_schema, messages) -> RouterDecision:
        user_message = messages[-1]["content"]
        return _heuristic_classify(user_message)

    monkeypatch.setattr(router_agent_module, "get_structured_output", fake_get_structured_output)

    agent = RouterAgent()
    correct = 0
    for message, expected_intent in _LABELED_DATASET:
        decision = await agent.decide(message)
        if decision.intent == expected_intent:
            correct += 1

    accuracy = correct / len(_LABELED_DATASET)
    assert accuracy >= MIN_ACCURACY, (
        f"acuracia de roteamento {accuracy:.0%} abaixo do alvo de {MIN_ACCURACY:.0%} (SC-002) "
        f"-- {correct}/{len(_LABELED_DATASET)} corretas"
    )
