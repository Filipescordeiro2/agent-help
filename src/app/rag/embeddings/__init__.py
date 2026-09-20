"""Pipeline de geracao de embeddings para chunks de conhecimento (dimensao configuravel)."""

from __future__ import annotations

from app.llm.embeddings_client import embed_texts


def embed_chunks(chunk_texts: list[str]) -> list[list[float]]:
    if not chunk_texts:
        return []
    return embed_texts(chunk_texts)
