"""UserMessageInput: payload de entrada estruturado (FR-001)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LENGTH = 4000


class UserMessageInput(BaseModel):
    """Mensagem bruta do usuario ja convertida em payload estruturado na borda da API."""

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    user_id: str = Field(min_length=1, description="Pre-validado pelo canal upstream (FR-046)")
    session_id: str | None = Field(default=None, description="Ausencia cria uma nova sessao")

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message nao pode ser vazia ou conter apenas espacos")
        return stripped
