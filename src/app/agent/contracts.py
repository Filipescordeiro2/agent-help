"""Contrato compartilhado entre agentes -- toda implementacao retorna AgentResponse.

Constitution Principio III: cada agente tem responsabilidade unica; nenhum agente retorna
`str`/`dict` solto, sempre uma instancia validada de AgentResponse.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent.state import GraphState
from app.schemas.agent_response import AgentResponse


class Agent(ABC):
    """Interface base que Router, Knowledge e Customer Support Agents implementam."""

    name: str

    @abstractmethod
    async def handle(self, state: GraphState) -> AgentResponse: ...
