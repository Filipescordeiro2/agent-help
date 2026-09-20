"""Gera `docker/grafana/dashboards/getnet-operacional.json` (executar: python docker/build_dashboard.py).

Mantido como script para que o JSON provisionado seja reproduzivel e revisavel. Toda expressao
PromQL usa apenas series de baixa cardinalidade de `contracts/schemas.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "getnet-prometheus"}
API = {"type": "yesoreyeram-infinity-datasource", "uid": "getnet-api"}


def prom_target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref}


def panel(pid: int, title: str, kind: str, x: int, y: int, w: int, h: int, targets: list, **extra):
    base = {
        "id": pid,
        "title": title,
        "type": kind,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": targets[0]["datasource"],
        "targets": targets,
    }
    base.update(extra)
    return base


def quantile(q: float, metric: str, by: str, ref: str) -> dict:
    expr = f"histogram_quantile({q}, sum by (le, {by}) (rate({metric}_bucket[5m])))"
    return prom_target(expr, f"p{int(q * 100)} {{{{{by}}}}}", ref)


panels = [
    panel(
        1,
        "TPM - tokens por minuto",
        "timeseries",
        0,
        0,
        12,
        8,
        [prom_target("sum(rate(getnet_llm_tokens_total[1m])) * 60", "tokens/min")],
    ),
    panel(
        2,
        "RPM - requisicoes por minuto",
        "timeseries",
        12,
        0,
        12,
        8,
        [prom_target("sum(rate(getnet_http_requests_total[1m])) * 60", "req/min")],
    ),
    panel(
        3,
        "Latencia por agente (p50/p95/p99)",
        "timeseries",
        0,
        8,
        12,
        8,
        [
            quantile(0.5, "getnet_agent_call_duration_seconds", "agent", "A"),
            quantile(0.95, "getnet_agent_call_duration_seconds", "agent", "B"),
            quantile(0.99, "getnet_agent_call_duration_seconds", "agent", "C"),
        ],
        fieldConfig={"defaults": {"unit": "s"}, "overrides": []},
    ),
    panel(
        4,
        "Latencia HTTP por endpoint (p95)",
        "timeseries",
        12,
        8,
        12,
        8,
        [quantile(0.95, "getnet_http_request_duration_seconds", "endpoint", "A")],
        fieldConfig={"defaults": {"unit": "s"}, "overrides": []},
    ),
    panel(
        5,
        "Distribuicao do grounding score",
        "barchart",
        0,
        16,
        12,
        8,
        [
            {
                **prom_target(
                    "sum by (le) (increase(getnet_grounding_score_bucket[1h]))", "{{le}}"
                ),
                "format": "table",
                "instant": True,
            }
        ],
    ),
    panel(
        6,
        "Sessoes ativas por usuario",
        "table",
        12,
        16,
        12,
        8,
        [
            {
                "datasource": API,
                "refId": "A",
                "type": "json",
                "source": "url",
                "format": "table",
                "url": "http://app:8000/api/v1/metrics/sessions-per-user",
                "url_options": {"method": "GET", "data": ""},
                "root_selector": "",
                "columns": [
                    {"selector": "user_id", "text": "user_id", "type": "string"},
                    {
                        "selector": "active_session_count",
                        "text": "active_session_count",
                        "type": "number",
                    },
                ],
            }
        ],
    ),
    panel(
        7,
        "Propostas do Feedback Agent por status",
        "timeseries",
        0,
        24,
        12,
        8,
        [prom_target("sum by (status) (getnet_feedback_proposals_total)", "{{status}}")],
    ),
    panel(
        8,
        "Taxa de aprovacao de propostas",
        "stat",
        12,
        24,
        6,
        8,
        [
            prom_target(
                'sum(getnet_feedback_proposals_total{status="APPROVED"}) / '
                'clamp_min(sum(getnet_feedback_proposals_total{status=~"APPROVED|REJECTED"}), 1)',
                "aprovacao",
            )
        ],
        fieldConfig={"defaults": {"unit": "percentunit", "min": 0, "max": 1}, "overrides": []},
    ),
    panel(
        9,
        "Taxa de erro por agente / ferramenta",
        "timeseries",
        18,
        24,
        6,
        8,
        [
            prom_target("sum by (agent) (rate(getnet_agent_errors_total[5m]))", "agente {{agent}}", "A"),
            prom_target("sum by (tool) (rate(getnet_tool_errors_total[5m]))", "ferramenta {{tool}}", "B"),
        ],
    ),
]

dashboard = {
    "uid": "getnet-operacional",
    "title": "Getnet — Visão Operacional",
    "tags": ["getnet", "multiagent"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "30s",
    "time": {"from": "now-1h", "to": "now"},
    "panels": panels,
}

if __name__ == "__main__":
    target = Path(__file__).parent / "grafana" / "dashboards" / "getnet-operacional.json"
    target.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"escrito: {target}")
