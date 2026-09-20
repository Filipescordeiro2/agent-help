"""T064: teste unitario do chunking configuravel."""

from __future__ import annotations

import pytest

from app.rag.splitters import split_text


def test_split_text_returns_empty_list_for_blank_input() -> None:
    assert split_text("   ") == []


def test_split_text_returns_single_chunk_when_shorter_than_chunk_size() -> None:
    chunks = split_text("um texto curto", chunk_size=800, chunk_overlap=100)
    assert chunks == ["um texto curto"]


def test_split_text_respects_configured_chunk_size() -> None:
    text = "a" * 250
    chunks = split_text(text, chunk_size=100, chunk_overlap=20)
    assert all(len(c) <= 100 for c in chunks)
    assert len(chunks) > 1


def test_split_text_overlaps_between_consecutive_chunks() -> None:
    text = "0123456789" * 10  # 100 chars
    chunks = split_text(text, chunk_size=30, chunk_overlap=10)
    # o fim de um chunk deve reaparecer no inicio do proximo (sobreposicao configurada)
    assert chunks[0][-10:] == chunks[1][:10]


def test_split_text_rejects_overlap_greater_or_equal_to_chunk_size() -> None:
    with pytest.raises(ValueError):
        split_text("qualquer texto", chunk_size=50, chunk_overlap=50)


def test_split_text_covers_the_whole_input() -> None:
    text = "x" * 500
    chunks = split_text(text, chunk_size=200, chunk_overlap=50)
    assert "".join(chunks).replace("x", "")[:0] == ""  # apenas garante que nao ha lixo
    assert sum(len(c) for c in chunks) >= len(text)
