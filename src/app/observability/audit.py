"""Emissao de eventos de auditoria -- escreve em AuditEventsRepository com campos seguros."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Literal

import structlog

from app.observability.logging import get_context_ids
from app.repository.audit_events_repository import AuditEvent, AuditEventsRepository
from app.repository.mongodb import client as mongodb_client
from app.repository.mongodb.client import get_database

logger = structlog.get_logger(__name__)


async def emit_audit_event(
    *,
    request_id: str,
    actor: Literal["agent", "tool", "system"],
    actor_name: str,
    event_type: str,
    status: str,
    session_id: str | None = None,
    message_id: str | None = None,
    execution_id: str | None = None,
    node_name: str | None = None,
    duration_ms: float | None = None,
    error_code: str | None = None,
    safe_metadata: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    repo = AuditEventsRepository(get_database())
    event = AuditEvent(
        event_id=str(uuid.uuid4()),
        request_id=request_id,
        session_id=session_id,
        message_id=message_id,
        execution_id=execution_id,
        actor=actor,
        actor_name=actor_name,
        node_name=node_name,
        event_type=event_type,
        status=status,
        duration_ms=duration_ms,
        error_code=error_code,
        safe_metadata=safe_metadata or {},
        details=details,
    )
    await repo.record(event)


@contextmanager
def timed_operation():
    """Context manager utilitario para medir duration_ms de um bloco de codigo."""
    start = time.perf_counter()
    result = {"duration_ms": 0.0}
    try:
        yield result
    finally:
        result["duration_ms"] = (time.perf_counter() - start) * 1000


async def safe_emit_audit_event(
    *,
    actor: Literal["agent", "tool", "system"],
    actor_name: str,
    event_type: str,
    status: str,
    request_id: str | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
    execution_id: str | None = None,
    node_name: str | None = None,
    duration_ms: float | None = None,
    error_code: str | None = None,
    safe_metadata: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Emite um evento de auditoria SEM nunca propagar excecao -- observabilidade jamais derruba
    o caminho de atendimento. Ids ausentes sao preenchidos a partir do contexto corrente; sem
    conexao configurada com o banco, o evento e simplesmente ignorado."""
    if not mongodb_client.is_connected():
        return
    ids = get_context_ids()
    try:
        await emit_audit_event(
            request_id=request_id or ids["request_id"] or f"internal-{uuid.uuid4().hex[:12]}",
            actor=actor,
            actor_name=actor_name,
            event_type=event_type,
            status=status,
            session_id=session_id or ids["session_id"],
            message_id=message_id or ids["message_id"],
            execution_id=execution_id or ids["execution_id"],
            node_name=node_name,
            duration_ms=duration_ms,
            error_code=error_code,
            safe_metadata=safe_metadata,
            details=details,
        )
    except Exception:  # noqa: BLE001 -- nunca propaga (Principio XI, degradacao graciosa)
        logger.warning("audit_emit_failed", reason=event_type)
