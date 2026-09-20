"""Seed do Playbook inicial de "problema de conexao de dispositivo" (US2)."""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.playbooks_repository import (
    Playbook,
    PlaybooksRepository,
    PlaybookStep,
)

DEVICE_CONNECTION_PLAYBOOK_ID = "pb_device_connection_issue"


def build_device_connection_playbook() -> Playbook:
    return Playbook(
        playbook_id=DEVICE_CONNECTION_PLAYBOOK_ID,
        name="Problema de conexao de dispositivo",
        objective="Restabelecer a conexao de uma maquininha que aparece offline.",
        symptoms=["nao conecta", "sem conexao", "offline", "nao esta conectando", "sem sinal"],
        prerequisites=["device_id do cliente conhecido"],
        steps=[
            PlaybookStep(
                step_id="check_status",
                instruction="Consultar o status atual do dispositivo.",
                tool="get_device_status",
                on_success="run_diagnostics",
                on_failure="escalate",
            ),
            PlaybookStep(
                step_id="run_diagnostics",
                instruction="Rodar diagnostico do dispositivo para identificar a causa.",
                tool="get_device_diagnostics",
                on_success="advise_customer",
                on_failure="escalate",
            ),
            PlaybookStep(
                step_id="advise_customer",
                instruction=(
                    "Orientar o cliente com base no diagnostico (ex.: reiniciar o dispositivo, "
                    "aproximar do roteador)."
                ),
                tool=None,
                on_success="closed",
                on_failure="escalate",
            ),
        ],
        authorized_tools=[
            "get_device_status",
            "get_device_diagnostics",
            "get_known_incidents",
            "create_support_ticket",
        ],
        escalation_rules=["Se o diagnostico nao resolver apos orientacao, encaminhar para humano."],
        success_criteria=["Dispositivo volta a reportar status online."],
        closure_criteria=["Cliente confirma que o problema foi resolvido, ou caso escalado."],
    )


async def seed_playbooks(db: AsyncIOMotorDatabase) -> None:
    repo = PlaybooksRepository(db)
    if await repo.get(DEVICE_CONNECTION_PLAYBOOK_ID) is None:
        await repo.insert(build_device_connection_playbook())
