"""GET /api/v1/executions/{id}, POST /api/v1/executions/{id}/resume,
GET /api/v1/sessions/{id}/executions -- rotas finas (delegam a app/agent/checkpoints/
resume.py e app/repository/executions_repository.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.checkpoints import resume as resume_module
from app.repository.executions_repository import Execution, ExecutionsRepository
from app.routers.deps import get_db, require_llm_api_key
from app.schemas.agent_response import AgentResponse

router = APIRouter(prefix="/api/v1", tags=["executions"])


@router.get("/executions/{execution_id}", response_model=Execution)
async def get_execution(execution_id: str, db: AsyncIOMotorDatabase = Depends(get_db)) -> Execution:
    execution = await ExecutionsRepository(db).get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execucao nao encontrada")
    return execution


@router.post(
    "/executions/{execution_id}/resume",
    response_model=AgentResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def resume_execution(
    execution_id: str, db: AsyncIOMotorDatabase = Depends(get_db)
) -> AgentResponse:
    try:
        return await resume_module.resume_execution(db, execution_id)
    except resume_module.ExecutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="execucao nao encontrada") from exc
    except resume_module.OriginalMessageNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/executions", response_model=list[Execution])
async def list_session_executions(
    session_id: str, db: AsyncIOMotorDatabase = Depends(get_db)
) -> list[Execution]:
    return await ExecutionsRepository(db).list_for_session(session_id)
