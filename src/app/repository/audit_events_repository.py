"""AuditEventsRepository -- colecao `audit_events` (data-model.md SS Audit Event).

Restrita a uma allowlist de campos seguros -- nunca segredos ou dados pessoais/financeiros
sensiveis (Constitution Principio IX).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository

# Allowlist explicita de chaves permitidas em safe_metadata -- nunca uma denylist.
SAFE_METADATA_ALLOWLIST = frozenset(
    {
        "intent",
        "reason_code",
        "confidence",
        "status",
        "tool_name",
        "prompt_version",
        "agent_version",
        "iteration_count",
        "score",
        "top_k",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "model",
        "method",
        "proposal_id",
        "action_type",
        "trigger_type",
        "run_id",
    }
)


def filter_safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metadata.items() if k in SAFE_METADATA_ALLOWLIST}


class AuditEvent(BaseModel):
    event_id: str
    request_id: str
    session_id: str | None = None
    message_id: str | None = None
    execution_id: str | None = None
    actor: Literal["agent", "tool", "system"]
    actor_name: str
    node_name: str | None = None
    event_type: str
    status: str
    duration_ms: float | None = None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    safe_metadata: dict[str, Any] = Field(default_factory=dict)
    # Conteudo ja MASCARADO/TRUNCADO (observability/redaction.py): entrada do usuario, retornos
    # de agentes/nos/ferramentas, decisoes. Fonte da API de auditoria por sessao.
    details: dict[str, Any] | None = None


class AuditEventsRepository(BaseRepository[AuditEvent]):
    collection_name = "audit_events"
    model = AuditEvent
    id_field = "event_id"

    async def record(self, event: AuditEvent) -> AuditEvent:
        event.safe_metadata = filter_safe_metadata(event.safe_metadata)
        return await self.insert(event)

    async def _ordered(self, query: dict[str, Any], limit: int) -> list[AuditEvent]:
        # created_at (ISO, mesma precisao) + _id (monotonico por processo) => ordem cronologica
        raw = (
            await self._collection.find(query)
            .sort([("created_at", 1), ("_id", 1)])
            .to_list(length=limit)
        )
        return [self.model.model_validate(doc) for doc in raw]

    async def list_for_session(
        self, session_id: str, *, event_types: list[str] | None = None, limit: int = 5000
    ) -> list[AuditEvent]:
        query: dict[str, Any] = {"session_id": session_id}
        if event_types:
            query["event_type"] = {"$in": event_types}
        return await self._ordered(query, limit)

    async def list_for_execution(self, execution_id: str, limit: int = 2000) -> list[AuditEvent]:
        return await self._ordered({"execution_id": execution_id}, limit)
