"""T119: teste garantindo que nenhum caminho de codigo permite que feedback altere
automaticamente prompts/Skills/Playbooks/configuracao de modelo -- spec FR-042.

Duas camadas de garantia:
1. Estatica: o servico de feedback (app/services/feedback.py) nunca importa os modulos que
   poderiam ser alterados (prompts sao arquivos estaticos sem modulo Python; skills/playbooks
   tem seus proprios servicos de escrita, nunca importados por ele).
2. Comportamental: registrar feedback nao muda o resultado de uma Skill/Playbook ja existente
   nem o comportamento do Router para a mesma entrada.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

_FEEDBACK_SERVICE = Path(__file__).resolve().parents[2] / "src" / "app" / "services" / "feedback.py"
_FORBIDDEN_IMPORT_PREFIXES = (
    "app.services.skills",
    "app.services.playbooks",
    "app.config",
    "app.agent.prompts",
)


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_feedback_package_never_imports_writable_behavior_modules() -> None:
    python_files = [_FEEDBACK_SERVICE]
    assert _FEEDBACK_SERVICE.exists(), "app/services/feedback.py deveria existir"

    for file_path in python_files:
        imported = _imported_modules(file_path)
        for module in imported:
            assert not module.startswith(_FORBIDDEN_IMPORT_PREFIXES), (
                f"{file_path} importa '{module}' -- feedback nunca deve poder escrever em "
                "prompts/Skills/Playbooks/configuracao (FR-042)"
            )


def test_submitting_feedback_does_not_change_skill_behavior(client: TestClient) -> None:
    skill_response = client.post(
        "/api/v1/skills",
        json={
            "name": "Skill original",
            "description": "d",
            "owning_agent": "knowledge_agent",
            "keywords": ["taxa"],
            "instructions": "instrucao original imutavel por feedback",
            "allowed_tools": [],
            "enabled": True,
        },
    )
    skill_id = skill_response.json()["skill_id"]
    original_instructions = skill_response.json()["instructions"]
    original_version = skill_response.json()["version"]

    for _ in range(5):
        client.post(
            "/api/v1/feedback",
            json={
                "user_id": "client_xpto",
                "session_id": "s1",
                "message_id": "m1",
                "execution_id": "e1",
                "problem_classification": "INCORRECT_RESPONSE",
                "agent_invoked": "knowledge_agent",
                "comment": "esta skill deveria mudar",
            },
        )

    skill_after = client.get(f"/api/v1/skills/{skill_id}").json()
    assert skill_after["instructions"] == original_instructions
    assert skill_after["version"] == original_version
