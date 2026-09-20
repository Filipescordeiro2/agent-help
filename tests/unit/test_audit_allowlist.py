"""T027: `filter_safe_metadata` mantem as chaves de baixa sensibilidade e remove o resto."""

from __future__ import annotations

from app.repository.audit_events_repository import (
    SAFE_METADATA_ALLOWLIST,
    filter_safe_metadata,
)


def test_new_keys_are_kept() -> None:
    metadata = {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
        "model": "m",
        "method": "POST",
        "proposal_id": "p",
        "action_type": "CREATE_SKILL",
        "trigger_type": "MANUAL",
        "run_id": "r",
        "score": 4,
    }
    assert filter_safe_metadata(metadata) == metadata


def test_everything_else_is_removed() -> None:
    filtered = filter_safe_metadata(
        {"model": "m", "comment": "livre", "message": "texto", "api_key": "sk-x", "cpf": "1"}
    )
    assert filtered == {"model": "m"}


def test_allowlist_never_contains_content_or_secret_keys() -> None:
    for forbidden in ("comment", "message", "content", "api_key", "password", "token", "cpf"):
        assert forbidden not in SAFE_METADATA_ALLOWLIST
