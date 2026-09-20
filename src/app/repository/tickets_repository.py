"""TicketsRepository -- colecao `tickets`: chamados abertos para o atendimento humano.

Um chamado e simples de proposito: quem e o cliente, o que ele disse no chat, a analise previa da
LLM (o que entendeu, o que ja foi tentado) e o motivo da abertura. `ticket_id` e o numero que o
cliente recebe (ex.: "TKT-000123"). Um chamado por caso (`case_id` unico).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from app.repository.base import BaseRepository


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class TicketReason(StrEnum):
    NOT_RESOLVED = "NOT_RESOLVED"  # a solucao passada nao resolveu
    NO_SOLUTION_FOUND = "NO_SOLUTION_FOUND"  # entendemos o problema, mas nao ha solucao publicada
    CUSTOMER_REQUESTED_HUMAN = "CUSTOMER_REQUESTED_HUMAN"
    VALIDATION_FAILED = "VALIDATION_FAILED"  # a resposta nao passou no grounding
    PLAYBOOK_EXHAUSTED = "PLAYBOOK_EXHAUSTED"


class TicketCustomer(BaseModel):
    user_id: str
    session_id: str
    channel: str | None = None


class ConversationLine(BaseModel):
    role: str  # "cliente" | "assistente"
    content: str
    at: datetime | None = None


class TicketAnalysis(BaseModel):
    """Analise previa da LLM, para o atendente humano nao recomecar do zero."""

    problem_summary: str | None = None
    category: str | None = None
    error_code: str | None = None
    clarifications_asked: int = 0
    solutions_tried: list[dict[str, Any]] = Field(default_factory=list)
    reason: TicketReason
    handoff_note: str  # resumo pronto para o atendente
    # Itens validados ANTES de abrir (cliente, problema entendido, conversa, solucao tentada...).
    validated: list[str] = Field(default_factory=list)


class Ticket(BaseModel):
    ticket_id: str
    case_id: str
    status: TicketStatus = TicketStatus.OPEN
    reason: TicketReason
    subject: str
    customer: TicketCustomer
    conversation: list[ConversationLine] = Field(default_factory=list)
    analysis: TicketAnalysis
    execution_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TicketsRepository(BaseRepository[Ticket]):
    collection_name = "tickets"
    model = Ticket
    id_field = "ticket_id"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        super().__init__(db)
        self._db = db

    async def get_by_case(self, case_id: str) -> Ticket | None:
        raw = await self._collection.find_one({"case_id": case_id})
        if raw is None:
            return None
        raw.pop("_id", None)
        return Ticket.model_validate(raw)

    async def list_filtered(
        self, *, user_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[Ticket]:
        query: dict[str, Any] = {}
        if user_id:
            query["customer.user_id"] = user_id
        if status:
            query["status"] = status
        raw_docs = await self._collection.find(query).sort("created_at", -1).to_list(length=limit)
        for doc in raw_docs:
            doc.pop("_id", None)
        return [Ticket.model_validate(doc) for doc in raw_docs]

    async def next_number(self) -> int:
        """Sequencial atomico (contador em `counters`) -- nunca repete numero de chamado."""
        doc = await self._db["counters"].find_one_and_update(
            {"_id": "ticket_number"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])
