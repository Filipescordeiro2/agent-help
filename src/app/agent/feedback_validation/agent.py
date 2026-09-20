"""Feedback Validation Agent -- 4o agente, processo de retaguarda (spec FR-001 a FR-005).

NAO herda `Agent`, NAO recebe `GraphState` e NAO e registrado em `AGENT_HANDLERS`: o Router
nunca o aciona a partir de mensagens de usuario final. Ele so classifica feedbacks e gera
`draft_content` completo; nunca aplica nada (Constitution Principio XII) -- este modulo nem
importa os servicos de escrita de Skills/Playbooks/Conhecimento.

A saida interna de cada chamada e um `AgentResponse` (`message` = justificativa curta; o payload
estruturado vai em `metadata`, extras `classification` / `draft_content`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from app.llm.structured_output import get_structured_output
from app.observability.instrumentation import current_agent_var
from app.repository.feedback_proposals_repository import ActionType
from app.repository.feedback_repository import Feedback
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.security.policies import wrap_untrusted_content

from .schemas import DRAFT_SCHEMAS, FeedbackClassification

logger = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "feedback_validation"
_MAX_COMMENT_CHARS = 1000
_MAX_RESPONSE_CHARS = 500


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _feedback_block(feedback: Feedback) -> str:
    response_text = ""
    if feedback.response_given:
        response_text = str(feedback.response_given.get("message", ""))[:_MAX_RESPONSE_CHARS]
    lines = [
        f"feedback_id: {feedback.feedback_id}",
        f"classificacao_registrada: {feedback.problem_classification.value}",
        f"agente: {feedback.agent_invoked}",
        f"intencao: {feedback.intent_identified}",
        f"nota: {feedback.rating}",
        f"comentario: {(feedback.comment or '')[:_MAX_COMMENT_CHARS]}",
        f"resposta_dada: {response_text}",
    ]
    return "\n".join(lines)


def _catalog_block(catalog: dict[str, list[dict[str, str]]] | None) -> str:
    if not catalog:
        return "(catalogo vazio)"
    return json.dumps(catalog, ensure_ascii=False)


def _error(code: str, execution_id: str) -> AgentResponse:
    return AgentResponse(
        status=Status.ERROR,
        agent=FeedbackValidationAgent.name,
        message="Nao foi possivel analisar o feedback.",
        metadata=ResponseMetadata(execution_id=execution_id, confidence=0.0, error_code=code),
    )


class FeedbackValidationAgent:
    name = "feedback_validation_agent"

    async def classify(
        self,
        feedback: Feedback,
        catalog: dict[str, list[dict[str, str]]] | None = None,
        *,
        execution_id: str = "feedback-agent",
    ) -> AgentResponse:
        token = current_agent_var.set(self.name)
        try:
            user_content = (
                "Feedback a classificar:\n"
                + wrap_untrusted_content(_feedback_block(feedback))
                + "\n\nCatalogo existente (ids e nomes):\n"
                + wrap_untrusted_content(_catalog_block(catalog))
            )
            classification = await get_structured_output(
                FeedbackClassification,
                [
                    {"role": "system", "content": _load_prompt("v1.md")},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception as exc:  # noqa: BLE001 -- vira AgentResponse(ERROR), nunca excecao crua
            logger.warning("feedback_classification_failed", reason=type(exc).__name__)
            return _error("CLASSIFICATION_FAILED", execution_id)
        finally:
            current_agent_var.reset(token)

        metadata = ResponseMetadata(
            execution_id=execution_id, confidence=classification.confidence
        ).with_extra(classification=classification.model_dump(mode="json"))
        return AgentResponse(
            status=Status.OK,
            agent=self.name,
            message=classification.justificativa or "sem justificativa",
            metadata=metadata,
        )

    async def draft(
        self,
        action_type: ActionType,
        feedbacks: list[Feedback],
        catalog: dict[str, list[dict[str, str]]] | None = None,
        *,
        confidence: float = 0.5,
        execution_id: str = "feedback-agent",
    ) -> AgentResponse:
        schema = DRAFT_SCHEMAS.get(action_type)
        if schema is None:
            return _error("NO_DRAFT_FOR_ACTION", execution_id)
        token = current_agent_var.set(self.name)
        try:
            blocks = "\n\n---\n\n".join(_feedback_block(f) for f in feedbacks)
            user_content = (
                f"Tipo de acao: {action_type.value}\n\nFeedbacks de origem:\n"
                + wrap_untrusted_content(blocks)
                + "\n\nCatalogo existente (ids e nomes):\n"
                + wrap_untrusted_content(_catalog_block(catalog))
            )
            draft: Any = await get_structured_output(
                schema,
                [
                    {"role": "system", "content": _load_prompt("draft_v1.md")},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("feedback_draft_failed", reason=type(exc).__name__)
            return _error("DRAFT_FAILED", execution_id)
        finally:
            current_agent_var.reset(token)

        metadata = ResponseMetadata(execution_id=execution_id, confidence=confidence).with_extra(
            draft_content=draft.model_dump(mode="json")
        )
        return AgentResponse(
            status=Status.OK,
            agent=self.name,
            message=f"Draft {action_type.value} gerado para revisao humana.",
            metadata=metadata,
        )


def extract_classification(response: AgentResponse) -> FeedbackClassification | None:
    raw = (response.metadata.model_extra or {}).get("classification")
    if response.status != Status.OK or raw is None:
        return None
    return FeedbackClassification.model_validate(raw)


def extract_draft_content(response: AgentResponse) -> dict[str, Any] | None:
    if response.status != Status.OK:
        return None
    return (response.metadata.model_extra or {}).get("draft_content")
