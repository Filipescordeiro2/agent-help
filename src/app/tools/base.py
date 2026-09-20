"""Base comum para todas as tools (Constitution Principio V).

Toda tool tem schema de entrada/saida tipado (Pydantic), allowlist implicita (apenas os
metodos definidos na classe sao operacoes possiveis -- nenhum comando arbitrario), timeout, e
tratamento de erro consistente. O LLM nunca controla a identidade/autorizacao do chamador --
isso vem do contexto passado pela API (Constitution Principio V).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

import structlog
from pydantic import BaseModel

from app.config.settings import get_settings
from app.observability.instrumentation import instrument_tool_call

logger = structlog.get_logger(__name__)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolAuthorizationError(Exception):
    """Levantado quando uma tool exige autorizacao explicita adicional nao concedida."""


class ToolTimeoutError(Exception):
    """Levantado quando uma tool excede seu timeout configurado."""


class BaseTool(ABC, Generic[InputT, OutputT]):
    name: str
    requires_explicit_authorization: bool = False
    timeout_seconds: float | None = None

    @abstractmethod
    async def _run(self, input_data: InputT) -> OutputT: ...

    async def run(self, input_data: InputT, *, authorized: bool = True) -> OutputT:
        if self.requires_explicit_authorization and not authorized:
            raise ToolAuthorizationError(
                f"tool '{self.name}' exige autorizacao explicita adicional"
            )

        timeout = self.timeout_seconds or get_settings().tool_timeout_seconds
        try:
            return await instrument_tool_call(
                self.name,
                lambda: asyncio.wait_for(self._run(input_data), timeout=timeout),
                input_data,
            )
        except TimeoutError as exc:
            logger.warning("tool_timeout", tool=self.name)
            raise ToolTimeoutError(f"tool '{self.name}' excedeu o timeout de {timeout}s") from exc
