"""Conexao e ciclo de vida assincrono ao MongoDB (Motor).

Aponta para MONGODB_URI -- localmente `mongodb/mongodb-atlas-local` (via docker-compose),
em producao um cluster Atlas real -- apenas por configuracao (ver research.md #1).
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config.settings import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def connect() -> AsyncIOMotorDatabase:
    """Cria (se necessario) e retorna a conexao com o banco configurado."""
    global _client, _db
    if _db is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongodb_uri)
        _db = _client.get_default_database()
    return _db


def is_connected() -> bool:
    """True quando ja existe uma conexao/banco configurado (sem criar uma nova)."""
    return _db is not None


def get_database() -> AsyncIOMotorDatabase:
    if _db is None:
        return connect()
    return _db


async def close() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


async def ping() -> bool:
    """Usado por /ready para verificar a saude da dependencia MongoDB."""
    try:
        await get_database().command("ping")
        return True
    except Exception:  # noqa: BLE001 -- readiness check deliberadamente amplo
        return False
