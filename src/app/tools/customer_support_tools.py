"""Ferramentas do Customer Support Agent (spec plano): get_customer_profile,
get_customer_devices, get_device_status, get_device_diagnostics, get_customer_tickets,
get_transaction_status, create_support_ticket, get_known_incidents,
execute_authorized_playbook_step.

`create_support_ticket` e `execute_authorized_playbook_step` exigem autorizacao explicita
adicional (alteram dados) -- todas as demais sao somente leitura.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.tools.base import BaseTool
from app.tools.mocks import customer_data


class UserIdInput(BaseModel):
    user_id: str


class DeviceIdInput(BaseModel):
    device_id: str


class NoInput(BaseModel):
    pass


class CreateTicketInput(BaseModel):
    user_id: str
    subject: str
    description: str


class ExecutePlaybookStepInput(BaseModel):
    playbook_id: str
    step_id: str
    tool_name: str
    authorized_tools: list[str]


class ToolResultOutput(BaseModel):
    result: dict | list | None


class GetCustomerProfileTool(BaseTool[UserIdInput, ToolResultOutput]):
    name = "get_customer_profile"

    async def _run(self, input_data: UserIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_customer_profile(input_data.user_id))


class GetCustomerDevicesTool(BaseTool[UserIdInput, ToolResultOutput]):
    name = "get_customer_devices"

    async def _run(self, input_data: UserIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_customer_devices(input_data.user_id))


class GetDeviceStatusTool(BaseTool[DeviceIdInput, ToolResultOutput]):
    name = "get_device_status"

    async def _run(self, input_data: DeviceIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_device_status(input_data.device_id))


class GetDeviceDiagnosticsTool(BaseTool[DeviceIdInput, ToolResultOutput]):
    name = "get_device_diagnostics"

    async def _run(self, input_data: DeviceIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_device_diagnostics(input_data.device_id))


class GetCustomerTicketsTool(BaseTool[UserIdInput, ToolResultOutput]):
    name = "get_customer_tickets"

    async def _run(self, input_data: UserIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_customer_tickets(input_data.user_id))


class GetTransactionStatusTool(BaseTool[UserIdInput, ToolResultOutput]):
    name = "get_transaction_status"

    async def _run(self, input_data: UserIdInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_transaction_status(input_data.user_id))


class GetKnownIncidentsTool(BaseTool[NoInput, ToolResultOutput]):
    name = "get_known_incidents"

    async def _run(self, _input_data: NoInput) -> ToolResultOutput:
        return ToolResultOutput(result=customer_data.get_known_incidents())


class CreateSupportTicketTool(BaseTool[CreateTicketInput, ToolResultOutput]):
    """Altera dados (cria chamado) -- exige autorizacao explicita adicional."""

    name = "create_support_ticket"
    requires_explicit_authorization = True

    async def _run(self, input_data: CreateTicketInput) -> ToolResultOutput:
        ticket = customer_data.create_ticket(
            input_data.user_id, subject=input_data.subject, description=input_data.description
        )
        return ToolResultOutput(result=ticket)


class ExecuteAuthorizedPlaybookStepTool(BaseTool[ExecutePlaybookStepInput, ToolResultOutput]):
    """Executa um passo de Playbook -- exige autorizacao explicita adicional e valida que o
    `tool_name` do passo esta na lista `authorized_tools` do Playbook (spec FR-019)."""

    name = "execute_authorized_playbook_step"
    requires_explicit_authorization = True

    async def _run(self, input_data: ExecutePlaybookStepInput) -> ToolResultOutput:
        if input_data.tool_name not in input_data.authorized_tools:
            return ToolResultOutput(
                result={
                    "executed": False,
                    "reason": "tool_not_authorized_for_this_playbook_step",
                }
            )
        return ToolResultOutput(
            result={
                "executed": True,
                "playbook_id": input_data.playbook_id,
                "step_id": input_data.step_id,
            }
        )
