"""PlaybooksRepository -- colecao `playbooks` (data-model.md).

Leitura suficiente para a US2 (Customer Support Agent consome Playbooks existentes); CRUD
completo (criacao/atualizacao/exclusao/embedding) chega na US5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.repository.base import BaseRepository


class PlaybookStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class PlaybookStep(BaseModel):
    step_id: str
    instruction: str
    tool: str | None = None
    on_success: str | None = None
    on_failure: str | None = None


class Playbook(BaseModel):
    playbook_id: str
    name: str
    objective: str
    symptoms: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[PlaybookStep] = Field(default_factory=list)
    decision_points: list[dict] = Field(default_factory=list)
    authorized_tools: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    closure_criteria: list[str] = Field(default_factory=list)
    version: str = "1"
    status: PlaybookStatus = PlaybookStatus.ACTIVE
    metadata: dict = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlaybooksRepository(BaseRepository[Playbook]):
    collection_name = "playbooks"
    model = Playbook
    id_field = "playbook_id"

    async def find_matching_symptom(self, message: str) -> Playbook | None:
        """Busca simples por frase de sintoma -- usada pelo Customer Support Agent para
        selecionar um Playbook aplicavel (busca semantica completa chega na US5).

        Compara apenas contra `symptoms` (frases especificas, ex.: "nao conecta"), nunca
        contra `name`/`objective` -- um nome de Playbook costuma conter palavras genericas
        (ex.: "problema") que apareceriam em quase qualquer mensagem, gerando falsos positivos.
        """
        lowered = message.lower()
        candidates = await self.list(filters={"status": PlaybookStatus.ACTIVE.value}, limit=200)
        for playbook in candidates:
            # Playbooks de outro agente (metadata.owning_agent) nao sao do Customer Support.
            owner = playbook.metadata.get("owning_agent")
            if owner not in (None, "customer_support_agent"):
                continue
            if any(symptom.lower() in lowered for symptom in playbook.symptoms):
                return playbook
        return None
