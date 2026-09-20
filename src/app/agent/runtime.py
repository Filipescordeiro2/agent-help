"""Instancia em tempo de execucao do grafo compilado, ligada ao banco de dados configurado."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.graph import build_graph
from app.agent.nodes.customer_support_node import register_customer_support_agent
from app.agent.nodes.knowledge_node import register_knowledge_agent
from app.agent.nodes.persist_node import persist_node
from app.agent.state import GraphState
from app.repository.mongodb.client import get_database

_compiled_graph = None


def get_compiled_graph(db: AsyncIOMotorDatabase | None = None):
    global _compiled_graph
    if _compiled_graph is None:
        database = db or get_database()

        register_knowledge_agent(database)
        register_customer_support_agent(database)

        async def _persist(state: GraphState) -> GraphState:
            return await persist_node(state, database)

        _compiled_graph = build_graph(persist_fn=_persist)
    return _compiled_graph


def reset_compiled_graph() -> None:
    """Usado por testes para forcar a reconstrucao do grafo contra um banco de teste."""
    global _compiled_graph
    _compiled_graph = None
