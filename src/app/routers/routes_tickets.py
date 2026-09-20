"""API de chamados (tickets) abertos pelo suporte guiado.

Rota fina -- delega a app/services/tickets.py. Somente leitura de proposito: o chamado nasce no
fluxo de atendimento (quando o cliente nao teve o problema resolvido) e o atendimento humano o
trata fora desta plataforma. Protegida pelo token do canal interno como toda /api/v1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.tickets_repository import Ticket
from app.routers.deps import get_db
from app.services import tickets as service

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


@router.get("", response_model=list[Ticket])
async def list_tickets(
    user_id: str | None = Query(default=None, description="Filtra pelo cliente"),
    status: str | None = Query(default=None, description="OPEN | IN_PROGRESS | CLOSED"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[Ticket]:
    """Chamados mais recentes primeiro."""
    return await service.list_tickets(db, user_id=user_id, status=status, limit=limit)


@router.get("/{ticket_id}", response_model=Ticket)
async def get_ticket(ticket_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Ticket:
    """Chamado completo: cliente, conversa (mascarada), analise previa da LLM e motivo."""
    try:
        return await service.get_ticket(db, ticket_id)
    except service.TicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail="chamado nao encontrado") from exc
