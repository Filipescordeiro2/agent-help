"""Configuracao da aplicacao via variaveis de ambiente (pydantic-settings).

Nenhum valor sensivel tem default hardcoded -- ausencia de uma variavel obrigatoria falha
explicitamente na inicializacao, nunca em silencio (Constitution SS Gestao de Credenciais).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017/getnet_support"

    # OpenRouter (camada unica de abstracao -- ver app/llm/openrouter_client.py)
    # A chave normalmente NAO fica aqui: vem por requisicao no cabecalho X-API-Key-LLM (ver
    # app/llm/credentials.py). Este valor e so um fallback opcional para uso sem requisicao.
    openrouter_api_key: str | None = None
    openrouter_model: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_provider: str = "openrouter"  # "openrouter" | "fake" (testes/dev offline)
    # Modelos de raciocinio (ex.: openai/gpt-5-nano) nao aceitam `temperature` customizada; o que se
    # ajusta e o esforco de raciocinio (minimal|low|medium|high) -- vazio nao envia o parametro.
    openrouter_reasoning_effort: str | None = "low"
    openrouter_timeout_seconds: float = Field(default=60.0, gt=0)
    # Teto de tokens de saida por chamada (inclui os de raciocinio). Sem ele o OpenRouter reserva o
    # maximo do modelo e recusa (402) contas com saldo baixo.
    openrouter_max_tokens: int = Field(default=4096, gt=0)

    # Embeddings -- dimensao nunca fixa no codigo (requisito explicito)
    embedding_model: str
    embedding_dimensions: int

    # Definicoes do agente (Skills, Playbooks, Keywords) versionadas em YAML: sincronizadas com o
    # MongoDB (com embeddings) no boot. `agent_definitions_dir` aponta para outra pasta (ex.:
    # volume montado) para editar sem recompilar; vazio = definicoes empacotadas.
    agent_definitions_enabled: bool = True
    agent_definitions_dir: str | None = None

    # Acesso a paginas web como base de conhecimento (RAG) -- ver app/rag/loaders/web.py.
    # Sempre com protecao contra SSRF (so hosts publicos); `web_allowed_domains` (lista separada por
    # virgula, casa o dominio e seus subdominios) restringe ainda mais; vazio = qualquer host
    # publico.
    web_access_enabled: bool = True
    web_allowed_domains: str = ""
    web_fetch_timeout_seconds: float = Field(default=20.0, gt=0)
    web_max_bytes: int = Field(default=2_000_000, gt=0)
    web_max_chars: int = Field(default=60_000, gt=0)
    web_max_redirects: int = Field(default=3, ge=0, le=10)
    web_max_urls_per_message: int = Field(default=2, ge=1, le=5)
    # Fontes web padrao (Mongo `web_sources`, API /api/v1/web-sources): quando a base nao responde o
    # agente consulta ate `web_max_sources_per_question` fontes habilitadas e ve qual tem a
    # informacao. `web_follow_links`: se a pagina inicial nao traz nada relevante, segue ate
    # `web_max_linked_pages` links do MESMO site cujo texto combina com a pergunta.
    web_max_sources_per_question: int = Field(default=5, ge=1, le=20)
    web_follow_links: bool = True
    web_max_linked_pages: int = Field(default=2, ge=0, le=5)
    # Resposta quando nem a base nem as fontes web tem a informacao.
    support_contact_message: str = (
        "Nao encontrei essa informacao na nossa base de conhecimento nem nos sites oficiais "
        "da Getnet que consultei. Para te ajudar melhor, entre em contato com a central de "
        "atendimento da Getnet."
    )
    # Piso de similaridade para um trecho de pagina web ser "relevante" (e salvo na base). Medido
    # com text-embedding-3-small: paginas relevantes chegam a 0.67-0.81; irrelevantes do mesmo site
    # (o texto de marca "Getnet" aproxima tudo) a ~0.54; fora do dominio ~0.32. Abaixo de 0.5
    # entra lixo.
    web_min_score: float = Field(default=0.60, ge=0.0, le=1.0)

    # Mensagem de boas-vindas (saudacao / "o que voce faz?"); vazio = a mensagem padrao.
    welcome_message: str | None = None

    # Feedback automatico: avaliacao de grounding com nota <= este valor (0-5) vira Feedback para o
    # Feedback Agent propor a melhoria (nunca aplica sozinho). Padrao 3: notas 0, 1, 2 e 3.
    auto_feedback_enabled: bool = True
    auto_feedback_max_score: int = Field(default=3, ge=0, le=5)

    # Suporte guiado (caso + chamado): perguntas de esclarecimento permitidas antes de desistir de
    # entender o problema, tentativas de solucao antes de abrir o chamado e quantas mensagens da
    # conversa vao no chamado.
    support_max_clarifications: int = Field(default=2, ge=0, le=5)
    support_max_solution_attempts: int = Field(default=2, ge=1, le=5)
    support_ticket_conversation_max_messages: int = Field(default=30, ge=2, le=200)

    # Auditoria por sessao (GET /api/v1/audit/...): conteudo (entrada do usuario, retornos de
    # ferramentas/agentes) mascarado (cartao, CPF/CNPJ, e-mail, telefone, chaves) e truncado.
    audit_capture_content: bool = True
    audit_capture_llm_io: bool = True  # prompts e saidas estruturadas dos modelos
    audit_max_content_chars: int = Field(default=2000, ge=200, le=20000)
    audit_max_llm_chars: int = Field(default=12000, ge=1000, le=100000)  # prompts/saidas

    # Observabilidade
    log_level: str = "INFO"
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "getnet-multiagent-support"

    # Grounding (Constitution Principio X) -- limiar inteiro entre 0 e 5, inclusive; so
    # `score < grounding_min_score` bloqueia a entrega.
    grounding_min_score: int = Field(default=3, ge=0, le=5)
    openrouter_model_grounding: str | None = None  # None -> usa openrouter_model

    # Feedback Agent (agente de retaguarda, Constitution Principio XII)
    feedback_agent_interval_minutes: int = Field(default=1440, gt=0)
    feedback_agent_schedule_enabled: bool = True
    feedback_agent_max_batch_size: int = Field(default=100, gt=0)

    # Fronteira de confianca da API (FR-046)
    internal_service_token: str

    # Limites operacionais (Constitution Principio I / boas praticas)
    max_graph_iterations: int = 12
    node_timeout_seconds: float = 30.0
    # Agente de dominio (LLM + leitura de paginas web): orcamento maior que o de um no simples.
    agent_timeout_seconds: float = 90.0
    tool_timeout_seconds: float = 15.0
    # Igual ao piso da web: o que a web salva (>= web_min_score) precisa ser reencontrado depois.
    knowledge_min_score: float = 0.60
    knowledge_default_top_k: int = 5

    # Rate limiting (por user_id quando disponivel no corpo, senao por IP)
    rate_limit_max_requests: int = 60
    rate_limit_window_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
