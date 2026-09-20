"""T084: teste unitario das ferramentas do Customer Support Agent -- autorizacao explicita
adicional exigida por create_support_ticket e execute_authorized_playbook_step."""

from __future__ import annotations

import pytest

from app.tools.base import ToolAuthorizationError
from app.tools.customer_support_tools import (
    CreateSupportTicketTool,
    CreateTicketInput,
    ExecuteAuthorizedPlaybookStepTool,
    ExecutePlaybookStepInput,
    GetCustomerDevicesTool,
    GetCustomerProfileTool,
    GetDeviceStatusTool,
    GetKnownIncidentsTool,
    NoInput,
    UserIdInput,
)


def test_read_only_tools_do_not_require_explicit_authorization() -> None:
    for tool_cls in (
        GetCustomerProfileTool,
        GetCustomerDevicesTool,
        GetDeviceStatusTool,
        GetKnownIncidentsTool,
    ):
        assert tool_cls.requires_explicit_authorization is False


def test_write_tools_require_explicit_authorization() -> None:
    assert CreateSupportTicketTool.requires_explicit_authorization is True
    assert ExecuteAuthorizedPlaybookStepTool.requires_explicit_authorization is True


async def test_create_support_ticket_raises_without_authorization() -> None:
    tool = CreateSupportTicketTool()
    with pytest.raises(ToolAuthorizationError):
        await tool.run(
            CreateTicketInput(user_id="client_xpto", subject="x", description="y"),
            authorized=False,
        )


async def test_create_support_ticket_succeeds_when_authorized() -> None:
    tool = CreateSupportTicketTool()
    result = await tool.run(
        CreateTicketInput(user_id="client_xpto", subject="x", description="y"), authorized=True
    )
    assert result.result["is_mock"] is True
    assert result.result["ticket_id"].startswith("tkt_mock_")


async def test_get_customer_profile_returns_mock_data_clearly_labeled() -> None:
    tool = GetCustomerProfileTool()
    result = await tool.run(UserIdInput(user_id="client_xpto"))
    assert result.result is not None
    assert result.result["is_mock"] is True


async def test_get_customer_profile_returns_none_for_unknown_user() -> None:
    tool = GetCustomerProfileTool()
    result = await tool.run(UserIdInput(user_id="does-not-exist"))
    assert result.result is None


async def test_execute_authorized_playbook_step_rejects_unauthorized_tool() -> None:
    tool = ExecuteAuthorizedPlaybookStepTool()
    result = await tool.run(
        ExecutePlaybookStepInput(
            playbook_id="pb1",
            step_id="s1",
            tool_name="delete_everything",
            authorized_tools=["get_device_status"],
        ),
        authorized=True,
    )
    assert result.result["executed"] is False


async def test_execute_authorized_playbook_step_allows_authorized_tool() -> None:
    tool = ExecuteAuthorizedPlaybookStepTool()
    result = await tool.run(
        ExecutePlaybookStepInput(
            playbook_id="pb1",
            step_id="s1",
            tool_name="get_device_status",
            authorized_tools=["get_device_status"],
        ),
        authorized=True,
    )
    assert result.result["executed"] is True


async def test_get_known_incidents_accepts_no_input() -> None:
    tool = GetKnownIncidentsTool()
    result = await tool.run(NoInput())
    assert isinstance(result.result, list)
    assert all(i["is_mock"] for i in result.result)
