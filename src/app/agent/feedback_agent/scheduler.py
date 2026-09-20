"""Agendamento embutido do Feedback Agent (APScheduler, spec FR-007).

Um unico job (`feedback_agent_run`) no intervalo `feedback_agent_interval_minutes`, com
`max_instances=1` e `coalesce=True`. Se uma execucao manual estiver rodando quando o job
disparar, a rodada agendada e simplesmente pulada (nao e erro).
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent.concurrency import FeedbackAgentRunInProgress
from app.agent.feedback_agent.runner import run_feedback_agent
from app.config.settings import get_settings
from app.llm.credentials import has_api_key
from app.repository.feedback_agent_runs_repository import TriggerType

logger = structlog.get_logger(__name__)

JOB_ID = "feedback_agent_run"

_scheduler: AsyncIOScheduler | None = None


async def _scheduled_run(db: AsyncIOMotorDatabase) -> None:
    # Rodada agendada nao tem requisicao (logo, nem X-API-Key-LLM): sem chave no ambiente ela e
    # pulada. Para processar feedbacks, dispare POST /feedback-agent/run enviando o cabecalho.
    if get_settings().llm_provider != "fake" and not has_api_key():
        logger.info("feedback_agent_scheduled_run_skipped", reason="no_llm_api_key")
        return
    try:
        await run_feedback_agent(db, TriggerType.SCHEDULED)
    except FeedbackAgentRunInProgress:
        logger.info("feedback_agent_scheduled_run_skipped", reason="run_in_progress")
    except Exception:  # noqa: BLE001 -- o agendador nunca deve morrer por uma rodada
        logger.error("feedback_agent_scheduled_run_failed")


def start_scheduler(db: AsyncIOMotorDatabase) -> AsyncIOScheduler | None:
    """Idempotente: chamar de novo com o agendador ativo devolve o mesmo. `None` se desligado."""
    global _scheduler
    settings = get_settings()
    if not settings.feedback_agent_schedule_enabled:
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _scheduled_run,
        "interval",
        minutes=settings.feedback_agent_interval_minutes,
        args=[db],
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("feedback_agent_scheduler_started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
        except RuntimeError:  # loop de eventos ja encerrado (ex.: fim do processo/teste)
            logger.warning("feedback_agent_scheduler_shutdown_skipped")
    _scheduler = None
