"""API de auditoria: informe o session_id e veja TUDO que o agente fez -- entrada do usuario,
Router, sub-agentes, nos, ferramentas, chamadas ao modelo, retornos e uma explicacao do porque
da resposta.

Rota fina -- delega a app/services/audit.py. O conteudo sensivel (cartao, CPF/CNPJ, e-mail,
telefone, chaves) ja foi MASCARADO na gravacao. Protegida pelo token do canal interno como toda
/api/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.routers.deps import get_db
from app.schemas.audit import AuditEventOut, SessionAudit, TurnAudit
from app.services import audit as service

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

_INCLUDE_LLM = Query(
    default=True, description="Inclui os prompts e saidas dos modelos (eventos llm_io)"
)


@router.get("/sessions/{session_id}", response_model=SessionAudit)
async def audit_session(
    session_id: str,
    include_llm: bool = _INCLUDE_LLM,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> SessionAudit:
    """Fluxo completo de uma sessao, turno a turno (uma mensagem do usuario = um turno)."""
    try:
        return await service.get_session_audit(db, session_id, include_llm=include_llm)
    except service.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from exc


@router.get("/sessions/{session_id}/events", response_model=list[AuditEventOut])
async def audit_session_events(
    session_id: str,
    event_type: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: int = Query(default=0, ge=0),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[AuditEventOut]:
    """Eventos brutos da sessao em ordem cronologica (filtre por `event_type`)."""
    try:
        return await service.list_session_events(
            db, session_id, event_type=event_type, limit=limit, cursor=cursor
        )
    except service.SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from exc


@router.get("/executions/{execution_id}", response_model=TurnAudit)
async def audit_execution(
    execution_id: str,
    include_llm: bool = _INCLUDE_LLM,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> TurnAudit:
    """Um unico turno (execucao) -- o `execution_id` vem em `metadata.execution_id` da resposta."""
    try:
        return await service.get_execution_audit(db, execution_id, include_llm=include_llm)
    except service.ExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail="execucao nao encontrada") from exc
