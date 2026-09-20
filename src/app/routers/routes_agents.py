"""GET /api/v1/agents, GET /api/v1/agents/{name} -- servidos pelo registro estatico."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent.registry import AgentDescriptor, get_agent, list_agents

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("", response_model=list[AgentDescriptor])
async def get_agents() -> list[AgentDescriptor]:
    return list_agents()


@router.get("/{name}", response_model=AgentDescriptor)
async def get_agent_by_name(name: str) -> AgentDescriptor:
    descriptor = get_agent(name)
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"agente '{name}' nao encontrado")
    return descriptor
