"""Inicializacao do OpenTelemetry SDK -- unica API de metricas e traces (Constitution Principio XI).

- Metricas: `MeterProvider` com `PrometheusMetricReader` (expostas em `GET /metrics`).
- Traces: `TracerProvider` com exporter OTLP gRPC para o coletor, apenas quando
  `OTEL_EXPORTER_OTLP_ENDPOINT` esta definido. Falha do coletor so gera log -- nunca erro para o
  usuario (degradacao graciosa).
- Correlacao: um `SpanProcessor` copia os ids de contexto usados pelo structlog para atributos de
  span, de modo que logs, metricas e traces de uma requisicao sejam correlacionaveis.
"""

from __future__ import annotations

import structlog
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config.settings import get_settings
from app.observability.logging import get_context_ids

logger = structlog.get_logger(__name__)

_DURATION_BUCKETS_SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
_GROUNDING_SCORE_BUCKETS = [0, 1, 2, 3, 4, 5]

_initialized = False
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


class ContextAttributesSpanProcessor(SpanProcessor):
    """Copia request/session/message/execution ids (contextvars) para atributos de span."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for key, value in get_context_ids().items():
            if value is not None:
                span.set_attribute(key, value)


def _views() -> list[View]:
    return [
        View(
            instrument_name="getnet_grounding_score",
            aggregation=ExplicitBucketHistogramAggregation(_GROUNDING_SCORE_BUCKETS),
        ),
        View(
            instrument_name="getnet_*_duration",
            aggregation=ExplicitBucketHistogramAggregation(_DURATION_BUCKETS_SECONDS),
        ),
    ]


def build_tracer_provider(resource: Resource, otlp_endpoint: str | None) -> TracerProvider:
    """Monta o TracerProvider; o exporter OTLP e opcional e nunca bloqueia nem levanta ao
    construir (a conexao com o coletor e preguicosa; falha de export so gera log)."""
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(ContextAttributesSpanProcessor())
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True, timeout=3)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def init_telemetry() -> None:
    """Idempotente: a segunda chamada nao faz nada. No-op quando `otel_enabled` e falso."""
    global _initialized, _tracer_provider, _meter_provider
    if _initialized:
        return
    settings = get_settings()
    if not settings.otel_enabled:
        return

    resource = Resource.create({"service.name": settings.otel_service_name})

    _meter_provider = MeterProvider(
        resource=resource, metric_readers=[PrometheusMetricReader()], views=_views()
    )
    metrics.set_meter_provider(_meter_provider)

    _tracer_provider = build_tracer_provider(resource, settings.otel_exporter_otlp_endpoint)
    trace.set_tracer_provider(_tracer_provider)

    # Os instrumentos sao recriados contra o provider real (ver observability/metrics.py).
    from app.observability import metrics as app_metrics

    app_metrics.reset_instruments()
    _initialized = True
    logger.info("telemetry_initialized")


def get_tracer_provider_instance() -> TracerProvider | None:
    """Usado por testes para anexar um exporter em memoria."""
    return _tracer_provider


def shutdown_telemetry() -> None:
    """Descarrega spans pendentes (nao desliga o provider global, que nao pode ser recriado)."""
    if _tracer_provider is not None:
        try:
            _tracer_provider.force_flush(timeout_millis=2000)
        except Exception:  # noqa: BLE001 -- observabilidade nunca derruba o encerramento
            logger.warning("telemetry_flush_failed")
