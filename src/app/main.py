"""Bootstrap da aplicacao FastAPI -- lifespan (Mongo/indices/OpenRouter), middlewares e rotas."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import openai
import structlog
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.agent.definitions.sync import embed_pending_if_possible, sync_definitions
from app.agent.feedback_agent.scheduler import start_scheduler, stop_scheduler
from app.config.settings import get_settings
from app.llm.credentials import (
    LLM_API_KEY_HEADER,
    LLMApiKeyMissingError,
    reset_request_api_key,
    set_request_api_key,
)
from app.observability.audit import safe_emit_audit_event
from app.observability.logging import bind_context, configure_logging
from app.observability.metrics import get_instruments
from app.observability.otel import init_telemetry, shutdown_telemetry
from app.repository.mongodb import client as mongodb_client
from app.repository.mongodb.indexes import create_indexes
from app.routers import (
    routes_agents,
    routes_audit,
    routes_executions,
    routes_feedback,
    routes_feedback_agent,
    routes_health,
    routes_keywords,
    routes_knowledge,
    routes_memory,
    routes_metrics,
    routes_playbooks,
    routes_search,
    routes_sessions,
    routes_skills,
    routes_tickets,
    routes_web_sources,
)
from app.routers.identity import USER_ID_HEADER, IdentityError
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata

configure_logging()
logger = structlog.get_logger(__name__)

# Rotas que NAO exigem o token de fronteira de confianca interna (FR-046).
_PUBLIC_PATHS = frozenset({"/health", "/ready"})
# Rotas fora de /api/v1 que TAMBEM exigem o token (superficie de observabilidade, spec FR-038).
_PROTECTED_NON_API_PATHS = frozenset({"/metrics"})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_telemetry()
    db = mongodb_client.connect()
    await create_indexes(db)
    try:
        await sync_definitions(db)
    except Exception as exc:  # noqa: BLE001 -- definicao invalida nao derruba o servico
        logger.error("agent_definitions_sync_failed", error=str(exc)[:500])
    start_scheduler(db)
    logger.info("startup_complete")
    yield
    stop_scheduler()
    shutdown_telemetry()
    await mongodb_client.close()


_API_DESCRIPTION = """
Plataforma multiagente de atendimento inteligente da Getnet (LangGraph + LangChain + MongoDB Atlas
Vector Search + OpenRouter).

**Como usar este Swagger**: clique em **Authorize** e informe:
1. `X-Internal-Service-Token` -- valor de `INTERNAL_SERVICE_TOKEN` (obrigatorio em `/api/v1/*` e
   `/metrics`);
2. `X-API-Key-LLM` -- a chave do OpenRouter. Ela nao fica no `.env`: e enviada por requisicao e so
   e necessaria nas rotas que usam LLM/embeddings (mensagens, ingestao e busca, aprovacao de
   propostas etc.).

As operacoes de revisao do Feedback Agent (`approve`, `reject`, `mark-applied`) tambem exigem o
cabecalho `X-Reviewer-Id`.

A identidade do usuario vai nos cabecalhos `X-User-Id` e `X-Session-Id` (e, quando aplicavel,
`X-Channel`, `X-Message-Id`, `X-Execution-Id`); o corpo continua aceito, e valores diferentes entre
cabecalho e corpo resultam em `400 IDENTITY_MISMATCH`. `/health`, `/ready` e esta documentacao
sao publicos.

Toda resposta e um `AgentResponse` estruturado (`status`, `agent`, `message`, `sources`,
`metadata`); erros tambem, com `status: ERROR` e `metadata.error_code`.
"""

_OPENAPI_TAGS = [
    {"name": "health", "description": "Saude e prontidao (publicos)."},
    {"name": "sessions", "description": "Sessoes e mensagens do atendimento."},
    {"name": "executions", "description": "Execucoes do grafo e retomada."},
    {"name": "agents", "description": "Agentes registrados."},
    {"name": "knowledge", "description": "Base de conhecimento (RAG)."},
    {"name": "search", "description": "Busca semantica e hibrida."},
    {"name": "skills", "description": "Skills versionadas."},
    {"name": "playbooks", "description": "Playbooks de atendimento."},
    {"name": "keywords", "description": "Palavras-chave e sinonimos."},
    {"name": "feedback", "description": "Registro e analytics de feedback."},
    {
        "name": "feedback-agent",
        "description": "Feedback Agent: execucoes e propostas (aprovacao humana).",
    },
    {"name": "memory", "description": "Memoria de curto/longo prazo, semantica e episodica."},
    {
        "name": "audit",
        "description": "Auditoria por sessao: fluxo completo do agente e o porque da resposta.",
    },
    {
        "name": "tickets",
        "description": (
            "Chamados abertos pelo suporte guiado quando o problema nao foi resolvido: cliente, "
            "conversa, analise previa da IA e motivo."
        ),
    },
    {
        "name": "web-sources",
        "description": "Sites que o agente consulta quando a base nao responde (fontes web).",
    },
    {"name": "metrics", "description": "Metricas operacionais (JSON) e /metrics (Prometheus)."},
]

app = FastAPI(
    title="Getnet Multiagent Support Platform",
    version="0.2.0",
    description=_API_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
)
FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/ready,/metrics")

app.include_router(routes_health.router)
app.include_router(routes_agents.router)
app.include_router(routes_sessions.router)
app.include_router(routes_executions.router)
app.include_router(routes_knowledge.router)
app.include_router(routes_search.router)
app.include_router(routes_skills.router)
app.include_router(routes_playbooks.router)
app.include_router(routes_keywords.router)
app.include_router(routes_feedback.router)
app.include_router(routes_feedback_agent.router)
app.include_router(routes_memory.router)
app.include_router(routes_web_sources.router)
app.include_router(routes_audit.router)
app.include_router(routes_tickets.router)
app.include_router(routes_metrics.router)
app.include_router(routes_metrics.prometheus_router)


def _error_response(execution_id: str, error_code: str, message: str) -> AgentResponse:
    return AgentResponse(
        status=Status.ERROR,
        agent="api",
        message=message,
        metadata=ResponseMetadata(execution_id=execution_id, confidence=0.0, error_code=error_code),
    )


async def structured_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Nenhuma excecao crua e exposta ao usuario -- sempre um AgentResponse(status=ERROR).

    Chamada tanto pelo `@app.exception_handler(Exception)` (defesa em profundidade) quanto
    diretamente pelas proprias middlewares abaixo -- `BaseHTTPMiddleware.call_next()` (usado
    por `@app.middleware("http")`) pode deixar uma excecao nao tratada escapar do
    `ExceptionMiddleware` interno em certas versoes do Starlette quando ha middlewares
    customizadas no meio do caminho, entao cada middleware tambem envolve seu proprio
    `call_next` em try/except como garantia primaria, nao apenas o handler registrado.
    """
    execution_id = str(uuid.uuid4())
    logger.error("unhandled_exception", error=str(exc))
    response = _error_response(execution_id, "INTERNAL_ERROR", "Ocorreu um erro inesperado.")
    return JSONResponse(status_code=500, content=response.model_dump(mode="json"))


async def identity_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Identidade (X-User-Id, X-Session-Id...) ausente, invalida ou conflitante -> 400."""
    assert isinstance(exc, IdentityError)
    return JSONResponse(
        status_code=400,
        content=_error_response(str(uuid.uuid4()), exc.code, exc.message).model_dump(mode="json"),
    )


async def llm_api_key_missing_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=_error_response(
            str(uuid.uuid4()),
            "LLM_API_KEY_REQUIRED",
            f"Informe a chave do OpenRouter no cabecalho {LLM_API_KEY_HEADER}.",
        ).model_dump(mode="json"),
    )


_LLM_UPSTREAM_ERRORS = {
    401: (401, "LLM_API_KEY_INVALID", "O OpenRouter rejeitou a chave informada em X-API-Key-LLM."),
    403: (403, "LLM_ACCESS_DENIED", "A chave do OpenRouter nao tem acesso ao modelo solicitado."),
    402: (402, "LLM_INSUFFICIENT_CREDITS", "Saldo/creditos insuficientes na conta do OpenRouter."),
    429: (429, "LLM_RATE_LIMITED", "O OpenRouter limitou as requisicoes; tente novamente."),
}


async def llm_upstream_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Erro HTTP devolvido pelo provedor de LLM/embeddings -> resposta estruturada e clara, sem
    expor o corpo do erro do provedor (que pode conter identificadores da conta)."""
    upstream = getattr(exc, "status_code", None)
    status, code, message = _LLM_UPSTREAM_ERRORS.get(
        upstream, (502, "LLM_UPSTREAM_ERROR", "O provedor de LLM retornou um erro.")
    )
    logger.warning("llm_upstream_error", upstream_status=upstream, error_code=code)
    return JSONResponse(
        status_code=status,
        content=_error_response(str(uuid.uuid4()), code, message).model_dump(mode="json"),
    )


@app.middleware("http")
async def llm_credentials_middleware(request: Request, call_next):
    """Disponibiliza a chave do OpenRouter (X-API-Key-LLM) so durante esta requisicao -- nunca
    logada, auditada nem devolvida; descartada ao final (ver app/llm/credentials.py)."""
    token = set_request_api_key(request.headers.get(LLM_API_KEY_HEADER))
    try:
        if request.url.path.startswith("/api/v1") and mongodb_client.is_connected():
            # Completa os embeddings das definicoes que ficaram pendentes no boot (sem chave).
            try:
                await embed_pending_if_possible(mongodb_client.get_database())
            except Exception:  # noqa: BLE001 -- nunca derruba a requisicao
                logger.warning("definitions_lazy_embedding_failed")
        return await call_next(request)
    finally:
        reset_request_api_key(token)


@app.middleware("http")
async def internal_trust_boundary_middleware(request: Request, call_next):
    """FR-046: API acessivel apenas por canais internos ja autenticados da Getnet."""
    path = request.url.path
    is_protected = path.startswith("/api/v1") or path in _PROTECTED_NON_API_PATHS
    if path in _PUBLIC_PATHS or not is_protected:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 -- ultima linha de defesa, ver docstring acima
            return await structured_error_handler(request, exc)

    settings = get_settings()
    token = request.headers.get("X-Internal-Service-Token")
    if token != settings.internal_service_token:
        execution_id = str(uuid.uuid4())
        response = _error_response(execution_id, "UNAUTHORIZED_CHANNEL", "Canal nao autorizado.")
        return JSONResponse(status_code=403, content=response.model_dump(mode="json"))

    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 -- ultima linha de defesa, ver docstring acima
        return await structured_error_handler(request, exc)


_NO_AUDIT_PATHS = frozenset({"/health", "/ready", "/metrics"})


async def _record_http_request(request: Request, status_code: int, duration_s: float) -> None:
    """Metricas HTTP (baixa cardinalidade: template de rota, nunca o path bruto) e evento de
    auditoria `http_request` (fonte da latencia por endpoint). Nunca propaga falha."""
    try:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", None) or "unmatched"
        instruments = get_instruments()
        instruments.http_requests.add(
            1, {"endpoint": endpoint, "method": request.method, "status_code": str(status_code)}
        )
        instruments.http_request_duration.record(duration_s, {"endpoint": endpoint})
        if request.url.path not in _NO_AUDIT_PATHS:
            await safe_emit_audit_event(
                actor="system",
                actor_name=endpoint,
                event_type="http_request",
                status=str(status_code),
                duration_ms=duration_s * 1000,
                safe_metadata={"method": request.method},
            )
    except Exception:  # noqa: BLE001 -- observabilidade nunca derruba a resposta
        logger.warning("http_metrics_failed")


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    bind_context(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 -- ultima linha de defesa, ver docstring acima
        response = await structured_error_handler(request, exc)
    response.headers["X-Request-Id"] = request_id
    await _record_http_request(request, response.status_code, time.perf_counter() - start)
    return response


class _SlidingWindowRateLimiter:
    """Rate limiter simples em memoria (por processo) -- janela deslizante por chave
    (`user:<id>` quando disponivel no corpo, senao `ip:<endereco>`)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str, *, max_requests: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        hits = self._hits[key]
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= max_requests:
            return False
        hits.append(now)
        return True


_rate_limiter = _SlidingWindowRateLimiter()


async def _rate_limit_key(request: Request) -> str:
    header_user = request.headers.get(USER_ID_HEADER, "").strip()
    if header_user:
        return f"user:{header_user[:256]}"
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 -- corpo ausente/invalido, cai no fallback por IP
            body = None
        if isinstance(body, dict) and body.get("user_id"):
            return f"user:{body['user_id']}"
    client_ip = request.client.host if request.client else "unknown"
    return f"ip:{client_ip}"


@app.middleware("http")
async def rate_limiting_middleware(request: Request, call_next):
    """Limite de requisicoes por user_id/IP -- mitiga uso abusivo/excessivo (Constitution
    §Boas praticas, defesa complementar contra uso excessivo de tokens via chamadas repetidas)."""
    if not request.url.path.startswith("/api/v1"):
        return await call_next(request)

    settings = get_settings()
    key = await _rate_limit_key(request)
    if not _rate_limiter.is_allowed(
        key,
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    ):
        execution_id = str(uuid.uuid4())
        response = _error_response(
            execution_id,
            "RATE_LIMIT_EXCEEDED",
            "Numero de solicitacoes excedido. Tente novamente em instantes.",
        )
        return JSONResponse(status_code=429, content=response.model_dump(mode="json"))

    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 -- ultima linha de defesa, ver docstring acima
        return await structured_error_handler(request, exc)


app.add_exception_handler(LLMApiKeyMissingError, llm_api_key_missing_handler)
app.add_exception_handler(IdentityError, identity_error_handler)
app.add_exception_handler(openai.APIStatusError, llm_upstream_error_handler)
app.add_exception_handler(Exception, structured_error_handler)


def custom_openapi() -> dict:
    """OpenAPI com o esquema de seguranca do token interno, para o botao Authorize do Swagger."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        "InternalServiceToken"
    ] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Internal-Service-Token",
        "description": "Token do canal interno (INTERNAL_SERVICE_TOKEN do .env).",
    }
    for path, operations in schema["paths"].items():
        if path in _PUBLIC_PATHS:
            continue
        for operation in operations.values():
            # Preserva o esquema gerado pelo FastAPI (LLMApiKey, nas rotas que usam LLM).
            requirement: dict[str, list] = {"InternalServiceToken": []}
            for existing in operation.get("security", []):
                requirement.update(existing)
            operation["security"] = [requirement]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
