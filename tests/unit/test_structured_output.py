"""T049: teste unitario do helper de saida estruturada."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import app.llm.structured_output as structured_output
from app.llm.structured_output import StructuredOutputError, get_structured_output


class _Decision(BaseModel):
    value: str


class _StructuredModel:
    def __init__(self, responses: list) -> None:
        self._responses = responses
        self.calls = 0

    def with_structured_output(self, _schema: type) -> _StructuredModel:
        return self

    async def ainvoke(self, _messages: list) -> object:
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


async def test_returns_schema_instance_on_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _StructuredModel([_Decision(value="ok")])
    monkeypatch.setattr(structured_output, "get_chat_model", lambda: model)

    result = await get_structured_output(_Decision, [{"role": "user", "content": "x"}])

    assert result.value == "ok"
    assert model.calls == 1


async def test_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _StructuredModel([{"not": "a decision"}, _Decision(value="retried")])
    monkeypatch.setattr(structured_output, "get_chat_model", lambda: model)

    result = await get_structured_output(_Decision, [{"role": "user", "content": "x"}])

    assert result.value == "retried"
    assert model.calls == 2


async def test_escalates_to_structured_output_error_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _StructuredModel([{"bad": 1}, {"bad": 2}])
    monkeypatch.setattr(structured_output, "get_chat_model", lambda: model)

    with pytest.raises(StructuredOutputError):
        await get_structured_output(_Decision, [{"role": "user", "content": "x"}])
