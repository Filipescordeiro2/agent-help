"""Liga o Knowledge Agent ao registro de despacho do grafo (AGENT_HANDLERS)."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.graph import AGENT_HANDLERS
from app.agent.knowledge.agent import KnowledgeAgent


def register_knowledge_agent(db: AsyncIOMotorDatabase) -> None:
    AGENT_HANDLERS["knowledge_agent"] = KnowledgeAgent(db)
