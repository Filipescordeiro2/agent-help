"""T069: exclusao mutua NAO bloqueante das execucoes do Feedback Agent (spec FR-009)."""

from __future__ import annotations

import asyncio

import pytest

import app.agent.feedback_agent.runner as runner_module
from app.agent.feedback_agent.concurrency import (
    FeedbackAgentRunInProgress,
    acquire_feedback_agent_lock,
)
from app.agent.feedback_agent.runner import run_feedback_agent
from app.repository.feedback_agent_runs_repository import (
    FeedbackAgentRunsRepository,
    RunStatus,
    TriggerType,
)


async def test_second_acquisition_fails_immediately_instead_of_waiting() -> None:
    async with acquire_feedback_agent_lock():
        with pytest.raises(FeedbackAgentRunInProgress):
            async with acquire_feedback_agent_lock():
                pass


async def test_lock_is_released_after_success_and_after_failure() -> None:
    async with acquire_feedback_agent_lock():
        pass
    with pytest.raises(RuntimeError):
        async with acquire_feedback_agent_lock():
            raise RuntimeError("falha dentro da secao critica")
    async with acquire_feedback_agent_lock():  # nao levanta: foi liberado
        pass


async def test_a_second_run_while_one_is_running_is_rejected_without_a_new_record(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner_module.build_feedback_agent_graph

    class _SlowGraph:
        async def ainvoke(self, state):
            started.set()
            await release.wait()
            return await original(test_db).ainvoke(state)

    monkeypatch.setattr(runner_module, "build_feedback_agent_graph", lambda _db: _SlowGraph())

    first = asyncio.create_task(run_feedback_agent(test_db, TriggerType.SCHEDULED))
    await started.wait()
    with pytest.raises(FeedbackAgentRunInProgress):
        await run_feedback_agent(test_db, TriggerType.MANUAL)
    running = await FeedbackAgentRunsRepository(test_db).list_recent(RunStatus.RUNNING)
    assert len(running) == 1  # so um registro RUNNING; a rejeitada nao criou outro

    release.set()
    finished = await first
    assert finished.status == RunStatus.COMPLETED
    again = await run_feedback_agent(test_db, TriggerType.MANUAL)  # lock liberado
    assert again.status == RunStatus.COMPLETED


async def test_failed_run_also_releases_the_lock(test_db, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_db):
        raise RuntimeError("grafo quebrado")

    monkeypatch.setattr(runner_module, "build_feedback_agent_graph", boom)
    failed = await run_feedback_agent(test_db, TriggerType.MANUAL)
    assert failed.status == RunStatus.FAILED
    monkeypatch.undo()
    assert (await run_feedback_agent(test_db, TriggerType.MANUAL)).status == RunStatus.COMPLETED
