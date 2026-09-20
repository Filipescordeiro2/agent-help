"""Re-ranking / filtros adicionais sobre resultados de recuperacao."""

from __future__ import annotations

from app.rag.retrievers import RetrievalResult


def filter_by_min_score(results: list[RetrievalResult], min_score: float) -> list[RetrievalResult]:
    return [r for r in results if r.score >= min_score]


def limit_results(results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
    return results[:top_k]


def rerank(
    results: list[RetrievalResult], *, min_score: float, top_k: int
) -> list[RetrievalResult]:
    """Pipeline padrao de re-ranking: os resultados ja chegam ordenados por score do
    retriever; aqui aplicamos apenas os filtros finais de score minimo e limite."""
    filtered = filter_by_min_score(results, min_score)
    return limit_results(filtered, top_k)
