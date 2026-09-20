"""Instrumentos OTel da plataforma (Constitution Principio XI).

Os nomes sao escolhidos para que a exposicao Prometheus resulte exatamente nos nomes de
`specs/002-.../contracts/schemas.md` (contadores ganham `_total`; histogramas em `s` ganham
`_seconds`). NUNCA usar `user_id`/`session_id`/`message_id` como atributo de metrica --
granularidade por usuario vem dos endpoints JSON (agregacao sobre `audit_events`).
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram

_METER_NAME = "getnet.multiagent"


@dataclass
class Instruments:
    http_requests: Counter
    http_request_duration: Histogram
    agent_call_duration: Histogram
    llm_tokens: Counter
    agent_errors: Counter
    tool_errors: Counter
    grounding_score: Histogram
    feedback_proposals: Counter


def _build() -> Instruments:
    meter = metrics.get_meter(_METER_NAME)
    return Instruments(
        http_requests=meter.create_counter(
            "getnet_http_requests", unit="1", description="Requisicoes HTTP"
        ),
        http_request_duration=meter.create_histogram(
            "getnet_http_request_duration", unit="s", description="Duracao de requisicoes HTTP"
        ),
        agent_call_duration=meter.create_histogram(
            "getnet_agent_call_duration", unit="s", description="Duracao de chamadas de agente"
        ),
        llm_tokens=meter.create_counter(
            "getnet_llm_tokens", unit="1", description="Tokens consumidos em chamadas de LLM"
        ),
        agent_errors=meter.create_counter(
            "getnet_agent_errors", unit="1", description="Erros por agente"
        ),
        tool_errors=meter.create_counter(
            "getnet_tool_errors", unit="1", description="Erros por ferramenta"
        ),
        grounding_score=meter.create_histogram(
            "getnet_grounding_score", unit="1", description="Distribuicao do score de grounding"
        ),
        feedback_proposals=meter.create_counter(
            "getnet_feedback_proposals", unit="1", description="Propostas do Feedback Agent"
        ),
    )


_instruments: Instruments | None = None


def get_instruments() -> Instruments:
    global _instruments
    if _instruments is None:
        _instruments = _build()
    return _instruments


def reset_instruments() -> None:
    """Recria os instrumentos (chamado apos `init_telemetry` definir o provider real)."""
    global _instruments
    _instruments = None
