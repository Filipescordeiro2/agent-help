"""T087: consistencia estatica do stack de observabilidade (compose, configs e dashboard).

Nao exige daemon Docker: valida arquivos versionados entre si (a carga real do Grafana e
verificada manualmente pelo quickstart / T097)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DOCKER = _ROOT / "docker"
_CONTRACT_SCHEMAS = (
    _ROOT / "specs" / "002-feedback-grounding-observability" / "contracts" / "schemas.md"
)

_REQUIRED_SERVICES = {
    "app",
    "mongodb",
    "otel-collector",
    "prometheus",
    "grafana",
    "loki",
    "promtail",
    "tempo",
}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def datasources() -> list[dict]:
    path = _DOCKER / "grafana" / "provisioning" / "datasources" / "datasources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["datasources"]


@pytest.fixture(scope="module")
def dashboard() -> dict:
    path = _DOCKER / "grafana" / "dashboards" / "getnet-operacional.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _depends_on(service: dict) -> set[str]:
    depends = service.get("depends_on", {})
    return set(depends) if isinstance(depends, dict) else set(depends)


def test_compose_defines_every_required_service(compose: dict) -> None:
    assert _REQUIRED_SERVICES <= set(compose["services"])


def test_app_does_not_depend_on_any_observability_service(compose: dict) -> None:
    assert _depends_on(compose["services"]["app"]) == {"mongodb"}
    assert compose["services"]["app"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"].startswith(
        "http://otel-collector"
    )


@pytest.mark.parametrize("name", ["prometheus", "grafana"])
def test_prometheus_and_grafana_receive_only_the_minimum_environment(
    compose: dict, name: str
) -> None:
    service = compose["services"][name]
    assert "env_file" not in service
    keys = set(service["environment"])
    assert "INTERNAL_SERVICE_TOKEN" in keys
    for key in keys - {"INTERNAL_SERVICE_TOKEN"}:
        assert key.startswith("GF_"), key  # so variaveis do proprio Grafana
    assert not keys & {"OPENROUTER_API_KEY", "MONGODB_URI", "EMBEDDING_MODEL"}


def test_config_files_are_valid_yaml_and_json() -> None:
    for path in list(_DOCKER.rglob("*.yaml")) + list(_DOCKER.rglob("*.yml")):
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path
    for path in _DOCKER.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_prometheus_scrapes_the_app_with_the_internal_token_header() -> None:
    config = yaml.safe_load((_DOCKER / "prometheus.yml").read_text(encoding="utf-8"))
    job = next(j for j in config["scrape_configs"] if j["job_name"] == "getnet-app")
    assert job["metrics_path"] == "/metrics"
    assert job["static_configs"][0]["targets"] == ["app:8000"]
    assert "X-Internal-Service-Token" in job["http_headers"]


def test_collector_forwards_traces_to_tempo_and_promtail_pushes_to_loki() -> None:
    collector = yaml.safe_load((_DOCKER / "otel-collector-config.yaml").read_text("utf-8"))
    pipeline = collector["service"]["pipelines"]["traces"]
    assert "otlp" in pipeline["receivers"] and "otlp/tempo" in pipeline["exporters"]
    assert collector["exporters"]["otlp/tempo"]["endpoint"] == "tempo:4317"

    tempo = yaml.safe_load((_DOCKER / "tempo.yaml").read_text(encoding="utf-8"))
    assert "otlp" in tempo["distributor"]["receivers"]

    promtail = yaml.safe_load((_DOCKER / "promtail-config.yaml").read_text(encoding="utf-8"))
    assert promtail["clients"][0]["url"] == "http://loki:3100/loki/api/v1/push"


def test_datasource_uids_are_unique_and_cover_prometheus_loki_tempo_and_api(
    datasources: list[dict],
) -> None:
    uids = [d["uid"] for d in datasources]
    assert len(uids) == len(set(uids))
    assert {d["type"] for d in datasources} >= {
        "prometheus",
        "loki",
        "tempo",
        "yesoreyeram-infinity-datasource",
    }
    api = next(d for d in datasources if d["uid"] == "getnet-api")
    assert api["secureJsonData"]["httpHeaderValue1"] == "${INTERNAL_SERVICE_TOKEN}"
    loki = next(d for d in datasources if d["type"] == "loki")
    assert loki["jsonData"]["derivedFields"][0]["datasourceUid"] == "getnet-tempo"


def test_dashboard_datasources_exist_in_the_provisioned_list(
    dashboard: dict, datasources: list[dict]
) -> None:
    known = {d["uid"] for d in datasources}
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] in known, panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] in known


def _contract_metric_names() -> set[str]:
    text = _CONTRACT_SCHEMAS.read_text(encoding="utf-8")
    return set(re.findall(r"`(getnet_[a-z_]+)`", text))


def _normalize(name: str) -> str:
    return re.sub(r"_(bucket|count|sum)$", "", name)


def test_every_promql_series_is_declared_in_the_contract(dashboard: dict) -> None:
    contract = _contract_metric_names()
    assert contract, "esperava as series de contracts/schemas.md"
    used: set[str] = set()
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            for name in re.findall(r"\b(getnet_[a-z_]+)", target.get("expr", "")):
                used.add(_normalize(name))
    assert used and used <= contract, used - contract


def test_dashboard_covers_the_required_views(dashboard: dict) -> None:
    titles = " | ".join(p["title"].lower() for p in dashboard["panels"])
    for expected in (
        "tpm",
        "rpm",
        "latencia por agente",
        "grounding",
        "sessoes ativas por usuario",
        "propostas",
        "aprovacao",
        "erro",
    ):
        assert expected in titles, expected
    assert dashboard["uid"] == "getnet-operacional"
    exprs = " ".join(t.get("expr", "") for p in dashboard["panels"] for t in p["targets"])
    assert "getnet_agent_call_duration_seconds_bucket" in exprs  # latencia por AGENTE (I1)
    assert "getnet_grounding_score_bucket" in exprs
    sessions_panel = next(p for p in dashboard["panels"] if "sessoes" in p["title"].lower())
    assert sessions_panel["datasource"]["uid"] == "getnet-api"
    assert "/api/v1/metrics/sessions-per-user" in sessions_panel["targets"][0]["url"]


def test_env_example_documents_the_new_settings() -> None:
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "GROUNDING_MIN_SCORE",
        "OPENROUTER_MODEL_GROUNDING",
        "FEEDBACK_AGENT_INTERVAL_MINUTES",
        "GRAFANA_ADMIN_PASSWORD",
        "OTEL_ENABLED",
    ):
        assert key in text, key
    assert "change-me" in text  # placeholders claramente ficticios
