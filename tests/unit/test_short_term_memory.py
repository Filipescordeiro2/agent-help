"""T101: teste unitario da montagem de memoria de curto prazo (spec FR-031)."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.repository.messages_repository import (
    Message,
    MessageSender,
    MessagesRepository,
)
from app.services.memory.short_term import format_turns_as_transcript, get_recent_turns


@pytest.fixture
def db():
    return AsyncMongoMockClient()["getnet_test"]


async def _insert(
    db, *, session_id: str, sender: MessageSender, text: str, message_id: str
) -> None:
    await MessagesRepository(db).insert(
        Message(
            message_id=message_id, session_id=session_id, sender=sender, payload={"message": text}
        )
    )


async def test_get_recent_turns_returns_empty_list_for_new_session(db) -> None:
    turns = await get_recent_turns(db, "sess_new")
    assert turns == []


async def test_get_recent_turns_preserves_chronological_order(db) -> None:
    await _insert(
        db, session_id="s1", sender=MessageSender.USER, text="primeira pergunta", message_id="m1"
    )
    await _insert(
        db, session_id="s1", sender=MessageSender.SYSTEM, text="primeira resposta", message_id="m2"
    )
    await _insert(
        db, session_id="s1", sender=MessageSender.USER, text="segunda pergunta", message_id="m3"
    )

    turns = await get_recent_turns(db, "s1")

    assert [t["content"] for t in turns] == [
        "primeira pergunta",
        "primeira resposta",
        "segunda pergunta",
    ]
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"


async def test_get_recent_turns_respects_limit_keeping_the_most_recent(db) -> None:
    for i in range(10):
        await _insert(
            db, session_id="s1", sender=MessageSender.USER, text=f"mensagem {i}", message_id=f"m{i}"
        )

    turns = await get_recent_turns(db, "s1", limit=3)

    assert len(turns) == 3
    assert turns[-1]["content"] == "mensagem 9"


async def test_get_recent_turns_ignores_other_sessions(db) -> None:
    await _insert(
        db, session_id="s1", sender=MessageSender.USER, text="da sessao 1", message_id="m1"
    )
    await _insert(
        db, session_id="s2", sender=MessageSender.USER, text="da sessao 2", message_id="m2"
    )

    turns = await get_recent_turns(db, "s1")

    assert len(turns) == 1
    assert turns[0]["content"] == "da sessao 1"


def test_format_turns_as_transcript_empty_returns_empty_string() -> None:
    assert format_turns_as_transcript([]) == ""


def test_format_turns_as_transcript_labels_roles_in_portuguese() -> None:
    transcript = format_turns_as_transcript(
        [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "ola"}]
    )
    assert "Cliente: oi" in transcript
    assert "Assistente: ola" in transcript
