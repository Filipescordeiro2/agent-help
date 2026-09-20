"""Liga o Customer Support Agent ao registro de despacho do grafo (AGENT_HANDLERS)."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.customer_support.agent import CustomerSupportAgent
from app.agent.graph import AGENT_HANDLERS


def register_customer_support_agent(db: AsyncIOMotorDatabase) -> None:
    AGENT_HANDLERS["customer_support_agent"] = CustomerSupportAgent(db)
