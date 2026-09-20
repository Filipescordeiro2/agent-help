"""O Mongo nasce sempre zerado: subir a aplicacao nao pode gravar seeds/mocks em nenhuma collection."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as app_main
from app.repository.mongodb import client as mongodb_client


async def _noop(*_a, **_k) -> None:
    return None


def test_application_startup_inserts_no_documents(test_db, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "create_indexes", _noop)
    monkeypatch.setattr(mongodb_client, "connect", lambda: test_db)

    with TestClient(app_main.app) as client:
        assert client.get("/health").status_code == 200

    async def _counts() -> dict[str, int]:
        names = await test_db.list_collection_names()
        return {name: await test_db[name].count_documents({}) for name in names}

    import asyncio

    counts = asyncio.run(_counts())
    assert all(count == 0 for count in counts.values()), counts


def test_no_seed_code_ships_in_the_application_package() -> None:
    from pathlib import Path

    package = Path(app_main.__file__).parent
    assert not (package / "repository" / "mongodb" / "seeds").exists()
    offenders = [
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if "seed_playbooks" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
