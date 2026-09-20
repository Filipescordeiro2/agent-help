"""SupportCasesRepository -- colecao `support_cases`: o "caso" de suporte de uma sessao.

Um caso e a maquina de estados do atendimento de um problema (ver
`app/agent/customer_support/case_flow.py`):

    COLLECTING_INFO ---(entendeu o problema)---> AWAITING_CONFIRMATION
          |                                            |-- resolveu ------> RESOLVED
          |-- (esgotou perguntas) --> NEEDS_CENTRAL    |-- nao resolveu --> TICKET_OPENED
          |-- (sem solucao) ---------> TICKET_OPENED   `-- outro assunto --> SUPERSEDED

So existe UM caso ativo (COLLECTING_INFO ou AWAITING_CONFIRMATION) por sessao.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class CaseStatus(StrEnum):
    COLLECTING_INFO = "COLLECTING_INFO"  # perguntamos algo ao cliente e aguardamos a resposta
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # passamos a solucao: "deu certo?"
    RESOLVED = "RESOLVED"
    TICKET_OPENED = "TICKET_OPENED"
    NEEDS_CENTRAL = "NEEDS_CENTRAL"  # nao entendemos o problema apos as perguntas permitidas
    SUPERSEDED = "SUPERSEDED"  # o cliente mudou de assunto


ACTIVE_STATUSES = (CaseStatus.COLLECTING_INFO, CaseStatus.AWAITING_CONFIRMATION)


class SolutionAttempt(BaseModel):
    """Uma solucao ja passada ao cliente (o que a LLM/base indicaram e de onde veio)."""

    number: int
    solution: str
    source_urls: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupportCase(BaseModel):
    case_id: str
    session_id: str
    user_id: str
    status: CaseStatus = CaseStatus.COLLECTING_INFO
    category: str | None = None
    # O problema foi de fato entendido (sintoma concreto) e uma solucao foi PROCURADA. Sao a base
    # da trava do chamado: sem elas, nenhum chamado abre (ver services/tickets.py).
    understood: bool = False
    searched: bool = False
    # O cliente pediu um atendente mas ainda faltava entender o problema: abre assim que entender.
    pending_ticket_reason: str | None = None
    problem_summary: str | None = None  # analise previa da LLM ("o problema e ...")
    error_code: str | None = None
    missing_information: str | None = None
    last_question: str | None = None
    clarification_count: int = 0
    attempts: list[SolutionAttempt] = Field(default_factory=list)
    ticket_id: str | None = None
    closed_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class SupportCasesRepository(BaseRepository[SupportCase]):
    collection_name = "support_cases"
    model = SupportCase
    id_field = "case_id"

    async def get_active(self, session_id: str) -> SupportCase | None:
        raw = await self._collection.find_one(
            {"session_id": session_id, "status": {"$in": [s.value for s in ACTIVE_STATUSES]}},
            sort=[("created_at", -1)],
        )
        if raw is None:
            return None
        raw.pop("_id", None)
        return SupportCase.model_validate(raw)

    async def list_for_session(self, session_id: str) -> list[SupportCase]:
        return await self.list(filters={"session_id": session_id}, limit=100)

    async def save(self, case: SupportCase) -> SupportCase:
        """Grava o estado completo do caso (insere ou substitui). Idempotente."""
        case.updated_at = datetime.now(UTC)
        await self._collection.replace_one(
            {"case_id": case.case_id}, case.model_dump(mode="json"), upsert=True
        )
        return case
