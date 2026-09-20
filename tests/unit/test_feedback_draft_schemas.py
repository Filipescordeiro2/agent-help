"""Os drafts vao ao modelo em modo estrito: sem objetos livres (o provedor recusa `dict`)."""

import pytest

from app.agent.feedback_validation.schemas import DRAFT_SCHEMAS


def _free_form_objects(node: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and not node.get("properties"):
            found.append(path or "<raiz>")
        if node.get("additionalProperties") not in (None, False):
            found.append(path or "<raiz>")
        for key, value in node.items():
            found += _free_form_objects(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _free_form_objects(value, f"{path}[{index}]")
    return found


@pytest.mark.parametrize("action_type", list(DRAFT_SCHEMAS))
def test_draft_schema_has_no_free_form_objects(action_type) -> None:
    schema = DRAFT_SCHEMAS[action_type].model_json_schema()
    assert _free_form_objects(schema) == []
