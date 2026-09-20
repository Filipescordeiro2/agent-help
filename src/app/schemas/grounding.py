"""Saida estruturada do no de grounding (Constitution Principio X, spec FR-021).

Interno: nunca exposto diretamente por uma rota -- o resultado e projetado em
`ResponseMetadata.grounding_score` / `grounding_reasoning`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GroundingEvaluation(BaseModel):
    score: int = Field(
        ge=0, le=5, description="0 = nao relacionado/alucinado; 5 = totalmente aderente"
    )
    reasoning: str = Field(description="justificativa curta, sem PII/segredos")
    adheres_to_question: bool
    uses_context_correctly: bool
    no_unsupported_claims: bool
    is_complete: bool
