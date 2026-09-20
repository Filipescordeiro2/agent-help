"""Defaults deterministicos do modo fake (`LLM_PROVIDER=fake`) por schema de saida estruturada.

Importado por `get_chat_model()` quando o provedor e `fake`; permite smoke tests offline
(`docker compose up` sem credenciais) em que o no de grounding e o Feedback Agent respondem de
forma previsivel.
"""

from __future__ import annotations

import re

from app.agent.customer_support.agent import SupportAnswerDraft
from app.agent.customer_support.case_flow import FollowUpClassification, ProblemAssessment
from app.agent.feedback_validation.schemas import (
    FeedbackClassification,
    KnowledgeDraft,
    PlaybookDraft,
    PlaybookStepDraft,
    PromptAdjustmentDraft,
    SkillDraft,
)
from app.agent.knowledge.agent import KnowledgeAnswerDraft
from app.llm.openrouter_client import register_fake_default
from app.repository.feedback_proposals_repository import ActionType
from app.schemas.grounding import GroundingEvaluation
from app.schemas.router import Intent, RouterDecision


def _grounding_default(_user_text: str) -> GroundingEvaluation:
    return GroundingEvaluation(
        score=5,
        reasoning="fake",
        adheres_to_question=True,
        uses_context_correctly=True,
        no_unsupported_claims=True,
        is_complete=True,
    )


register_fake_default(GroundingEvaluation, _grounding_default)


# --- Feedback Validation Agent (defaults deterministicos por palavra-chave do comentario) -------


def _comment_of(user_text: str) -> str:
    for line in user_text.splitlines():
        if line.startswith("comentario:"):
            return line.removeprefix("comentario:").strip()
    return user_text


def _first_comment(user_text: str) -> str:
    comments = [
        line.removeprefix("comentario:").strip()
        for line in user_text.splitlines()
        if line.startswith("comentario:")
    ]
    return comments[0] if comments else user_text


def _topic(comment: str) -> str:
    return " ".join(comment.lower().split()[:5])


def _classification_default(user_text: str) -> FeedbackClassification:
    comment = _comment_of(user_text)
    lowered = comment.lower()
    if len(lowered.strip()) < 15:
        return FeedbackClassification(
            is_actionable=False,
            discard_reason="NOISE",
            justificativa="Feedback sem conteudo especifico.",
            confidence=0.9,
        )
    rules = (
        (("playbook", "passo a passo"), ActionType.CREATE_PLAYBOOK),
        (("skill",), ActionType.CREATE_SKILL),
        (("conhecimento", "documento"), ActionType.CREATE_KNOWLEDGE),
        (("prompt",), ActionType.PROMPT_ADJUSTMENT),
    )
    for keywords, action in rules:
        if any(k in lowered for k in keywords):
            return FeedbackClassification(
                is_actionable=True,
                action_type=action,
                target_topic=_topic(comment),
                justificativa="Lacuna especifica identificada no feedback.",
                confidence=0.8,
            )
    return FeedbackClassification(
        is_actionable=False,
        discard_reason="OUT_OF_SCOPE",
        justificativa="Nenhuma melhoria concreta identificada.",
        confidence=0.6,
    )


def _playbook_default(user_text: str) -> PlaybookDraft:
    topic = _topic(_first_comment(user_text)) or "novo problema"
    return PlaybookDraft(
        name=f"Playbook: {topic}",
        objective=f"Resolver o problema relatado: {topic}.",
        symptoms=[topic],
        steps=[
            PlaybookStepDraft(
                step_id="advise_customer",
                instruction="Orientar o cliente conforme o procedimento revisado.",
                on_success="closed",
                on_failure="escalate",
            )
        ],
        authorized_tools=["create_support_ticket"],
        escalation_rules=["Se nao resolver, encaminhar para atendente humano."],
        success_criteria=["Cliente confirma a resolucao."],
    )


def _skill_default(user_text: str) -> SkillDraft:
    topic = _topic(_first_comment(user_text)) or "novo tema"
    return SkillDraft(
        name=f"Skill: {topic}",
        description=f"Orientacoes sobre {topic}.",
        owning_agent="knowledge_agent",
        keywords=topic.split(),
        instructions=f"Ao tratar de {topic}, responda apenas com base no conteudo recuperado.",
    )


def _knowledge_default(user_text: str) -> KnowledgeDraft:
    topic = _topic(_first_comment(user_text)) or "novo tema"
    return KnowledgeDraft(
        title=f"Conhecimento: {topic}",
        source="feedback_agent",
        content=f"Rascunho para revisao humana sobre {topic}.",
    )


def _prompt_default(user_text: str) -> PromptAdjustmentDraft:
    topic = _topic(_first_comment(user_text))
    return PromptAdjustmentDraft(
        target_prompt="knowledge",
        proposed_change=f"Revisar o prompt conforme o feedback: {topic}.",
    )


register_fake_default(FeedbackClassification, _classification_default)
register_fake_default(PlaybookDraft, _playbook_default)
register_fake_default(SkillDraft, _skill_default)
register_fake_default(KnowledgeDraft, _knowledge_default)
register_fake_default(PromptAdjustmentDraft, _prompt_default)


# --- Atendimento (Router / Knowledge / Customer Support) -- demonstracao offline ------------------
# Com `LLM_PROVIDER=fake` a plataforma responde de ponta a ponta sem credenciais: o Router decide
# por palavras-chave e os agentes devolvem respostas simuladas (claramente marcadas), ecoando o
# contexto recuperado. Nao substitui um LLM real -- serve para validar a plataforma, o grounding e a
# observabilidade.

_KNOWLEDGE_HINTS = (
    "taxa",
    "tarifa",
    "preco",
    "quanto custa",
    "como funciona",
    "prazo",
    "cashback",
    "http://",
    "https://",
)
_SUPPORT_HINTS = ("nao conecta", "nao liga", "offline", "sem sinal", "problema", "travou", "erro")


def _router_default(user_text: str) -> RouterDecision:
    lowered = user_text.lower()
    knowledge = any(h in lowered for h in _KNOWLEDGE_HINTS)
    support = any(h in lowered for h in _SUPPORT_HINTS)
    if knowledge and support:
        return RouterDecision(
            intent=Intent.MULTI_AGENT,
            target_sequence=["knowledge_agent", "customer_support_agent"],
            confidence=0.9,
            requires_clarification=False,
            reason_code="FAKE_MIXED_DOMAIN",
        )
    if support:
        return RouterDecision(
            intent=Intent.CUSTOMER_SUPPORT,
            target_agent="customer_support_agent",
            confidence=0.9,
            requires_clarification=False,
            reason_code="FAKE_SUPPORT_KEYWORD",
        )
    if knowledge:
        return RouterDecision(
            intent=Intent.KNOWLEDGE,
            target_agent="knowledge_agent",
            confidence=0.9,
            requires_clarification=False,
            reason_code="FAKE_KNOWLEDGE_KEYWORD",
        )
    return RouterDecision(
        intent=Intent.CLARIFICATION_REQUIRED,
        confidence=0.4,
        requires_clarification=True,
        reason_code="FAKE_NO_KEYWORD",
    )


_SOURCE_LINE = re.compile(r"\[Fonte [^\]]+\] (.+)")


def _knowledge_answer_default(_user_text: str, system_text: str = "") -> KnowledgeAnswerDraft:
    match = _SOURCE_LINE.search(system_text)
    if match:
        return KnowledgeAnswerDraft(
            message=(
                "(resposta simulada, modo fake) Segundo a base de conhecimento: " + match.group(1)
            ),
            grounded_in_sources=True,
        )
    return KnowledgeAnswerDraft(
        message="(resposta simulada, modo fake) Sem trecho recuperado.", grounded_in_sources=False
    )


def _support_answer_default(_user_text: str) -> SupportAnswerDraft:
    return SupportAnswerDraft(
        message=(
            "(resposta simulada, modo fake) O diagnostico indica sinal Wi-Fi fraco: reinicie a "
            "maquininha e aproxime-a do roteador."
        )
    )


register_fake_default(RouterDecision, _router_default)
register_fake_default(KnowledgeAnswerDraft, _knowledge_answer_default)
register_fake_default(SupportAnswerDraft, _support_answer_default)

_CONCRETE_SYMPTOMS = (
    "nao liga",
    "nao conecta",
    "nao le",
    "travou",
    "recus",
    "erro",
    "offline",
    "sem sinal",
)


def _assessment_default(user_text: str) -> ProblemAssessment:
    """Fake: ha problema entendido se a conversa cita um sintoma concreto; senao, pergunta."""
    lowered = user_text.lower()
    if any(symptom in lowered for symptom in _CONCRETE_SYMPTOMS):
        return ProblemAssessment(
            understood=True,
            category="device_error",
            problem_summary="problema informado pelo cliente (modo fake).",
            confidence=0.8,
        )
    return ProblemAssessment(
        understood=False,
        category="other",
        problem_summary="problema ainda nao detalhado.",
        missing_information="qual e o problema ou a mensagem de erro",
        clarifying_question="Qual e o problema ou a mensagem de erro que aparece na maquininha?",
        confidence=0.4,
    )


def _followup_default(_user_text: str) -> FollowUpClassification:
    return FollowUpClassification(outcome="NEW_INFORMATION", reason="modo fake")


register_fake_default(ProblemAssessment, _assessment_default)
register_fake_default(FollowUpClassification, _followup_default)
