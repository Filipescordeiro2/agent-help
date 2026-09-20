"""Abstracao de embeddings -- dimensao e modelo lidos de configuracao (research.md #6).

Prioriza o modelo de embeddings configurado via OpenRouter; se o modelo configurado nao
suportar embeddings via OpenRouter no momento da implantacao, este e o unico ponto do
codigo que precisa mudar para apontar a um provedor de embeddings alternativo.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from app.config.settings import get_settings
from app.llm.credentials import resolve_api_key


class FakeEmbeddings:
    """Modo fake para testes/dev offline -- vetor deterministico do tamanho configurado."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 997) / 997 for i in range(self._dimensions)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


@lru_cache
def _fake_embeddings(dimensions: int) -> FakeEmbeddings:
    return FakeEmbeddings(dimensions)


def get_embeddings_client() -> OpenAIEmbeddings | FakeEmbeddings:
    """No modo real nao ha cache: a chave (X-API-Key-LLM) e por requisicao (app/llm/credentials)."""
    settings = get_settings()
    if settings.llm_provider == "fake":
        return _fake_embeddings(settings.embedding_dimensions)
    return OpenAIEmbeddings(
        base_url=settings.openrouter_base_url,
        api_key=resolve_api_key(),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        # O OpenRouter recebe o texto puro: sem isto o cliente OpenAI envia arrays de token ids
        # (tiktoken), formato que provedores compativeis nao aceitam.
        check_embedding_ctx_length=False,
    )


def embed_text(text: str) -> list[float]:
    return get_embeddings_client().embed_query(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embeddings_client().embed_documents(texts)
