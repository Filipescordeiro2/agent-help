"""Avaliacao de qualidade de recuperacao -- usado para validar mudancas no pipeline de RAG."""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.retrievers import RetrievalResult


@dataclass
class RetrievalEvalCase:
    query: str
    expected_document_ids: set[str]


def recall_at_k(results: list[RetrievalResult], expected_document_ids: set[str]) -> float:
    if not expected_document_ids:
        return 1.0
    retrieved_ids = {r.document_id for r in results}
    hits = len(retrieved_ids & expected_document_ids)
    return hits / len(expected_document_ids)


CaseWithResults = tuple[RetrievalEvalCase, list[RetrievalResult]]


def average_recall(cases_with_results: list[CaseWithResults]) -> float:
    if not cases_with_results:
        return 0.0
    scores = [
        recall_at_k(results, case.expected_document_ids) for case, results in cases_with_results
    ]
    return sum(scores) / len(scores)
