"""Playbooks que orientam um agente (guia no prompt) -- hoje usado pelo Knowledge Agent.

Um Playbook com `metadata.owning_agent == "<agente>"` e `status == active` vira um bloco de
orientacao anexado ao prompt do agente (procedimento de atendimento). O Customer Support Agent
continua escolhendo os SEUS Playbooks por sintoma (`PlaybooksRepository.find_matching_symptom`),
que ignora os Playbooks de outros agentes.

Estrutura: um Playbook PRINCIPAL (`always_apply`) descreve o fluxo comum e diz quando usar cada
SUB-PLAYBOOK (procedimento de um assunto: Pix, estorno, erros da maquininha...). Ver
`app/agent/topic_selection.py`.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.playbooks_repository import Playbook, PlaybooksRepository, PlaybookStatus


async def select_guidance_playbooks(
    db: AsyncIOMotorDatabase, *, owning_agent: str
) -> list[Playbook]:
    active = await PlaybooksRepository(db).list(
        filters={"status": PlaybookStatus.ACTIVE.value}, limit=200
    )
    return [p for p in active if p.metadata.get("owning_agent") == owning_agent]


def format_playbook_block(playbook: Playbook) -> str:
    lines = [f"Procedimento de atendimento (Playbook '{playbook.name}'): {playbook.objective}"]
    lines.append("Etapas:")
    for number, step in enumerate(playbook.steps, start=1):
        tool = f" [ferramenta: {step.tool}]" if step.tool else ""
        lines.append(f"{number}. {step.step_id}{tool}: {step.instruction}")
    if playbook.escalation_rules:
        lines.append("Escalonar para atendente humano quando:")
        lines += [f"- {rule}" for rule in playbook.escalation_rules]
    if playbook.exceptions:
        lines.append("Excecoes:")
        lines += [f"- {item}" for item in playbook.exceptions]
    if playbook.success_criteria:
        lines.append("Criterios de sucesso:")
        lines += [f"- {item}" for item in playbook.success_criteria]
    lines.append(
        "Nota: as etapas com ferramenta (consulta a base e a pagina web) ja foram executadas "
        "pelo pipeline antes de voce; voce atua na compreensao da duvida, na resposta "
        "fundamentada, na confirmacao e no escalonamento."
    )
    return "\n".join(lines)


def format_playbooks_as_guidance(playbooks: list[Playbook]) -> str:
    return "\n\n".join(format_playbook_block(p) for p in playbooks)
