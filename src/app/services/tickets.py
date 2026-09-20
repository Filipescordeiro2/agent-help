"""Servico de chamados (tickets): abre, consulta e lista.

`open_ticket` e o UNICO ponto que cria chamado. Ele monta o pacote que o atendente humano recebe:
os dados do cliente, o que ele disse no chat (mascarado), a analise previa da LLM (o que entendeu e
o que ja foi tentado) e o motivo. E idempotente por caso: chamar de novo para o mesmo caso devolve
o chamado ja aberto (nunca duplica).
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config.settings import get_settings
from app.observability.redaction import redact_text, truncate
from app.repository.messages_repository import MessageSender, MessagesRepository
from app.repository.sessions_repository import SessionsRepository
from app.repository.support_cases_repository import SupportCase
from app.repository.tickets_repository import (
    ConversationLine,
    Ticket,
    TicketAnalysis,
    TicketCustomer,
    TicketReason,
    TicketsRepository,
)

TICKET_PREFIX = "TKT-"
_MAX_LINE_CHARS = 1500

_REASON_TEXT = {
    TicketReason.NOT_RESOLVED: "o cliente informou que a solucao indicada nao resolveu",
    TicketReason.NO_SOLUTION_FOUND: (
        "o problema foi entendido, mas nao ha solucao nos materiais consultados"
    ),
    TicketReason.CUSTOMER_REQUESTED_HUMAN: "o cliente pediu para falar com um atendente",
    TicketReason.VALIDATION_FAILED: "a resposta da IA nao passou na validacao de qualidade",
    TicketReason.PLAYBOOK_EXHAUSTED: "o procedimento automatico se esgotou sem resolver",
}


class TicketNotFoundError(Exception):
    pass


class TicketNotReadyError(Exception):
    """O chamado NAO pode ser aberto: faltam informacoes (`missing`, em portugues)."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("chamado incompleto: " + "; ".join(missing))
        self.missing = missing


# Rotulos estaveis das exigencias (o fluxo de suporte decide o que fazer com cada uma).
NEEDS_CUSTOMER = "cliente identificado"
NEEDS_PROBLEM = "problema entendido"
NEEDS_CONVERSATION = "conversa do cliente registrada"
NEEDS_SEARCH = "busca de solucao realizada"
NEEDS_ATTEMPT = "solucao ja indicada ao cliente"


def missing_ticket_info(
    case: SupportCase, reason: TicketReason, conversation: list[ConversationLine]
) -> list[str]:
    """Tudo o que ainda falta para o chamado poder abrir (lista vazia = pronto).

    Regras (todas obrigatorias): cliente e sessao identificados; problema ENTENDIDO (sintoma
    concreto, com resumo); ao menos uma mensagem do cliente na conversa; e, conforme o motivo, a
    prova de que o agente tentou resolver: solucao indicada (nao resolveu / validacao) ou busca
    feita sem achar (sem solucao). Pedido de atendente exige so o problema entendido."""
    missing: list[str] = []
    if not case.user_id or not case.session_id:
        missing.append(NEEDS_CUSTOMER)
    if not (case.understood and (case.problem_summary or "").strip()):
        missing.append(NEEDS_PROBLEM)
    if not any(line.role == "cliente" and line.content.strip() for line in conversation):
        missing.append(NEEDS_CONVERSATION)
    if reason in (TicketReason.NOT_RESOLVED, TicketReason.VALIDATION_FAILED) and not case.attempts:
        missing.append(NEEDS_ATTEMPT)
    if reason == TicketReason.NO_SOLUTION_FOUND and not case.searched:
        missing.append(NEEDS_SEARCH)
    return missing


def format_ticket_number(sequence: int) -> str:
    return f"{TICKET_PREFIX}{sequence:06d}"


async def build_conversation(db: AsyncIOMotorDatabase, session_id: str) -> list[ConversationLine]:
    """O que foi dito na sessao (cliente e assistente), do mais antigo ao mais novo, mascarado."""
    limit = get_settings().support_ticket_conversation_max_messages
    messages = await MessagesRepository(db).list_for_session(session_id, limit=1000)
    messages.sort(key=lambda m: m.created_at)
    lines: list[ConversationLine] = []
    for message in messages[-limit:]:
        text = message.payload.get("message")
        if not text:
            continue
        lines.append(
            ConversationLine(
                role="cliente" if message.sender == MessageSender.USER else "assistente",
                content=truncate(redact_text(str(text)), _MAX_LINE_CHARS),
                at=message.created_at,
            )
        )
    return lines


def _handoff_note(case: SupportCase, reason: TicketReason) -> str:
    parts = [f"Motivo: {_REASON_TEXT[reason]}."]
    if case.problem_summary:
        parts.append(f"Problema entendido: {case.problem_summary}")
    if case.error_code:
        parts.append(f"Codigo de erro: {case.error_code}.")
    if case.attempts:
        parts.append(f"Solucoes ja indicadas ao cliente: {len(case.attempts)}.")
    if case.clarification_count:
        parts.append(f"Perguntas feitas ao cliente: {case.clarification_count}.")
    return " ".join(parts)


def _validated_items(case: SupportCase, reason: TicketReason) -> list[str]:
    """O que foi conferido antes de abrir (fica no chamado, para o atendente confiar no pacote)."""
    items = [NEEDS_CUSTOMER, NEEDS_PROBLEM, NEEDS_CONVERSATION]
    if case.attempts:
        items.append(NEEDS_ATTEMPT)
    if case.searched:
        items.append(NEEDS_SEARCH)
    return items


def _subject(case: SupportCase) -> str:
    base = case.problem_summary or "Problema informado pelo cliente"
    return truncate(base, 120)


async def open_ticket(
    db: AsyncIOMotorDatabase,
    *,
    case: SupportCase,
    reason: TicketReason,
    execution_id: str | None = None,
    conversation: list[ConversationLine] | None = None,
) -> Ticket:
    """Abre o chamado SE estiver completo; senao levanta `TicketNotReadyError` (nada e gravado)."""
    repo = TicketsRepository(db)
    existing = await repo.get_by_case(case.case_id)
    if existing is not None:
        return existing

    lines = (
        conversation if conversation is not None else await build_conversation(db, case.session_id)
    )
    missing = missing_ticket_info(case, reason, lines)
    if missing:
        raise TicketNotReadyError(missing)

    session = await SessionsRepository(db).get(case.session_id)
    ticket = Ticket(
        ticket_id=format_ticket_number(await repo.next_number()),
        case_id=case.case_id,
        reason=reason,
        subject=_subject(case),
        customer=TicketCustomer(
            user_id=case.user_id,
            session_id=case.session_id,
            channel=session.channel if session else None,
        ),
        conversation=lines,
        analysis=TicketAnalysis(
            problem_summary=case.problem_summary,
            category=case.category,
            error_code=case.error_code,
            clarifications_asked=case.clarification_count,
            solutions_tried=[
                {
                    "number": a.number,
                    "solution": truncate(a.solution, _MAX_LINE_CHARS),
                    "source_urls": a.source_urls,
                }
                for a in case.attempts
            ],
            reason=reason,
            handoff_note=_handoff_note(case, reason),
            validated=_validated_items(case, reason),
        ),
        execution_id=execution_id,
    )
    await repo.insert(ticket)
    return ticket


async def get_ticket(db: AsyncIOMotorDatabase, ticket_id: str) -> Ticket:
    ticket = await TicketsRepository(db).get(ticket_id.strip().upper())
    if ticket is None:
        raise TicketNotFoundError(ticket_id)
    return ticket


async def list_tickets(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Ticket]:
    return await TicketsRepository(db).list_filtered(user_id=user_id, status=status, limit=limit)
