"""RouterDecision: saida estruturada exclusiva do Router Agent (FR-006 a FR-010)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Intent(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    MULTI_AGENT = "MULTI_AGENT"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNKNOWN = "UNKNOWN"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    # Conversa social (oi, obrigado, tchau, "o que voce faz?"): resposta de boas-vindas.
    GREETING = "GREETING"


class RouterDecision(BaseModel):
    """Decisao estruturada do Router Agent -- nunca texto livre (Constitution Principio III)."""

    intent: Intent
    target_agent: str | None = None
    target_sequence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool
    reason_code: str = Field(
        description="Codigo curto e seguro para auditoria -- nunca o raciocinio privado do modelo"
    )
