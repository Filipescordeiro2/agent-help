"""Execucao do Feedback Agent (agendada ou sob demanda) -- registra um `FeedbackAgentRun`.

Nunca aplica mudancas (Principio XII): apenas cria `FeedbackProposal` em `PENDING_HUMAN_REVIEW`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent.concurrency import acquire_feedback_agent_lock
from app.agent.feedback_agent.graph import build_feedback_agent_graph
from app.agent.feedback_agent.state import FeedbackAgentState
from app.repository.feedback_agent_runs_repository import (
    FeedbackAgentRun,
    FeedbackAgentRunsRepository,
    RunStatus,
    TriggerType,
)

logger = structlog.get_logger(__name__)


async def run_feedback_agent(db: AsyncIOMotorDatabase, trigger: TriggerType) -> FeedbackAgentRun:
    """Levanta `FeedbackAgentRunInProgress` (sem criar registro) se ja houver execucao."""
    async with acquire_feedback_agent_lock():
        return await _run(db, trigger)


async def _run(db: AsyncIOMotorDatabase, trigger: TriggerType) -> FeedbackAgentRun:
    repo = FeedbackAgentRunsRepository(db)
    run = await repo.insert(FeedbackAgentRun(run_id=str(uuid.uuid4()), trigger_type=trigger))

    try:
        graph = build_feedback_agent_graph(db)
        initial: FeedbackAgentState = {"run_id": run.run_id}
        final = await graph.ainvoke(initial)
        processed = sorted(set(final.get("processed_ids", [])))
        blocked = dict(final.get("blocked", {}))
        await repo.update(
            run.run_id,
            {
                "status": RunStatus.COMPLETED.value,
                "finished_at": datetime.now(UTC).isoformat(),
                "feedback_processed_count": len(processed),
                "proposals_created_count": len(final.get("proposals", [])),
                "processed_feedback_ids": processed,
                "blocked_feedback_ids": sorted(blocked),
                "blocked_reasons": blocked,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- run FAILED registrado, nunca stack trace bruto
        logger.error("feedback_agent_run_failed", run_id=run.run_id, reason=type(exc).__name__)
        current = await repo.get(run.run_id)
        await repo.update(
            run.run_id,
            {
                "status": RunStatus.FAILED.value,
                "finished_at": datetime.now(UTC).isoformat(),
                "proposals_created_count": len(current.proposal_ids) if current else 0,
                "error_summary": f"Falha ao executar o Feedback Agent ({type(exc).__name__}).",
            },
        )

    final_run = await repo.get(run.run_id)
    assert final_run is not None
    return final_run
