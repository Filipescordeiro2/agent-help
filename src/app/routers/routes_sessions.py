"""POST/GET /api/v1/sessions, POST /messages, POST /close -- rotas finas, sem logica de negocio
(delegam a app/services/sessions.py e app/agent/)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.repository.sessions_repository import Session
from app.routers.deps import require_llm_api_key
from app.routers.identity import IdentityError, IdentityHeaders, get_identity_headers
from app.schemas.agent_response import AgentResponse
from app.schemas.user_message import MAX_MESSAGE_LENGTH, UserMessageInput
from app.services import sessions as service

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    # Opcionais no corpo: `X-User-Id` / `X-Channel` (cabecalhos) tem o mesmo efeito.
    user_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-User-Id.",
        json_schema_extra={"deprecated": True},
    )
    channel: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Channel.",
        json_schema_extra={"deprecated": True},
    )


class MessageRequest(BaseModel):
    """Corpo da mensagem. `user_id`/`session_id` sao opcionais aqui: podem (e devem) vir nos
    cabecalhos `X-User-Id` / `X-Session-Id`; se vierem nos dois, precisam ser iguais."""

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    user_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-User-Id.",
        json_schema_extra={"deprecated": True},
    )
    session_id: str | None = Field(
        default=None,
        description="Obsoleto: envie no cabecalho X-Session-Id.",
        json_schema_extra={"deprecated": True},
    )


@router.post("", response_model=Session)
async def create_session(
    payload: CreateSessionRequest | None = None,
    ids: IdentityHeaders = Depends(get_identity_headers),
) -> Session:
    body = payload or CreateSessionRequest()
    user_id = ids.resolve("user_id", body.user_id)
    channel = ids.resolve("channel", body.channel, required=False)
    return await service.create_session(user_id, channel)


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: str) -> Session:
    try:
        return await service.get_session(session_id)
    except service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from exc


@router.post(
    "/{session_id}/messages",
    response_model=AgentResponse,
    dependencies=[Depends(require_llm_api_key)],
)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    ids: IdentityHeaders = Depends(get_identity_headers),
) -> AgentResponse:
    user_id = ids.resolve("user_id", payload.user_id)
    # A sessao vem da URL; `X-Session-Id` e o `session_id` do corpo, se enviados, devem coincidir.
    for other in (ids.session_id, ids.resolve("session_id", payload.session_id, required=False)):
        if other is not None and other != session_id:
            raise IdentityError(
                "IDENTITY_MISMATCH", "session_id difere do da URL; envie o mesmo valor."
            )
    user_message = UserMessageInput(message=payload.message, user_id=user_id, session_id=session_id)
    try:
        return await service.process_message(session_id, user_message)
    except service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from exc
    except service.SessionClosedError as exc:
        raise HTTPException(status_code=409, detail="sessao encerrada") from exc


@router.post("/{session_id}/close", response_model=Session)
async def close_session(session_id: str) -> Session:
    try:
        return await service.close_session(session_id)
    except service.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="sessao nao encontrada") from exc
