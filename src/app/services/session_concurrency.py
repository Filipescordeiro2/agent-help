"""Tratamento seguro de concorrencia por sessao (spec FR-038).

Serializa o processamento de mensagens da MESMA sessao com um lock em memoria por
`session_id` -- duas mensagens concorrentes na mesma sessao nunca sao processadas em
paralelo pelo grafo (evita estado de sessao/checkpoint corrompido por escrita concorrente).
Mensagens de sessoes diferentes continuam totalmente paralelas entre si.

A idempotencia para operacoes nao naturalmente repetiveis (ex.: nao duplicar uma escalacao
para o mesmo evento) e tratada na camada de dominio -- ver
`app/tools/mocks/customer_data.py::create_ticket`, que reaproveita um chamado aberto
existente para o mesmo assunto em vez de criar um novo.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_session_lock(session_id: str) -> asyncio.Lock:
    return _session_locks[session_id]


class acquire_session_lock:
    """Context manager assincrono: `async with acquire_session_lock(session_id): ...`"""

    def __init__(self, session_id: str) -> None:
        self._lock = get_session_lock(session_id)

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(self, *_exc_info: object) -> None:
        self._lock.release()
