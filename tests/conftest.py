"""Fixtures compartilhadas dos testes de integracao.

Monta uma aplicacao FastAPI minima reaproveitando os routers/middlewares reais, sem disparar
o lifespan completo de app/main.py (que exigiria um MongoDB real) -- troca a conexao MongoDB
por um banco mongomock e as chamadas de LLM/embeddings por versoes controladas e deterministicas.
"""

from __future__ import annotations

import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import app.agent.customer_support.agent as customer_support_agent_module
import app.agent.customer_support.case_flow as case_flow_module
import app.agent.feedback_validation.agent as feedback_validation_agent_module
import app.agent.knowledge.agent as knowledge_agent_module
import app.agent.nodes.grounding_node as grounding_node_module
import app.agent.router.agent as router_agent_module
import app.llm.embeddings_client as embeddings_client_module
from app.agent import runtime as graph_runtime
from app.agent.feedback_agent import concurrency as feedback_agent_concurrency
from app.agent.graph import AGENT_HANDLERS
from app.config.settings import get_settings
from app.llm.credentials import LLMApiKeyMissingError
from app.main import (
    _rate_limiter,
    identity_error_handler,
    internal_trust_boundary_middleware,
    llm_api_key_missing_handler,
    llm_credentials_middleware,
    llm_upstream_error_handler,
    rate_limiting_middleware,
    request_context_middleware,
    structured_error_handler,
)
from app.observability.otel import init_telemetry
from app.repository.mongodb import client as mongodb_client
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
from app.routers.identity import IdentityError
from app.schemas.grounding import GroundingEvaluation
from app.services import session_concurrency as concurrency_module


@pytest.fixture
def test_db():
    db = AsyncMongoMockClient()["getnet_test"]
    mongodb_client._db = db
    yield db
    mongodb_client._db = None


@pytest.fixture(autouse=True)
def _isolated_database():
    """Nenhum teste toca o MongoDB real: sem `test_db`, o banco e um mongomock descartavel. (Nos e
    servicos que gravam por conta propria -- feedback automatico, caso de suporte -- chamariam
    `get_database()` e ficariam 30 s tentando conectar em localhost.)"""
    mongodb_client._db = AsyncMongoMockClient()["getnet_isolated"]
    yield
    mongodb_client._db = None


@pytest.fixture(autouse=True)
def _pin_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes nao dependem do `.env` local (que pode apontar para o OpenRouter real): provedor
    fake, sem chave de ambiente e embeddings de dimensao pequena."""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "embedding_dimensions", 8)
    monkeypatch.setattr(settings, "agent_definitions_enabled", False)
    monkeypatch.setattr(settings, "agent_definitions_dir", None)


@pytest.fixture(autouse=True)
def _reset_graph_registry():
    AGENT_HANDLERS.clear()
    graph_runtime.reset_compiled_graph()
    concurrency_module._session_locks.clear()
    feedback_agent_concurrency.reset_lock()
    _rate_limiter._hits.clear()
    yield
    AGENT_HANDLERS.clear()
    graph_runtime.reset_compiled_graph()
    concurrency_module._session_locks.clear()
    feedback_agent_concurrency.reset_lock()
    _rate_limiter._hits.clear()


@pytest.fixture
def internal_token(monkeypatch: pytest.MonkeyPatch) -> str:
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_service_token", "test-internal-token")
    return "test-internal-token"


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> dict[type, object]:
    """Mapa {Schema: instancia ou factory} consumido por um `get_structured_output` fake,
    aplicado ao Router Agent, Knowledge Agent e Customer Support Agent (cada um importou o
    nome separadamente, por isso todos os modulos sao corrigidos)."""
    responses: dict[type, object] = {}

    async def fake_get_structured_output(schema, _messages, **_kwargs):
        if schema not in responses:
            raise AssertionError(f"nenhuma resposta fake configurada para {schema}")
        value = responses[schema]
        return value() if callable(value) else value

    monkeypatch.setattr(router_agent_module, "get_structured_output", fake_get_structured_output)
    monkeypatch.setattr(knowledge_agent_module, "get_structured_output", fake_get_structured_output)
    monkeypatch.setattr(
        customer_support_agent_module, "get_structured_output", fake_get_structured_output
    )
    monkeypatch.setattr(case_flow_module, "get_structured_output", fake_get_structured_output)
    monkeypatch.setattr(grounding_node_module, "get_structured_output", fake_get_structured_output)
    monkeypatch.setattr(
        feedback_validation_agent_module, "get_structured_output", fake_get_structured_output
    )
    # Suporte guiado: por padrao a analise do problema pede esclarecimento (cada teste do fluxo
    # configura o que precisa) e o "deu certo?" ambiguo vira informacao nova.
    responses.setdefault(
        case_flow_module.ProblemAssessment,
        case_flow_module.ProblemAssessment(
            understood=False,
            category="other",
            problem_summary="",
            clarifying_question="Qual e o problema ou a mensagem de erro?",
            confidence=0.3,
        ),
    )
    responses.setdefault(
        case_flow_module.FollowUpClassification,
        case_flow_module.FollowUpClassification(outcome="NEW_INFORMATION", reason="teste"),
    )
    # Default: o no de grounding aprova (score 5) salvo quando o teste configura outro valor.
    responses.setdefault(
        GroundingEvaluation,
        GroundingEvaluation(
            score=5,
            reasoning="default de teste",
            adheres_to_question=True,
            uses_context_correctly=True,
            no_unsupported_claims=True,
            is_complete=True,
        ),
    )
    return responses


@pytest.fixture
def fake_embeddings(monkeypatch: pytest.MonkeyPatch):
    """Embeddings deterministicos e controlaveis: textos que compartilham uma palavra-chave
    relevante ficam semanticamente proximos; os demais ficam distantes.

    Corrige `get_embeddings_client()` na propria origem (app.llm.embeddings_client) -- todo
    modulo que chama `embed_text`/`embed_texts` (retrievers, entity_search, skills/playbooks
    service, rotas de busca) passa por essas duas funcoes, que resolvem
    `get_embeddings_client()` no namespace do proprio modulo em tempo de chamada, entao um
    unico ponto de patch cobre todos os chamadores, independente de quem importou o que."""

    keywords = ["maquininha", "taxa", "conexao", "dispositivo"]

    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        vector = [1.0 if kw in lowered else 0.0 for kw in keywords]
        if not any(vector):
            vector = [0.0, 0.0, 0.0, 1.0]
            vector[-1] = 0.01  # evita vetor nulo (norma zero)
        return vector

    class _FakeEmbeddingsClient:
        def embed_query(self, text: str) -> list[float]:
            return _vector(text)

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [_vector(t) for t in texts]

    monkeypatch.setattr(
        embeddings_client_module, "get_embeddings_client", lambda: _FakeEmbeddingsClient()
    )
    return _vector


def _build_test_app() -> FastAPI:
    init_telemetry()  # metricas/spans reais nos testes (idempotente)
    test_app = FastAPI()
    test_app.middleware("http")(llm_credentials_middleware)
    test_app.middleware("http")(internal_trust_boundary_middleware)
    test_app.middleware("http")(request_context_middleware)
    test_app.middleware("http")(rate_limiting_middleware)
    test_app.add_exception_handler(LLMApiKeyMissingError, llm_api_key_missing_handler)
    test_app.add_exception_handler(IdentityError, identity_error_handler)
    test_app.add_exception_handler(openai.APIStatusError, llm_upstream_error_handler)
    test_app.add_exception_handler(Exception, structured_error_handler)
    test_app.include_router(routes_health.router)
    test_app.include_router(routes_agents.router)
    test_app.include_router(routes_sessions.router)
    test_app.include_router(routes_executions.router)
    test_app.include_router(routes_knowledge.router)
    test_app.include_router(routes_search.router)
    test_app.include_router(routes_skills.router)
    test_app.include_router(routes_playbooks.router)
    test_app.include_router(routes_keywords.router)
    test_app.include_router(routes_feedback.router)
    test_app.include_router(routes_feedback_agent.router)
    test_app.include_router(routes_memory.router)
    test_app.include_router(routes_web_sources.router)
    test_app.include_router(routes_audit.router)
    test_app.include_router(routes_tickets.router)
    test_app.include_router(routes_metrics.router)
    test_app.include_router(routes_metrics.prometheus_router)
    return test_app


@pytest.fixture
def client(test_db, internal_token: str, fake_embeddings) -> TestClient:
    test_app = _build_test_app()
    return TestClient(test_app, headers={"X-Internal-Service-Token": internal_token})
