"""Knowledge Agent -- responde via RAG, nunca sem contexto suficiente (spec FR-011 a FR-016).

Fluxo: 1) base de conhecimento; 2) se nao responde, paginas web (URLs da mensagem e fontes web
cadastradas), salvando na base so o relevante; 3) se nada tem a informacao, orienta o cliente a
procurar a central de atendimento. Cada decisao vai para a trilha de auditoria.
"""

from __future__ import annotations

from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.contracts import Agent
from app.agent.state import GraphState
from app.agent.topic_selection import select_topic
from app.config.settings import get_settings
from app.llm.structured_output import get_structured_output
from app.observability.trace import emit_trace
from app.rag.loaders.web import error_codes, extract_urls, strip_urls
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata, SourceRef
from app.security.policies import wrap_untrusted_content
from app.services.memory.short_term import format_turns_as_transcript, get_recent_turns
from app.tools.base import ToolTimeoutError
from app.tools.knowledge_tools import SearchInput, SearchKnowledgeTool
from app.tools.web_tools import SearchWebPagesTool, WebSearchInput, WebSearchOutput

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "knowledge" / "v1.md"


class KnowledgeAnswerDraft(BaseModel):
    """Saida estruturada intermediaria do LLM -- nunca a resposta final direto de texto livre."""

    message: str
    grounded_in_sources: bool


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


class KnowledgeAgent(Agent):
    name = "knowledge_agent"

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db
        self._search_tool = SearchKnowledgeTool(db)
        self._web_tool = SearchWebPagesTool(db)

    async def handle(self, state: GraphState) -> AgentResponse:
        settings = get_settings()
        message = state["user_message"].message
        execution_id = state["execution_id"]

        # 1) A base semantica vem primeiro. URLs atrapalham a busca, entao saem da query.
        kb_query = strip_urls(message) or message
        search_result = await self._search_tool.run(
            SearchInput(
                query=kb_query,
                top_k=settings.knowledge_default_top_k,
                min_score=settings.knowledge_min_score,
            )
        )
        # Pergunta com CODIGO DE ERRO ("ADQ 4-91"): trecho da base de outro codigo (ADQ 4-83, IDL
        # 1-04...) e parecido no texto e passa no piso de similaridade, mas nao responde. So vale o
        # trecho que cita o MESMO codigo; sem nenhum, a base "nao tem resposta" e vai a web.
        kb_dropped = 0
        wanted_codes = error_codes(kb_query)
        if wanted_codes:
            kept = [r for r in search_result.results if wanted_codes & error_codes(r["content"])]
            kb_dropped = len(search_result.results) - len(kept)
            search_result.results = kept
        kb_scores = [r["score"] for r in search_result.results]

        # 2) So se a base NAO responde o agente vai a web: primeiro as URLs da mensagem e, se nada
        # relevante vier delas, as fontes web cadastradas (todas, ver qual tem a informacao). A tool
        # salva na base vetorial so os trechos relevantes (da proxima vez, sem ir ao site).
        # Retentativa apos o grounding reprovar uma resposta que veio SO da base: o trecho da base
        # passou no piso de similaridade mas nao serviu (ex.: outro codigo de erro, outro assunto);
        # entao consulta tambem as fontes web antes de responder de novo.
        retry_with_web = bool(state.get("grounding_feedback")) and not state.get("web_consulted")
        web_attempted = not search_result.results or retry_with_web
        web_result = WebSearchOutput(results=[], errors=[])
        if web_attempted:
            web_result = await self._search_web(message)
            state["web_consulted"] = True

        decision = {
            "question": kb_query,
            "knowledge_base": {
                "results": len(kb_scores),
                "best_score": max(kb_scores) if kb_scores else None,
                "min_score": settings.knowledge_min_score,
                "dropped_other_error_code": kb_dropped,
                "documents": [
                    {"document_id": r["document_id"], "score": r["score"]}
                    for r in search_result.results
                ],
            },
            "web": {
                "attempted": web_attempted,
                "reason": (
                    (
                        "base_sem_resposta"
                        if not search_result.results
                        else "grounding_reprovou_base"
                    )
                    if web_attempted
                    else None
                ),
                "urls_in_message": extract_urls(message, limit=5),
                "consulted": web_result.consulted,
                "errors": web_result.errors,
                "saved_chunks": len(web_result.results),
            },
        }

        if not search_result.results and not web_result.results:
            await emit_trace(
                "knowledge_decision",
                actor="agent",
                actor_name=self.name,
                details={**decision, "outcome": "sem_informacao_encaminhar_central"},
            )
            return AgentResponse(
                status=Status.INSUFFICIENT_CONTEXT,
                agent=self.name,
                message=_no_information_message(web_result),
                sources=[],
                metadata=ResponseMetadata(execution_id=execution_id, confidence=0.0),
            )

        context_lines = [
            f"[Fonte {r['document_id']}] {r['content']}" for r in search_result.results
        ] + [
            f"[Fonte web {r['document_id']} {r['url']}] {r['content']}" for r in web_result.results
        ]
        context_block = "\n\n".join(context_lines)
        recent_turns = await get_recent_turns(self._db, state["session_id"])
        transcript = format_turns_as_transcript(recent_turns)
        # Tipo de solicitacao: Keywords -> Playbook do assunto -> Skills. Sem Playbook do assunto,
        # o agente raciocina sozinho sobre o que reuniu (modo "sem playbook").
        topic = await select_topic(self._db, owning_agent=self.name, message=message)
        topic_guidance = topic.guidance()

        await emit_trace(
            "knowledge_decision",
            actor="agent",
            actor_name=self.name,
            details={
                **decision,
                "outcome": (
                    "respondeu_com_a_base_e_a_web"
                    if search_result.results and web_result.results
                    else "respondeu_com_a_base"
                    if search_result.results
                    else "respondeu_com_a_web"
                ),
                **topic.trace_details(),
            },
        )

        # Contexto recuperado exposto ao no de grounding (avalia a resposta contra ele).
        state["grounding_context"] = [
            *state.get("grounding_context", []),
            *(r["content"] for r in search_result.results),
            *(r["content"] for r in web_result.results),
        ]

        system_prompt = _load_prompt() + "\n\n" + wrap_untrusted_content(context_block)
        if state.get("grounding_feedback"):
            system_prompt += "\n\n" + wrap_untrusted_content(
                "Avaliacao da tentativa anterior (corrija a resposta): "
                + state["grounding_feedback"]
            )
        if topic_guidance:
            system_prompt += "\n\n" + topic_guidance
        if state.get("answer_mode") == "support_steps":
            system_prompt += (
                "\n\nModo suporte: o cliente ja descreveu o problema. Responda SO com o passo a "
                "passo (numerado) para resolver, exatamente como a fonte traz. NAO faca perguntas, "
                "NAO ofereca encaminhar a atendente e NAO pergunte se deu certo: o fluxo de "
                "suporte faz isso depois."
            )
        if transcript:
            system_prompt += "\n\n" + transcript

        draft = await get_structured_output(
            KnowledgeAnswerDraft,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )

        sources = [
            SourceRef(document_id=r["document_id"], chunk_id=r.get("chunk_id"), score=r["score"])
            for r in search_result.results
        ] + [
            SourceRef(
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                score=r["score"],
                url=r["url"],
            )
            for r in web_result.results
        ]
        confidence = max(source.score for source in sources)

        return AgentResponse(
            status=Status.OK,
            agent=self.name,
            message=draft.message,
            sources=sources,
            metadata=ResponseMetadata(
                execution_id=execution_id,
                confidence=confidence,
                grounded_in_sources=draft.grounded_in_sources,
            ),
        )

    async def _search_web(self, message: str) -> WebSearchOutput:
        """Trechos das paginas web (citadas ou fontes cadastradas); vazio se nada serviu."""
        settings = get_settings()
        try:
            return await self._web_tool.run(
                WebSearchInput(
                    query=message,
                    top_k=settings.knowledge_default_top_k,
                    min_score=settings.web_min_score,
                )
            )
        except ToolTimeoutError:
            return WebSearchOutput(
                results=[],
                errors=[
                    {
                        "code": "WEB_FETCH_FAILED",
                        "message": "A consulta as paginas web demorou demais.",
                    }
                ],
            )


def _no_information_message(web_result: WebSearchOutput) -> str:
    """Nem a base nem a web tem a informacao: orienta a procurar a central de atendimento.

    Detalhes tecnicos (qual site falhou, por que) ficam so na auditoria, nao na resposta."""
    contact = get_settings().support_contact_message
    link_failed = any(
        entry.get("source_id") is None and entry.get("status") != "ok"
        for entry in web_result.consulted
    )
    if link_failed:
        return f"Nao consegui acessar o link informado. {contact}"
    return contact
