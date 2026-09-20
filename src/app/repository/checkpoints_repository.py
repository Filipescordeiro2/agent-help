"""CheckpointsRepository -- colecao `graph_checkpoints`.

Ver data-model.md secao "Execution & Checkpoint".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class GraphCheckpoint(BaseModel):
    checkpoint_id: str
    execution_id: str
    session_id: str
    graph_state: dict[str, Any]
    node_name: str
    iteration_count: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CheckpointsRepository(BaseRepository[GraphCheckpoint]):
    collection_name = "graph_checkpoints"
    model = GraphCheckpoint
    id_field = "checkpoint_id"

    async def latest_for_execution(self, execution_id: str) -> GraphCheckpoint | None:
        raw_docs = (
            await self._collection.find({"execution_id": execution_id})
            .sort("created_at", -1)
            .to_list(length=1)
        )
        if not raw_docs:
            return None
        return self.model.model_validate(raw_docs[0])
