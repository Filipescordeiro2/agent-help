"""Fluxo de suporte guiado do Customer Support Agent (caso de suporte + chamado).

Objetivo: o agente NUNCA escala nem abre chamado sem antes entender o problema do cliente e tentar
resolver. O desenho e uma maquina de estados por sessao (`support_cases`), com decisoes tomadas em
codigo e a LLM usada so onde ha linguagem natural para interpretar:

1. Entender (`_assess`): mensagem vaga ("preciso de ajuda com a maquininha") -> UMA pergunta ao
   cliente (ate `support_max_clarifications`); com codigo de erro ou sintoma concreto -> segue.
   O codigo de erro e detectado por codigo (barato e confiavel); o resto, pela LLM
   (`ProblemAssessment`).
2. Resolver (`_find_solution`): o problema entendido vira uma busca no Knowledge Agent (base e
   paginas de ajuda, que salva o que achou). Achou -> "Tente fazer isso: ... Deu certo?" e o caso
   fica AGUARDANDO CONFIRMACAO. Nao achou solucao -> abre o chamado ja com a analise.
3. Confirmar (`_follow_up`): "deu certo" encerra; "nao deu certo" (ou "quero um atendente") abre o
   chamado; informacao nova (outro codigo, mais detalhes) gera nova tentativa (limite
   `support_max_solution_attempts`); outro assunto encerra o caso e segue como duvida.
4. Chamado (`app/services/tickets.py`): um por caso, com o cliente, a conversa (mascarada), a
   analise previa da LLM e o motivo; o cliente recebe o numero (ex.: TKT-000123).

O agente e "puro" em relacao ao estado do caso: le do banco, decide e deixa o novo estado em
`state["support_outcome"]`; quem grava e o no `support_commit` (depois da validacao da resposta), de
modo que a retentativa do grounding nunca conta duas vezes uma pergunta ou uma tentativa. So o
chamado e criado na hora (o numero vai na resposta), de forma idempotente por caso.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.contracts import Agent
from app.agent.small_talk import asks_for_human
from app.agent.state import GraphState
from app.config.settings import get_settings
from app.llm.structured_output import get_structured_output
from app.observability.redaction import redact_text, truncate
from app.rag.loaders.web import error_codes
from app.repository.support_cases_repository import CaseStatus, SolutionAttempt, SupportCase
from app.repository.tickets_repository import ConversationLine, TicketReason
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.security.policies import wrap_untrusted_content
from app.services.memory.short_term import format_turns_as_transcript, get_recent_turns
from app.services.tickets import (
    NEEDS_ATTEMPT,
    NEEDS_PROBLEM,
    NEEDS_SEARCH,
    TicketNotReadyError,
    build_conversation,
    missing_ticket_info,
    open_ticket,
)

_PROMPTS = Path(__file__).resolve().parents[1] / "prompts" / "customer_support"
AGENT_NAME = "customer_support_agent"


# --- saidas estruturadas da LLM ------------------------------------------------------------------


class ProblemAssessment(BaseModel):
    """Analise previa do problema: ja da para procurar uma solucao ou falta perguntar?"""

    understood: bool
    category: Literal["device_error", "connectivity", "transaction", "account", "other"]
    problem_summary: str
    error_code: str | None = None
    missing_information: str | None = None
    clarifying_question: str | None = None
    # A ultima mensagem trata de OUTRO assunto (nao responde a pergunta feita): encerra o caso.
    is_new_topic: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class FollowUpClassification(BaseModel):
    """O que o cliente quis dizer ao responder ao "deu certo?"."""

    outcome: Literal["RESOLVED", "NOT_RESOLVED", "WANTS_HUMAN", "NEW_INFORMATION", "UNRELATED"]
    reason: str


class FollowUp(StrEnum):
    RESOLVED = "RESOLVED"
    NOT_RESOLVED = "NOT_RESOLVED"
    WANTS_HUMAN = "WANTS_HUMAN"
    NEW_INFORMATION = "NEW_INFORMATION"
    UNRELATED = "UNRELATED"


@dataclass
class SupportOutcome:
    """Novo estado do caso + acao (para a auditoria); gravado pelo no `support_commit`."""

    case: SupportCase
    action: str
    detail: str | None = None


# --- interpretacao deterministica do "deu certo?" ------------------------------------------------


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s-]", " ", folded)


_NEGATIVE = re.compile(
    r"\b(nao|nunca|continua|persiste|mesmo erro|de novo|nada mudou|segue igual|piorou|"
    r"ainda (esta|ta|com|aparece|da|nao))\b"
)
_UNSURE = re.compile(r"\bnao (sei|entendi|tenho|lembro)\b")
_POSITIVE = re.compile(
    r"\b(deu certo|deu tudo certo|funcionou|resolveu|resolvido|consegui|tudo certo|"
    r"tudo ok|ja esta funcionando|voltou a funcionar|obrigad[oa]|valeu|vlw|sim|isso mesmo|"
    r"perfeito|show)\b"
)


def classify_followup_text(message: str, case: SupportCase) -> FollowUp | None:
    """Respostas obvias ao "deu certo?" (sem LLM). None = ambigua (a LLM decide)."""
    text = _fold(message).strip()
    if not text:
        return None
    new_codes = error_codes(message) - error_codes(case.error_code or "")
    if new_codes:
        return FollowUp.NEW_INFORMATION  # "nao, agora aparece o erro ADQ 1-87"
    if asks_for_human(message):
        return FollowUp.WANTS_HUMAN
    tokens = text.split()
    if _UNSURE.search(text):
        return None  # "nao sei": ambigua, a LLM decide
    if _NEGATIVE.search(text) and len(tokens) <= 12:
        return FollowUp.NOT_RESOLVED
    if _POSITIVE.search(text) and len(tokens) <= 10:
        return FollowUp.RESOLVED
    return None


def display_error_code(code: str) -> str:
    """ "adq483" -> "ADQ 4-83"; "idl108" -> "IDL 1-08"; "s2074" -> "S-2074"."""
    match = re.fullmatch(r"(adq|idl)(\d)(\d{2})", code)
    if match:
        return f"{match.group(1).upper()} {match.group(2)}-{match.group(3)}"
    match = re.fullmatch(r"s(\d{4})", code)
    return f"S-{match.group(1)}" if match else code.upper()


# --- mensagens ao cliente (fixas e revisadas: nada de texto livre onde o fluxo decide) -----------

DEFAULT_QUESTION = (
    "Para eu te ajudar, me conta: qual e o problema ou a mensagem de erro que aparece na tela da "
    'maquininha? (por exemplo: "Erro ADQ 4-83")'
)


def _msg_solution(summary: str, steps: str) -> str:
    return (
        f"Entendi: {summary}\n\nTente fazer isso:\n\n{steps.strip()}\n\n"
        "Deu certo? Se nao resolver, e so me avisar que eu abro um chamado com tudo o que voce me "
        "contou para o nosso atendimento."
    )


_MSG_RESOLVED = "Que bom que deu certo! Se precisar de mais alguma coisa, e so chamar."

_MSG_NEEDS_CENTRAL = (
    "Nao consegui entender o problema com as informacoes que tenho. Para nao te deixar sem "
    "resposta, entre em contato com a central de atendimento da Getnet. Se quiser tentar de novo, "
    "me diga qual e a mensagem de erro que aparece na maquininha."
)


def ticket_message(reason: TicketReason, ticket_id: str, summary: str | None) -> str:
    what = (summary or "o seu problema").rstrip(".")
    if reason == TicketReason.NO_SOLUTION_FOUND:
        head = (
            f"Entendi o seu problema ({what}), mas nao encontrei nos nossos materiais uma solucao "
            "para ele."
        )
    elif reason == TicketReason.CUSTOMER_REQUESTED_HUMAN:
        head = "Combinado, vou passar o seu caso para o nosso atendimento."
    else:
        head = "Entendi, sinto muito que nao tenha resolvido."
    return (
        f"{head} Abri o chamado **{ticket_id}** com tudo o que voce me contou e o que ja "
        "tentamos, para a nossa equipe te ajudar sem voce precisar repetir nada. Guarde esse "
        "numero e aguarde o retorno do atendimento."
    )


# --- o fluxo -------------------------------------------------------------------------------------


class SupportCaseFlow:
    def __init__(self, db: AsyncIOMotorDatabase, knowledge_agent: Agent) -> None:
        self._db = db
        self._knowledge = knowledge_agent

    async def run(self, state: GraphState, case: SupportCase | None) -> AgentResponse:
        message = state["user_message"].message
        if case is None:
            case = SupportCase(
                case_id=str(uuid.uuid4()),
                session_id=state["session_id"],
                user_id=state["user_message"].user_id,
            )
        else:
            case = case.model_copy(deep=True)

        if case.status == CaseStatus.AWAITING_CONFIRMATION:
            follow_up = await self._follow_up(message, case)
            if follow_up == FollowUp.RESOLVED:
                return self._resolved(state, case)
            if follow_up in (FollowUp.NOT_RESOLVED, FollowUp.WANTS_HUMAN):
                reason = (
                    TicketReason.CUSTOMER_REQUESTED_HUMAN
                    if follow_up == FollowUp.WANTS_HUMAN
                    else TicketReason.NOT_RESOLVED
                )
                return await self._ticket(state, case, reason)
            if follow_up == FollowUp.UNRELATED:
                return await self._superseded(state, case)
            # NEW_INFORMATION: entende de novo com o que o cliente acrescentou
            if len(case.attempts) >= get_settings().support_max_solution_attempts:
                return await self._ticket(state, case, TicketReason.NOT_RESOLVED)

        if asks_for_human(message):
            # Pediu atendente: o chamado abre assim que o problema estiver entendido (nunca vazio).
            case.pending_ticket_reason = TicketReason.CUSTOMER_REQUESTED_HUMAN.value

        assessment = await self._assess(state, case)
        if assessment.is_new_topic and case.status == CaseStatus.COLLECTING_INFO:
            return await self._superseded(state, case)
        if not assessment.understood:
            return self._ask(state, case, assessment)

        case.understood = True
        case.problem_summary = assessment.problem_summary
        case.error_code = assessment.error_code
        case.category = assessment.category
        case.missing_information = None
        if case.pending_ticket_reason:
            return await self._ticket(state, case, TicketReason(case.pending_ticket_reason))
        return await self._solve_or_ticket(state, case)

    async def _solve_or_ticket(self, state: GraphState, case: SupportCase) -> AgentResponse:
        """Problema entendido: procura a solucao (achou -> cliente; nao achou -> chamado)."""
        solution = await self._find_solution(state, case)
        if solution is None:
            return await self._ticket(state, case, TicketReason.NO_SOLUTION_FOUND)
        return self._propose(state, case, solution)

    # --- 1. entender ------------------------------------------------------------------------

    async def _user_text(self, state: GraphState) -> str:
        turns = await get_recent_turns(self._db, state["session_id"], limit=10)
        user_turns = [t["content"] for t in turns if t["role"] == "user"]
        current = state["user_message"].message
        if not user_turns or user_turns[-1] != current:
            user_turns.append(current)
        return "\n".join(user_turns)

    async def _assess(self, state: GraphState, case: SupportCase) -> ProblemAssessment:
        all_user_text = await self._user_text(state)
        codes = sorted(error_codes(all_user_text))
        if codes:
            shown = display_error_code(codes[-1])
            return ProblemAssessment(
                understood=True,
                category="device_error",
                problem_summary=f"maquininha exibindo o erro {shown}.",
                error_code=shown,
                confidence=0.95,
            )
        if re.search(r"\bnfc\b", _fold(all_user_text)):
            return ProblemAssessment(
                understood=True,
                category="device_error",
                problem_summary="falha na leitora NFC (pagamento por aproximacao).",
                confidence=0.9,
            )
        turns = await get_recent_turns(self._db, state["session_id"], limit=10)
        transcript = format_turns_as_transcript(turns) or (
            "Cliente: " + state["user_message"].message
        )
        asked = (
            f"Perguntas de esclarecimento ja feitas: {case.clarification_count}. "
            f"Ultima pergunta: {case.last_question or '-'}"
        )
        return await get_structured_output(
            ProblemAssessment,
            [
                {"role": "system", "content": (_PROMPTS / "assessment_v1.md").read_text("utf-8")},
                {
                    "role": "user",
                    "content": wrap_untrusted_content(transcript) + "\n\n" + asked,
                },
            ],
        )

    def _ask(
        self, state: GraphState, case: SupportCase, assessment: ProblemAssessment
    ) -> AgentResponse:
        settings = get_settings()
        if case.clarification_count >= settings.support_max_clarifications:
            case.status = CaseStatus.NEEDS_CENTRAL
            case.closed_reason = "clarificacoes_esgotadas"
            state["support_outcome"] = SupportOutcome(case, "needs_central")
            self._exempt(state)
            return self._response(state, Status.INSUFFICIENT_CONTEXT, _MSG_NEEDS_CENTRAL)
        question = (assessment.clarifying_question or "").strip() or DEFAULT_QUESTION
        case.status = CaseStatus.COLLECTING_INFO
        case.clarification_count += 1
        case.last_question = question
        case.missing_information = assessment.missing_information
        state["support_outcome"] = SupportOutcome(case, "clarification_asked", question)
        self._exempt(state)
        return self._response(state, Status.CLARIFICATION_REQUIRED, question)

    # --- 2. resolver ------------------------------------------------------------------------

    async def _find_solution(self, state: GraphState, case: SupportCase) -> AgentResponse | None:
        query = (case.problem_summary or "").rstrip(".")
        if case.error_code and case.error_code.lower() not in query.lower():
            query = f"{query} {case.error_code}"
        inner: GraphState = GraphState(**state)  # copia rasa: a mensagem do agente de conhecimento
        inner["user_message"] = state["user_message"].model_copy(update={"message": query})
        inner["grounding_context"] = list(state.get("grounding_context", []))
        inner["answer_mode"] = "support_steps"
        response = await self._knowledge.handle(inner)
        case.searched = True
        state["grounding_context"] = inner["grounding_context"]
        if "web_consulted" in inner:
            state["web_consulted"] = inner["web_consulted"]
        return response if response.status == Status.OK else None

    def _propose(
        self, state: GraphState, case: SupportCase, solution: AgentResponse
    ) -> AgentResponse:
        case.status = CaseStatus.AWAITING_CONFIRMATION
        case.attempts.append(
            SolutionAttempt(
                number=len(case.attempts) + 1,
                solution=truncate(solution.message, 3000),
                source_urls=[s.url for s in solution.sources if s.url],
                source_document_ids=[s.document_id for s in solution.sources],
            )
        )
        state["support_outcome"] = SupportOutcome(case, "solution_proposed")
        return AgentResponse(
            status=Status.OK,
            agent=AGENT_NAME,
            message=_msg_solution(case.problem_summary or "o seu problema.", solution.message),
            sources=solution.sources,
            metadata=solution.metadata.model_copy(
                update={"execution_id": state["execution_id"], "grounding_score": None}
            ),
        )

    # --- 3. confirmar -----------------------------------------------------------------------

    async def _follow_up(self, message: str, case: SupportCase) -> FollowUp:
        obvious = classify_followup_text(message, case)
        if obvious is not None:
            return obvious
        result = await get_structured_output(
            FollowUpClassification,
            [
                {"role": "system", "content": (_PROMPTS / "followup_v1.md").read_text("utf-8")},
                {
                    "role": "user",
                    "content": (
                        f"Problema em atendimento: {case.problem_summary}\n"
                        "Ultima solucao indicada ao cliente:\n"
                        + wrap_untrusted_content(
                            case.attempts[-1].solution if case.attempts else ""
                        )
                        + "\n\nResposta do cliente:\n"
                        + wrap_untrusted_content(message)
                    ),
                },
            ],
        )
        return FollowUp(result.outcome)

    def _resolved(self, state: GraphState, case: SupportCase) -> AgentResponse:
        case.status = CaseStatus.RESOLVED
        case.closed_reason = "cliente_confirmou"
        state["support_outcome"] = SupportOutcome(case, "resolved")
        self._exempt(state)
        return self._response(state, Status.OK, _MSG_RESOLVED)

    async def _superseded(self, state: GraphState, case: SupportCase) -> AgentResponse:
        case.status = CaseStatus.SUPERSEDED
        case.closed_reason = "cliente_mudou_de_assunto"
        state["support_outcome"] = SupportOutcome(case, "superseded")
        return await self._knowledge.handle(state)  # a nova pergunta segue como duvida comum

    # --- 4. chamado -------------------------------------------------------------------------

    async def _ticket(
        self, state: GraphState, case: SupportCase, reason: TicketReason
    ) -> AgentResponse:
        """Abre o chamado SO com tudo validado; faltando algo, faz o que falta em vez de abrir."""
        conversation = await self._conversation(state, case)
        missing = missing_ticket_info(case, reason, conversation)
        if missing:
            return await self._complete_before_ticket(state, case, reason, missing)
        try:
            ticket = await open_ticket(
                self._db,
                case=case,
                reason=reason,
                execution_id=state["execution_id"],
                conversation=conversation,
            )
        except TicketNotReadyError as exc:  # a trava do servico e a palavra final
            return await self._complete_before_ticket(state, case, reason, exc.missing)
        case.status = CaseStatus.TICKET_OPENED
        case.ticket_id = ticket.ticket_id
        case.closed_reason = reason.value
        case.pending_ticket_reason = None
        state["support_outcome"] = SupportOutcome(case, "ticket_opened", reason.value)
        self._exempt(state)
        return AgentResponse(
            status=Status.ESCALATION_REQUIRED,
            agent=AGENT_NAME,
            message=ticket_message(reason, ticket.ticket_id, case.problem_summary),
            sources=[],
            metadata=ResponseMetadata(
                execution_id=state["execution_id"], confidence=0.8, ticket_id=ticket.ticket_id
            ),
        )

    async def _complete_before_ticket(
        self, state: GraphState, case: SupportCase, reason: TicketReason, missing: list[str]
    ) -> AgentResponse:
        """Faltam informacoes para o chamado: obtem-nas (pergunta, busca) e SO ENTAO abre."""
        case.pending_ticket_reason = reason.value
        if NEEDS_PROBLEM in missing:
            # Nao entendemos o problema: pergunta (o chamado fica pendente ate entender).
            return self._ask(
                state,
                case,
                ProblemAssessment(
                    understood=False,
                    category="other",
                    problem_summary="",
                    missing_information=NEEDS_PROBLEM,
                    confidence=0.0,
                ),
            )
        if NEEDS_SEARCH in missing or NEEDS_ATTEMPT in missing:
            case.pending_ticket_reason = None  # a busca decide: solucao ou chamado por falta dela
            return await self._solve_or_ticket(state, case)
        # Informacao que nao conseguimos obter aqui (ex.: cliente sem conversa registrada).
        state["support_outcome"] = SupportOutcome(case, "ticket_blocked", "; ".join(missing))
        self._exempt(state)
        return self._response(state, Status.INSUFFICIENT_CONTEXT, _MSG_NEEDS_CENTRAL)

    async def _conversation(self, state: GraphState, case: SupportCase) -> list[ConversationLine]:
        """A conversa da sessao; garante que a mensagem atual do cliente esta nela."""
        lines = await build_conversation(self._db, case.session_id)
        current = redact_text(state["user_message"].message)
        if not any(
            line.role == "cliente" and line.content == truncate(current, 1500) for line in lines
        ):
            lines.append(ConversationLine(role="cliente", content=truncate(current, 1500)))
        return lines

    # --- utilitarios ------------------------------------------------------------------------

    @staticmethod
    def _exempt(state: GraphState) -> None:
        """Mensagem do fluxo (pergunta, agradecimento, chamado) nao tem o que fundamentar."""
        state["grounding_exempt_agents"] = [*state.get("grounding_exempt_agents", []), AGENT_NAME]

    @staticmethod
    def _response(state: GraphState, status: Status, message: str) -> AgentResponse:
        return AgentResponse(
            status=status,
            agent=AGENT_NAME,
            message=message,
            sources=[],
            metadata=ResponseMetadata(execution_id=state["execution_id"], confidence=0.8),
        )
