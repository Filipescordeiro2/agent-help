"""Router Agent -- decisao de roteamento estruturada, nunca texto livre (spec FR-006 a FR-010).

Nesta fase (Foundational) suporta apenas decisao de alvo unico: KNOWLEDGE, CUSTOMER_SUPPORT,
UNKNOWN, SECURITY_BLOCKED. MULTI_AGENT e CLARIFICATION_REQUIRED sao adicionados na US3
(ver extend_for_multi_agent abaixo, aplicado em app/agent/nodes/router_node.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.agent.small_talk import asks_for_human
from app.config.settings import get_settings
from app.llm.structured_output import get_structured_output
from app.rag.loaders.web import extract_urls
from app.schemas.router import Intent, RouterDecision

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "router" / "v1.md"

# Limiar minimo de confianca abaixo do qual a decisao MUST virar CLARIFICATION_REQUIRED
# (estendido pela US3 -- ver agent/router/agent.py::apply_clarification_threshold).
DEFAULT_CONFIDENCE_THRESHOLD = 0.55

# Codigos de erro da maquininha da Central de Ajuda: "ADQ 1-87", "IDL 1-04", "S-2074", NFC.
_ERROR_CODE_IN_MESSAGE = re.compile(
    r"\b(?:adq|idl)\s*\d\s?-?\s?\d{2}\b|\bs\s?-\s?\d{4}\b|\bnfc\b", re.IGNORECASE
)

# Nome canonico do agente de cada intencao. Modelos costumam devolver o nome da INTENCAO
# ("CUSTOMER_SUPPORT") ou uma forma curta ("knowledge") em `target_agent`; normalizamos.
_AGENT_BY_INTENT = {
    Intent.KNOWLEDGE: "knowledge_agent",
    Intent.CUSTOMER_SUPPORT: "customer_support_agent",
}
_ALIASES = {
    "knowledge": "knowledge_agent",
    "knowledge_agent": "knowledge_agent",
    "customer_support": "customer_support_agent",
    "customer_support_agent": "customer_support_agent",
    "support": "customer_support_agent",
}


def canonical_agent_name(name: str | None) -> str | None:
    """`knowledge`, `KNOWLEDGE`, `Knowledge Agent` -> `knowledge_agent`; outro: inalterado."""
    if name is None:
        return None
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(key, name)


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class RouterAgent:
    name = "router_agent"

    def __init__(self, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> None:
        self._confidence_threshold = confidence_threshold

    async def decide(self, message: str) -> RouterDecision:
        system_prompt = _load_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]
        decision = await get_structured_output(RouterDecision, messages)
        decision = self._normalize_targets(decision)
        decision = self._apply_clarification_threshold(decision)
        decision = self._apply_url_guard(decision, message)
        decision = self._apply_error_code_guard(decision, message)
        return self._apply_human_request_guard(decision, message)

    @staticmethod
    def _normalize_targets(decision: RouterDecision) -> RouterDecision:
        """Garante nomes de agente validos: aceita o nome da intencao ou forma curta e, se o
        modelo deixou o alvo vazio numa intencao de agente unico, deriva-o da intencao."""
        target = canonical_agent_name(decision.target_agent)
        if decision.intent in _AGENT_BY_INTENT and target not in _AGENT_BY_INTENT.values():
            target = _AGENT_BY_INTENT[decision.intent]
        sequence = [canonical_agent_name(n) or n for n in decision.target_sequence]
        return decision.model_copy(update={"target_agent": target, "target_sequence": sequence})

    @staticmethod
    def _apply_url_guard(decision: RouterDecision, message: str) -> RouterDecision:
        """Mensagem com URL que o modelo nao soube classificar vai para o Knowledge Agent.

        Garante por codigo (nao so pelo prompt) que uma pergunta sobre uma pagina web chegue ao
        agente que sabe consulta-la (base primeiro, depois a pagina). Nao interfere quando o
        modelo ja escolheu uma intencao concreta (ex.: suporte) nem em bloqueios de seguranca."""
        if decision.intent not in (Intent.CLARIFICATION_REQUIRED, Intent.UNKNOWN):
            return decision
        if not get_settings().web_access_enabled or not extract_urls(message, limit=1):
            return decision
        return decision.model_copy(
            update={
                "intent": Intent.KNOWLEDGE,
                "target_agent": "knowledge_agent",
                "target_sequence": [],
                "confidence": max(decision.confidence, 0.6),
                "requires_clarification": False,
                "reason_code": "URL_IN_MESSAGE",
            }
        )

    @staticmethod
    def _apply_error_code_guard(decision: RouterDecision, message: str) -> RouterDecision:
        """Codigo de erro da maquininha (ADQ 1-87, IDL 1-04, S-2074, NFC) vai para o SUPORTE.

        E um problema em andamento: o Customer Support Agent busca o passo a passo na Central de
        Ajuda, passa ao cliente, pergunta se deu certo e, se nao, abre o chamado. Nao interfere em
        MULTI_AGENT nem em bloqueios de seguranca."""
        if decision.intent not in (
            Intent.KNOWLEDGE,
            Intent.CUSTOMER_SUPPORT,
            Intent.CLARIFICATION_REQUIRED,
            Intent.UNKNOWN,
        ):
            return decision
        if not _ERROR_CODE_IN_MESSAGE.search(message):
            return decision
        return decision.model_copy(
            update={
                "intent": Intent.CUSTOMER_SUPPORT,
                "target_agent": "customer_support_agent",
                "target_sequence": [],
                "confidence": max(decision.confidence, 0.6),
                "requires_clarification": False,
                "reason_code": "ERROR_CODE_IN_MESSAGE",
            }
        )

    @staticmethod
    def _apply_human_request_guard(decision: RouterDecision, message: str) -> RouterDecision:
        """ "Quero falar com um atendente" vai para o SUPORTE, que entende o problema primeiro e
        so entao abre o chamado (nunca um chamado vazio). Nao interfere em MULTI_AGENT."""
        if decision.intent not in (
            Intent.KNOWLEDGE,
            Intent.CUSTOMER_SUPPORT,
            Intent.CLARIFICATION_REQUIRED,
            Intent.UNKNOWN,
        ):
            return decision
        if not asks_for_human(message):
            return decision
        return decision.model_copy(
            update={
                "intent": Intent.CUSTOMER_SUPPORT,
                "target_agent": "customer_support_agent",
                "target_sequence": [],
                "confidence": max(decision.confidence, 0.7),
                "requires_clarification": False,
                "reason_code": "HUMAN_REQUESTED",
            }
        )

    def _apply_clarification_threshold(self, decision: RouterDecision) -> RouterDecision:
        if (
            decision.intent
            not in (Intent.CLARIFICATION_REQUIRED, Intent.SECURITY_BLOCKED, Intent.GREETING)
            and decision.confidence < self._confidence_threshold
        ):
            return decision.model_copy(
                update={
                    "intent": Intent.CLARIFICATION_REQUIRED,
                    "requires_clarification": True,
                }
            )
        return decision
