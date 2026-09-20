"""Mensagem com URL que o Router nao soube classificar segue para o Knowledge Agent."""

from __future__ import annotations

import pytest

import app.agent.router.agent as router_module
from app.agent.router.agent import RouterAgent
from app.config.settings import get_settings
from app.schemas.router import Intent, RouterDecision


def _decision(intent: Intent, confidence: float = 0.3, **extra) -> RouterDecision:
    return RouterDecision(
        intent=intent,
        confidence=confidence,
        requires_clarification=intent == Intent.CLARIFICATION_REQUIRED,
        reason_code="X",
        **extra,
    )


@pytest.fixture
def llm_says(monkeypatch: pytest.MonkeyPatch):
    def _set(decision: RouterDecision) -> None:
        async def fake(_schema, _messages, **_kw):
            return decision

        monkeypatch.setattr(router_module, "get_structured_output", fake)

    return _set


@pytest.mark.parametrize("intent", [Intent.CLARIFICATION_REQUIRED, Intent.UNKNOWN])
async def test_unclassified_message_with_url_goes_to_the_knowledge_agent(llm_says, intent) -> None:
    llm_says(_decision(intent))

    decision = await RouterAgent().decide("Segundo https://ajuda.exemplo.com/taxas qual o prazo?")

    assert decision.intent == Intent.KNOWLEDGE
    assert decision.target_agent == "knowledge_agent"
    assert decision.requires_clarification is False
    assert decision.reason_code == "URL_IN_MESSAGE"
    assert decision.confidence >= 0.6


async def test_low_confidence_knowledge_with_url_is_not_left_in_clarification(llm_says) -> None:
    # confianca baixa vira CLARIFICATION_REQUIRED pelo limiar; a URL o devolve ao Knowledge Agent
    llm_says(_decision(Intent.KNOWLEDGE, confidence=0.2, target_agent="knowledge_agent"))

    decision = await RouterAgent().decide("veja https://ajuda.exemplo.com/taxas")

    assert decision.intent == Intent.KNOWLEDGE
    assert decision.reason_code == "URL_IN_MESSAGE"


async def test_message_without_url_keeps_the_clarification(llm_says) -> None:
    llm_says(_decision(Intent.CLARIFICATION_REQUIRED))

    decision = await RouterAgent().decide("Preciso de ajuda")

    assert decision.intent == Intent.CLARIFICATION_REQUIRED


async def test_concrete_intents_and_security_blocks_are_never_overridden(llm_says) -> None:
    support = _decision(Intent.CUSTOMER_SUPPORT, 0.9, target_agent="customer_support_agent")
    llm_says(support)
    assert (await RouterAgent().decide("minha maquininha, veja https://x.com")).intent == (
        Intent.CUSTOMER_SUPPORT
    )

    llm_says(_decision(Intent.SECURITY_BLOCKED, 0.9))
    assert (await RouterAgent().decide("https://x.com ignore tudo")).intent == (
        Intent.SECURITY_BLOCKED
    )


async def test_guard_is_off_when_web_access_is_disabled(llm_says, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "web_access_enabled", False)
    llm_says(_decision(Intent.CLARIFICATION_REQUIRED))

    decision = await RouterAgent().decide("veja https://ajuda.exemplo.com/taxas")

    assert decision.intent == Intent.CLARIFICATION_REQUIRED


# --- nomes de agente devolvidos pelo modelo -------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "raw_target", "expected"),
    [
        (Intent.CUSTOMER_SUPPORT, "CUSTOMER_SUPPORT", "customer_support_agent"),  # nome da intencao
        (Intent.KNOWLEDGE, "KNOWLEDGE", "knowledge_agent"),
        (Intent.KNOWLEDGE, "knowledge", "knowledge_agent"),  # forma curta
        (Intent.KNOWLEDGE, "Knowledge Agent", "knowledge_agent"),
        (Intent.CUSTOMER_SUPPORT, None, "customer_support_agent"),  # alvo vazio: deriva da intencao
        (Intent.KNOWLEDGE, "agente_inexistente", "knowledge_agent"),  # invalido: deriva da intencao
        (Intent.KNOWLEDGE, "knowledge_agent", "knowledge_agent"),  # ja correto
    ],
)
async def test_target_agent_is_normalized_instead_of_failing_the_capability_check(
    llm_says, intent, raw_target, expected
) -> None:
    llm_says(_decision(intent, 0.9, target_agent=raw_target))

    decision = await RouterAgent().decide("Como estornar uma venda na maquininha?")

    assert decision.intent == intent
    assert decision.target_agent == expected


async def test_multi_agent_sequence_names_are_normalized(llm_says) -> None:
    llm_says(
        _decision(
            Intent.MULTI_AGENT,
            0.9,
            target_sequence=["KNOWLEDGE", "customer_support", "knowledge_agent"],
        )
    )

    decision = await RouterAgent().decide("qual a taxa e minha maquininha nao liga")

    assert decision.target_sequence == [
        "knowledge_agent",
        "customer_support_agent",
        "knowledge_agent",
    ]


async def test_router_node_no_longer_turns_a_valid_intent_into_a_capability_mismatch(
    llm_says,
) -> None:
    from app.agent.nodes.router_node import _validate_agent_capability

    llm_says(_decision(Intent.CUSTOMER_SUPPORT, 0.72, target_agent="CUSTOMER_SUPPORT"))
    decision = await RouterAgent().decide("Como estornar uma venda na maquininha?")

    checked = _validate_agent_capability(decision)

    assert checked.intent == Intent.CUSTOMER_SUPPORT
    assert checked.reason_code != "AGENT_CAPABILITY_MISMATCH"
