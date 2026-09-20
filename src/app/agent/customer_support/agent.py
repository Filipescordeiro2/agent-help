"""Customer Support Agent -- trata problemas do cliente (spec FR-017 a FR-020).

Dois caminhos:
- **Suporte guiado** (padrao, ver `case_flow.py`): entende o problema (pergunta se estiver vago),
  passa a solucao das paginas de ajuda ("tente fazer isso... deu certo?") e, se nao resolver, abre
  um chamado real com a conversa e a analise previa. Vale quando ja ha um caso ativo na sessao ou
  quando nenhum Playbook de suporte casa com a mensagem.
- **Playbook de suporte** (dispositivo/diagnostico): so executa um PlaybookStep cujo `tool`
  esteja em `authorized_tools` (FR-019); nunca fabrica dado de cliente/dispositivo (FR-018);
  escala com ticket_id (mock) quando o Playbook se esgota (FR-020 -- diagnostico mock).
"""

from __future__ import annotations

import json
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.contracts import Agent
from app.agent.customer_support.case_flow import SupportCaseFlow
from app.agent.knowledge.agent import KnowledgeAgent
from app.agent.skill_selection import format_skills_as_guidance, select_applicable_skills
from app.agent.state import GraphState
from app.llm.structured_output import get_structured_output
from app.repository.playbooks_repository import Playbook, PlaybooksRepository
from app.repository.support_cases_repository import SupportCasesRepository
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata
from app.security.policies import wrap_untrusted_content
from app.services.memory.short_term import format_turns_as_transcript, get_recent_turns
from app.tools.customer_support_tools import (
    CreateSupportTicketTool,
    CreateTicketInput,
    DeviceIdInput,
    GetCustomerDevicesTool,
    GetDeviceDiagnosticsTool,
    GetDeviceStatusTool,
    UserIdInput,
)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "customer_support" / "v1.md"


class SupportAnswerDraft(BaseModel):
    message: str


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class CustomerSupportAgent(Agent):
    name = "customer_support_agent"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._playbooks_repo = PlaybooksRepository(db)
        self._get_devices_tool = GetCustomerDevicesTool()
        self._get_status_tool = GetDeviceStatusTool()
        self._get_diagnostics_tool = GetDeviceDiagnosticsTool()
        self._create_ticket_tool = CreateSupportTicketTool()
        self._cases = SupportCasesRepository(db)
        self._flow = SupportCaseFlow(db, KnowledgeAgent(db))

    async def handle(self, state: GraphState) -> AgentResponse:
        message = state["user_message"].message
        user_id = state["user_message"].user_id
        execution_id = state["execution_id"]

        # Caso ativo na sessao (perguntamos algo ou aguardamos o "deu certo?"): segue o fluxo.
        active_case = await self._cases.get_active(state["session_id"])
        if active_case is not None:
            return await self._flow.run(state, active_case)

        playbook = await self._playbooks_repo.find_matching_symptom(message)
        if playbook is None:
            return await self._flow.run(state, None)

        devices_result = await self._get_devices_tool.run(UserIdInput(user_id=user_id))
        devices = devices_result.result or []
        offline_device = next((d for d in devices if d.get("status") == "offline"), None)

        if offline_device is None:
            return await self._escalate(
                user_id=user_id,
                execution_id=execution_id,
                subject=f"Playbook '{playbook.name}' sem dispositivo correspondente",
                description=message,
                playbook=playbook,
            )

        device_id = offline_device["device_id"]

        if "get_device_status" not in playbook.authorized_tools:
            return await self._escalate(
                user_id=user_id,
                execution_id=execution_id,
                subject="Passo nao autorizado pelo Playbook",
                description=message,
                playbook=playbook,
            )
        status_result = await self._get_status_tool.run(DeviceIdInput(device_id=device_id))

        if "get_device_diagnostics" not in playbook.authorized_tools:
            return await self._escalate(
                user_id=user_id,
                execution_id=execution_id,
                subject="Passo nao autorizado pelo Playbook",
                description=message,
                playbook=playbook,
            )
        diagnostics_result = await self._get_diagnostics_tool.run(
            DeviceIdInput(device_id=device_id)
        )
        diagnostics = diagnostics_result.result or {}

        if diagnostics.get("diagnostics") == "sinal_wifi_fraco":
            # Contexto (Playbook + resultados de ferramentas) exposto ao no de grounding.
            state["grounding_context"] = [
                *state.get("grounding_context", []),
                f"Playbook '{playbook.name}': {playbook.objective}",
                json.dumps({"status": status_result.result, "diagnostics": diagnostics}),
            ]
            draft = await self._draft_resolution_message(
                session_id=state["session_id"],
                status=status_result.result,
                diagnostics=diagnostics,
                grounding_feedback=state.get("grounding_feedback"),
            )
            return AgentResponse(
                status=Status.OK,
                agent=self.name,
                message=draft.message,
                sources=[],
                metadata=ResponseMetadata(execution_id=execution_id, confidence=0.85),
            )

        return await self._escalate(
            user_id=user_id,
            execution_id=execution_id,
            subject=f"Playbook '{playbook.name}' esgotado sem resolucao",
            description=message,
            playbook=playbook,
        )

    async def _draft_resolution_message(
        self,
        *,
        session_id: str,
        status: dict | None,
        diagnostics: dict,
        grounding_feedback: str | None = None,
    ) -> SupportAnswerDraft:
        context = wrap_untrusted_content(json.dumps({"status": status, "diagnostics": diagnostics}))
        recent_turns = await get_recent_turns(self._db, session_id)
        transcript = format_turns_as_transcript(recent_turns)
        applicable_skills = await select_applicable_skills(
            self._db, owning_agent=self.name, query=json.dumps(diagnostics)
        )
        skills_guidance = format_skills_as_guidance(applicable_skills)

        system_prompt = _load_prompt() + "\n\n" + context
        if grounding_feedback:
            system_prompt += "\n\n" + wrap_untrusted_content(
                "Avaliacao da tentativa anterior (corrija a resposta): " + grounding_feedback
            )
        if skills_guidance:
            system_prompt += "\n\n" + skills_guidance
        if transcript:
            system_prompt += "\n\n" + transcript
        return await get_structured_output(
            SupportAnswerDraft,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Oriente o cliente com base no diagnostico acima."},
            ],
        )

    async def _escalate(
        self,
        *,
        user_id: str,
        execution_id: str,
        subject: str,
        description: str,
        playbook: Playbook | None = None,
    ) -> AgentResponse:
        if playbook is not None and "create_support_ticket" not in playbook.authorized_tools:
            # Playbook nao autoriza abertura de chamado -- ainda assim escalamos, mas sem tool.
            ticket_id = None
        else:
            ticket_result = await self._create_ticket_tool.run(
                CreateTicketInput(user_id=user_id, subject=subject, description=description),
                authorized=True,
            )
            ticket_id = (ticket_result.result or {}).get("ticket_id")

        return AgentResponse(
            status=Status.ESCALATION_REQUIRED,
            agent=self.name,
            message=(
                "Nao consegui resolver isso automaticamente. Vou encaminhar seu caso para um "
                "atendente humano."
            ),
            sources=[],
            metadata=ResponseMetadata(
                execution_id=execution_id, confidence=0.5, ticket_id=ticket_id
            ),
        )
