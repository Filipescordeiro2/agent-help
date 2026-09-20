"""Exclusao mutua das execucoes do Feedback Agent (spec FR-009, SC-008).

Lock global de processo unico (mesmo padrao de `services/session_concurrency.py`): o servico roda
como um unico processo por container, entao um `asyncio.Lock` basta. Diferente do lock de
sessao, este e NAO bloqueante -- uma segunda execucao (agendada ou manual) enquanto outra roda
falha imediatamente com `FeedbackAgentRunInProgress`.
"""

from __future__ import annotations

import asyncio


class FeedbackAgentRunInProgress(Exception):
    """Ja existe uma execucao do Feedback Agent em andamento."""


_run_lock = asyncio.Lock()


def reset_lock() -> None:
    """Usado por testes para descartar o estado do lock entre casos."""
    global _run_lock
    _run_lock = asyncio.Lock()


class acquire_feedback_agent_lock:
    """`async with acquire_feedback_agent_lock(): ...` -- levanta se ja houver execucao."""

    async def __aenter__(self) -> None:
        if _run_lock.locked():
            raise FeedbackAgentRunInProgress
        await _run_lock.acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        _run_lock.release()
