"""Checkpointer do LangGraph sobre MongoDB (research.md #2).

Garante que "checkpoints e recuperacao de sessao" usem exclusivamente MongoDB, sem introduzir
um segundo mecanismo de persistencia so para checkpoints (Constitution Principio I / VI).
"""

from __future__ import annotations

import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.state import GraphState, serialize_state
from app.repository.checkpoints_repository import (
    CheckpointsRepository,
    GraphCheckpoint,
)


class MongoCheckpointer:
    """Salva/recupera checkpoints de execucao do grafo na colecao `graph_checkpoints`."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._repo = CheckpointsRepository(db)

    async def save(self, state: GraphState, node_name: str) -> GraphCheckpoint:
        checkpoint = GraphCheckpoint(
            checkpoint_id=str(uuid.uuid4()),
            execution_id=state["execution_id"],
            session_id=state["session_id"],
            graph_state=serialize_state(state),
            node_name=node_name,
            iteration_count=state.get("iteration_count", 0),
        )
        return await self._repo.insert(checkpoint)

    async def latest(self, execution_id: str) -> GraphCheckpoint | None:
        return await self._repo.latest_for_execution(execution_id)

    @staticmethod
    def deserialize(raw_state: dict[str, Any]) -> dict[str, Any]:
        """Retorna o dict bruto do estado salvo -- o chamador reidrata os schemas necessarios."""
        return raw_state
