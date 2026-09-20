"""Schemas da API de auditoria por sessao (GET /api/v1/audit/...)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TimelineEvent(BaseModel):
    order: int = Field(description="Posicao do evento dentro do turno (1..n)")
    at: datetime
    type: str = Field(description="user_input | node_call | agent_call | tool_call | llm_io | ...")
    label: str = Field(description="Descricao curta em portugues")
    actor: str
    name: str
    node: str | None = None
    status: str
    duration_ms: float | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadados seguros")
    details: dict[str, Any] | None = Field(
        default=None, description="Conteudo mascarado: entrada, retorno, decisao, prompt..."
    )


class TurnAudit(BaseModel):
    """Um turno = uma mensagem do usuario e tudo que o agente fez ate responder."""

    execution_id: str
    message_id: str | None = None
    request_id: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    user_input: str | None = None
    final_status: str | None = None
    final_response: dict[str, Any] | None = None
    explanation: list[str] = Field(
        description="POR QUE o agente respondeu assim, passo a passo, em portugues"
    )
    summary: dict[str, Any] = Field(
        description="Resumo estruturado: rota, agentes, ferramentas, base, web, grounding, tokens"
    )
    timeline: list[TimelineEvent]


class SessionAudit(BaseModel):
    session_id: str
    user_id: str
    channel: str | None = None
    status: str
    created_at: datetime
    total_turns: int
    turns: list[TurnAudit]


class AuditEventOut(TimelineEvent):
    event_id: str
    request_id: str
    session_id: str | None = None
    message_id: str | None = None
    execution_id: str | None = None
