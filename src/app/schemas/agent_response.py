"""AgentResponse: envelope unico de saida de todo agente, no do grafo e rota da API.

Ver spec.md FR-001 a FR-005 e data-model.md secao "AgentResponse". Nenhum agente, no do
LangGraph ou rota FastAPI pode retornar `str`/`dict` solto -- sempre uma instancia validada
deste schema.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ResponseMetadata, SourceRef


class Status(StrEnum):
    OK = "OK"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    ERROR = "ERROR"


class AgentResponse(BaseModel):
    """Contrato universal de saida (FR-002 a FR-005)."""

    status: Status
    agent: str
    message: str = Field(min_length=1, description="Unico campo de texto livre no payload")
    sources: list[SourceRef] = Field(default_factory=list)
    metadata: ResponseMetadata

    @model_validator(mode="after")
    def _grounded_in_sources_required_when_sources_present(self) -> AgentResponse:
        if self.sources and self.metadata.grounded_in_sources is None:
            raise ValueError(
                "metadata.grounded_in_sources e obrigatorio quando sources nao esta vazio "
                "(FR-015 -- distincao auditavel entre recuperacao e inferencia)"
            )
        return self
