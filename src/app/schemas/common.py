"""Schemas compartilhados pelo contrato universal de I/O (ver data-model.md)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """Referência a uma fonte usada para fundamentar uma resposta (RAG)."""

    document_id: str
    chunk_id: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    url: str | None = None  # preenchido quando a fonte e uma pagina web (RAG web)


class ResponseMetadata(BaseModel):
    """Metadados seguros de toda resposta de agente (nunca raciocínio privado do modelo)."""

    execution_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    grounded_in_sources: bool | None = Field(
        default=None,
        description=(
            "Obrigatório quando `sources` não está vazio (ver AgentResponse). "
            "true = o conteúdo de `message` deriva apenas das fontes recuperadas; "
            "false = o agente acrescentou inferência/síntese própria além das fontes. "
            "Operacionaliza FR-015 (distinção auditável entre recuperação e inferência)."
        ),
    )
    grounding_score: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description=(
            "Score de aterramento 0-5 (Constitution Principio X). Nulo exclusivamente para "
            "respostas que ja chegam ao no de grounding com status procedural, sem conteudo "
            "substantivo. Resposta rebaixada pelo proprio no mantem o score que causou o "
            "rebaixamento."
        ),
    )
    grounding_reasoning: str | None = Field(
        default=None,
        description="Justificativa curta e segura do score (nunca raciocinio bruto do modelo).",
    )
    error_code: str | None = None
    ticket_id: str | None = None

    model_config = {"extra": "allow"}

    def with_extra(self, **kwargs: Any) -> ResponseMetadata:
        data = self.model_dump()
        data.update(kwargs)
        return ResponseMetadata(**data)


class StructuredError(BaseModel):
    """Erro estruturado — nunca uma exceção crua é exposta ao chamador."""

    error_code: str
    detail: str
    retryable: bool = False
