"""T070: agendador embutido (APScheduler) do Feedback Agent (spec FR-007)."""

from __future__ import annotations

import pytest

import app.agent.feedback_agent.scheduler as scheduler_module
from app.agent.feedback_agent.concurrency import FeedbackAgentRunInProgress
from app.config.settings import get_settings
from app.repository.feedback_agent_runs_repository import TriggerType


@pytest.fixture(autouse=True)
async def _clean_scheduler():
    scheduler_module.stop_scheduler()
    yield
    scheduler_module.stop_scheduler()


async def test_registers_a_single_interval_job_with_the_configured_settings(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "feedback_agent_schedule_enabled", True)
    monkeypatch.setattr(settings, "feedback_agent_interval_minutes", 30)

    scheduler = scheduler_module.start_scheduler(test_db)

    assert scheduler is not None and scheduler.running
    jobs = scheduler.get_jobs()
    assert [j.id for j in jobs] == [scheduler_module.JOB_ID]
    job = jobs[0]
    assert job.trigger.interval.total_seconds() == 30 * 60
    assert job.max_instances == 1 and job.coalesce is True


async def test_does_not_register_anything_when_disabled(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "feedback_agent_schedule_enabled", False)
    assert scheduler_module.start_scheduler(test_db) is None
    assert scheduler_module._scheduler is None


async def test_start_and_stop_are_idempotent(test_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "feedback_agent_schedule_enabled", True)
    first = scheduler_module.start_scheduler(test_db)
    second = scheduler_module.start_scheduler(test_db)
    assert first is second and len(first.get_jobs()) == 1

    scheduler_module.stop_scheduler()
    scheduler_module.stop_scheduler()  # nao levanta
    assert scheduler_module._scheduler is None


async def test_the_job_runs_the_agent_as_scheduled(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[TriggerType] = []

    async def fake_run(_db, trigger):
        calls.append(trigger)

    monkeypatch.setattr(scheduler_module, "run_feedback_agent", fake_run)
    await scheduler_module._scheduled_run(test_db)
    assert calls == [TriggerType.SCHEDULED]


async def test_a_round_skipped_because_a_run_is_in_progress_is_not_an_error(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def busy(_db, _trigger):
        raise FeedbackAgentRunInProgress

    monkeypatch.setattr(scheduler_module, "run_feedback_agent", busy)
    await scheduler_module._scheduled_run(test_db)  # nao propaga


async def test_an_unexpected_failure_never_kills_the_scheduler_job(
    test_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_db, _trigger):
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(scheduler_module, "run_feedback_agent", broken)
    await scheduler_module._scheduled_run(test_db)  # nao propaga
