"""Saidas estruturadas do LLM do Feedback Validation Agent (classificacao e drafts).

Os drafts espelham exatamente os campos aceitos pelos `service.py` de Skills, Playbooks e
Conhecimento (`create_*`), para que `draft_content` esteja pronto para aplicacao apos a
aprovacao humana (spec FR-004). Os `*UpdateDraft` acrescentam o id do alvo.

Os schemas sao enviados ao modelo em modo estrito (json_schema): nao podem ter objetos livres
(`dict`), que o provedor recusa; por isso `metadata` fica de fora e os pontos de decisao sao
tipados.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.repository.feedback_proposals_repository import ActionType


class FeedbackClassification(BaseModel):
    is_actionable: bool
    discard_reason: Literal["NOISE", "DUPLICATE", "OUT_OF_SCOPE"] | None = None
    action_type: ActionType = ActionType.NO_ACTION
    target_topic: str | None = None
    justificativa: str
    confidence: float = Field(ge=0.0, le=1.0)


class PlaybookStepDraft(BaseModel):
    step_id: str
    instruction: str
    tool: str | None = None
    on_success: str | None = None
    on_failure: str | None = None


class DecisionPointDraft(BaseModel):
    """Ponto de decisao do playbook (mesmo formato dos playbooks versionados em YAML)."""

    question: str
    if_yes: str
    if_no: str


class SkillDraft(BaseModel):
    name: str
    description: str
    owning_agent: str
    keywords: list[str] = Field(default_factory=list)
    instructions: str
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class PlaybookDraft(BaseModel):
    name: str
    objective: str
    symptoms: list[str]
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[PlaybookStepDraft]
    decision_points: list[DecisionPointDraft] = Field(default_factory=list)
    authorized_tools: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)
    success_criteria: list[str]
    closure_criteria: list[str] = Field(default_factory=list)
    status: Literal["draft", "active", "deprecated"] = "active"


class KnowledgeDraft(BaseModel):
    title: str
    source: str
    content: str
    product: str | None = None
    region: str | None = None
    doc_type: str | None = None


class SkillUpdateDraft(SkillDraft):
    target_id: str


class PlaybookUpdateDraft(PlaybookDraft):
    target_id: str


class KnowledgeUpdateDraft(KnowledgeDraft):
    target_id: str


class PromptAdjustmentDraft(BaseModel):
    target_prompt: str
    proposed_change: str


# Schema do draft por tipo de acao (usado pelo agente para gerar e por `apply.py` para revalidar).
DRAFT_SCHEMAS: dict[ActionType, type[BaseModel]] = {
    ActionType.CREATE_SKILL: SkillDraft,
    ActionType.UPDATE_SKILL: SkillUpdateDraft,
    ActionType.CREATE_PLAYBOOK: PlaybookDraft,
    ActionType.UPDATE_PLAYBOOK: PlaybookUpdateDraft,
    ActionType.CREATE_KNOWLEDGE: KnowledgeDraft,
    ActionType.UPDATE_KNOWLEDGE: KnowledgeUpdateDraft,
    ActionType.PROMPT_ADJUSTMENT: PromptAdjustmentDraft,
}
