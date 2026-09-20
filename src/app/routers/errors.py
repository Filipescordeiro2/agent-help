"""Erros estruturados das rotas -- sempre `AgentResponse(status=ERROR)`, nunca excecao crua."""

from __future__ import annotations

import uuid

from fastapi.responses import JSONResponse

from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata


def structured_error(status_code: int, error_code: str, message: str) -> JSONResponse:
    response = AgentResponse(
        status=Status.ERROR,
        agent="api",
        message=message,
        metadata=ResponseMetadata(
            execution_id=str(uuid.uuid4()), confidence=0.0, error_code=error_code
        ),
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))
