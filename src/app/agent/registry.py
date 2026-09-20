"""Registro estatico de agentes -- exposto via GET /api/v1/agents.

Agentes sao codigo versionado, nao dado de negocio (data-model.md secao "Agent").
"""

from __future__ import annotations

from pydantic import BaseModel


class AgentDescriptor(BaseModel):
    name: str
    description: str
    version: str
    status: str  # "enabled" | "disabled"
    capabilities: list[str]


AGENT_REGISTRY: dict[str, AgentDescriptor] = {
    "router_agent": AgentDescriptor(
        name="router_agent",
        description="Ponto de entrada logico -- classifica intencao e decide roteamento.",
        version="1",
        status="enabled",
        capabilities=["routing"],
    ),
    "knowledge_agent": AgentDescriptor(
        name="knowledge_agent",
        description="Responde duvidas via RAG e busca semantica vetorizada.",
        version="1",
        status="enabled",
        capabilities=["KNOWLEDGE"],
    ),
    "customer_support_agent": AgentDescriptor(
        name="customer_support_agent",
        description="Trata solicitacoes de atendimento via dados de cliente e Playbooks.",
        version="1",
        status="enabled",
        capabilities=["CUSTOMER_SUPPORT"],
    ),
}


def list_agents() -> list[AgentDescriptor]:
    return list(AGENT_REGISTRY.values())


def get_agent(name: str) -> AgentDescriptor | None:
    return AGENT_REGISTRY.get(name)


def agent_can_handle(agent_name: str, intent: str) -> bool:
    """Validacao cruzada de capacidade -- usada pelo router_node antes do despacho (FR-010)."""
    descriptor = AGENT_REGISTRY.get(agent_name)
    if descriptor is None or descriptor.status != "enabled":
        return False
    return intent in descriptor.capabilities
