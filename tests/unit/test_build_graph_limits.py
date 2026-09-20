"""T053: teste unitario do limite de iteracoes e timeout por no/execucao do StateGraph."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.contracts import Agent
from app.agent.graph import (
    AGENT_HANDLERS,
    IterationLimitExceeded,
    dispatch_node,
)
from app.agent.state import new_state
from app.config.settings import get_settings
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.schemas.router import Intent, RouterDecision
from app.schemas.user_message import UserMessageInput


def _state_with_target(target_agent: str):
    state = new_state(
        session_id="s",
        execution_id="e",
        request_id="r",
        user_message=UserMessageInput(message="oi", user_id="u"),
    )
    state["routing_decision"] = RouterDecision(
        intent=Intent.KNOWLEDGE,
        target_agent=target_agent,
        confidence=0.9,
        requires_clarification=False,
        reason_code="X",
    )
    return state


async def test_dispatch_node_raises_when_iteration_limit_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_graph_iterations", 1)

    state = _state_with_target("nonexistent_agent")
    state["iteration_count"] = 5

    with pytest.raises(IterationLimitExceeded):
        await dispatch_node(state)


async def test_dispatch_node_timeout_becomes_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estourar o prazo do agente nao derruba a requisicao: encaminha ao atendimento."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_timeout_seconds", 0.05)

    class SlowAgent(Agent):
        name = "slow_agent"

        async def handle(self, _state) -> AgentResponse:
            await asyncio.sleep(1)
            return AgentResponse(
                status=Status.OK,
                agent=self.name,
                message="tarde demais",
                metadata=ResponseMetadata(execution_id="e", confidence=0.5),
            )

    AGENT_HANDLERS["slow_agent"] = SlowAgent()
    try:
        state = _state_with_target("slow_agent")
        result = await dispatch_node(state)
        response = result["final_response"]
        assert response.status == Status.ESCALATION_REQUIRED
        assert response.metadata.error_code == "AGENT_TIMEOUT"
        assert response.message
    finally:
        del AGENT_HANDLERS["slow_agent"]


async def test_dispatch_node_invokes_registered_handler() -> None:
    class EchoAgent(Agent):
        name = "echo_agent"

        async def handle(self, _state) -> AgentResponse:
            return AgentResponse(
                status=Status.OK,
                agent=self.name,
                message="echo",
                metadata=ResponseMetadata(execution_id="e", confidence=1.0),
            )

    AGENT_HANDLERS["echo_agent"] = EchoAgent()
    try:
        state = _state_with_target("echo_agent")
        result = await dispatch_node(state)
        assert result["final_response"].message == "echo"
    finally:
        del AGENT_HANDLERS["echo_agent"]
