"""Servico de auditoria por sessao: reconstroi TUDO que o agente fez em cada turno.

Le a trilha gravada em `audit_events` (ver `observability/trace.py`) e monta, por mensagem do
usuario: a entrada, a linha do tempo (nos, sub-agentes, ferramentas, chamadas ao modelo, retornos) e
uma **explicacao em portugues** do porque da resposta -- gerada de forma deterministica a partir dos
eventos (nunca por um LLM), para ser confiavel como evidencia.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.audit_events_repository import AuditEvent, AuditEventsRepository
from app.repository.sessions_repository import SessionsRepository
from app.schemas.audit import AuditEventOut, SessionAudit, TimelineEvent, TurnAudit

_LABELS = {
    "user_input": "Entrada do usuario",
    "final_response": "Resposta final entregue",
    "output_blocked": "Resposta barrada pela verificacao de saida",
    "knowledge_decision": "Decisao do Knowledge Agent (base x web)",
    "grounding_evaluated": "Avaliacao de grounding",
    "llm_call": "Uso de tokens do modelo",
    "http_request": "Requisicao HTTP",
}
_NODE_LABELS = {
    "security": "Verificacao de seguranca da entrada",
    "router": "Router: classificacao da intencao",
    "clarification": "Pedido de esclarecimento",
    "dispatch": "Despacho para o agente",
    "multi_agent_dispatch": "Despacho para varios agentes",
    "compose_response": "Composicao da resposta",
    "grounding": "No de grounding (avalia a resposta)",
    "validate_response": "Validacao da resposta",
    "persist": "Persistencia da sessao/memoria",
    "security_blocked": "Bloqueio de seguranca",
}


class SessionNotFound(Exception):
    pass


class ExecutionNotFound(Exception):
    pass


def _label(event: AuditEvent) -> str:
    if event.event_type == "node_call":
        return _NODE_LABELS.get(event.actor_name, f"No {event.actor_name}")
    if event.event_type == "agent_call":
        return f"Agente {event.actor_name}"
    if event.event_type == "tool_call":
        return f"Ferramenta {event.actor_name}"
    if event.event_type == "llm_io":
        return f"Chamada ao modelo ({event.actor_name})"
    return _LABELS.get(event.event_type, event.event_type)


def _timeline_event(order: int, event: AuditEvent) -> TimelineEvent:
    return TimelineEvent(
        order=order,
        at=event.created_at,
        type=event.event_type,
        label=_label(event),
        actor=event.actor,
        name=event.actor_name,
        node=event.node_name,
        status=event.status,
        duration_ms=event.duration_ms,
        error_code=event.error_code,
        metadata=event.safe_metadata,
        details=event.details,
    )


def _first(events: list[AuditEvent], event_type: str, name: str | None = None) -> AuditEvent | None:
    for event in events:
        if event.event_type == event_type and (name is None or event.actor_name == name):
            return event
    return None


def _details(event: AuditEvent | None) -> dict[str, Any]:
    return (event.details or {}) if event else {}


def _fmt_score(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, int | float) else "n/d"


def _support_case_line(info: dict[str, Any]) -> str:
    """Uma frase por passo do suporte guiado (entender -> resolver -> confirmar -> chamado)."""
    action = info.get("action")
    summary = info.get("problem_summary") or "problema ainda nao detalhado"
    if action == "clarification_asked":
        return (
            "Suporte: o problema ainda nao estava claro, entao o agente PERGUNTOU ao cliente "
            f'(pergunta {info.get("clarification_count")}): "{info.get("detail")}". Nao foi '
            "buscar solucao nem abrir chamado antes de entender."
        )
    if action == "solution_proposed":
        return (
            f"Suporte: problema entendido ({summary}). O agente passou a solucao (tentativa "
            f"{info.get('attempts')}) e perguntou se deu certo; o caso ficou aguardando a "
            "confirmacao do cliente."
        )
    if action == "resolved":
        return "Suporte: o cliente confirmou que deu certo; o caso foi encerrado sem chamado."
    if action == "ticket_opened":
        return (
            f"Suporte: foi aberto o chamado {info.get('ticket_id')} (motivo: {info.get('detail')}) "
            "com os dados do cliente, a conversa e a analise previa; o cliente recebeu o numero."
        )
    if action == "needs_central":
        return (
            "Suporte: mesmo apos as perguntas permitidas o problema continuou vago; o cliente foi "
            "orientado a procurar a central de atendimento (nenhum chamado foi aberto)."
        )
    if action == "superseded":
        return "Suporte: o cliente mudou de assunto; o caso foi encerrado e a nova pergunta seguiu."
    return f"Suporte: {action}."


def build_explanation(events: list[AuditEvent]) -> list[str]:
    """Frases em portugues que respondem "por que o agente fez/respondeu isso?"."""
    lines: list[str] = []

    user_input = _details(_first(events, "user_input")).get("message")
    if user_input:
        lines.append(f'O usuario perguntou: "{user_input}".')

    security = _details(_first(events, "node_call", "security"))
    if security.get("blocked"):
        lines.append(
            "A entrada foi BLOQUEADA pela verificacao de seguranca "
            f"(motivo: {security.get('reason')}); nenhum agente foi acionado."
        )
    elif security:
        lines.append("A entrada passou pela verificacao de seguranca.")

    router = _details(_first(events, "node_call", "router"))
    if router:
        target = router.get("target_agent") or ", ".join(router.get("target_sequence") or []) or "-"
        lines.append(
            f"O Router classificou a intencao como {router.get('intent')} "
            f"(confianca {_fmt_score(router.get('confidence'))}, "
            f"codigo {router.get('reason_code')}) "
            f"e encaminhou para: {target}."
        )
        if router.get("intent") == "GREETING":
            lines.append(
                "A mensagem era so conversa social (saudacao, agradecimento, despedida ou "
                "pedido de ajuda), sem pergunta: respondi com a mensagem de boas-vindas, sem "
                "consultar a base de conhecimento, os sites nem o modelo."
            )
        if router.get("reason_code") == "ERROR_CODE_IN_MESSAGE":
            lines.append(
                "A mensagem trazia um codigo de erro da maquininha; por regra, foi para o "
                "Knowledge Agent, que consulta a pagina de ajuda do codigo (passo a passo)."
            )
        if router.get("reason_code") == "URL_IN_MESSAGE":
            lines.append(
                "A mensagem trazia um link e o modelo nao soube classificar; por regra, foi para o "
                "Knowledge Agent."
            )

    for event in events:
        if event.event_type == "tool_call" and event.actor_name == "search_knowledge":
            output = (event.details or {}).get("output", {})
            results = output.get("results", []) if isinstance(output, dict) else []
            lines.append(
                f"A ferramenta search_knowledge consultou a base de conhecimento e retornou "
                f"{len(results)} trecho(s)."
            )

    decision = _details(_first(events, "knowledge_decision"))
    if decision:
        kb = decision.get("knowledge_base", {})
        web = decision.get("web", {})
        if kb.get("dropped_other_error_code"):
            lines.append(
                f"{kb['dropped_other_error_code']} trecho(s) da base foram descartados: eram de "
                "OUTRO codigo de erro (parecidos no texto, mas nao servem para este codigo)."
            )
        if kb.get("results") and web.get("reason") == "grounding_reprovou_base":
            lines.append(
                f"A base trouxe {kb['results']} trecho(s) (melhor similaridade "
                f"{_fmt_score(kb.get('best_score'))}), mas o grounding reprovou a resposta feita "
                "so com eles; na retentativa o agente tambem consultou os sites."
            )
        elif kb.get("results"):
            lines.append(
                f"A base de conhecimento respondeu: {kb['results']} trecho(s), melhor "
                f"similaridade {_fmt_score(kb.get('best_score'))} "
                f"(minimo exigido {_fmt_score(kb.get('min_score'))}). "
                "Por isso o agente NAO foi aos sites."
            )
        else:
            lines.append(
                f"A base de conhecimento NAO tinha resposta (nenhum trecho com similaridade >= "
                f"{_fmt_score(kb.get('min_score'))})."
            )
        if web.get("attempted"):
            consulted = web.get("consulted", [])
            if not consulted:
                lines.append("Nao havia URL na mensagem nem fonte web habilitada para consultar.")
            for entry in consulted:
                name = entry.get("source_name") or "URL da mensagem"
                if entry.get("status") != "ok":
                    lines.append(
                        f"Consulta a {name} ({entry.get('url')}): falhou ({entry.get('status')})."
                    )
                    continue
                followed = entry.get("followed_links", [])
                extra = f", seguindo {len(followed)} link(s) do mesmo site" if followed else ""
                if not entry.get("relevant_chunks"):
                    verdict = "nao trouxe nada relevante"
                elif entry.get("used_in_answer"):
                    verdict = "trouxe trechos relevantes, usados na resposta"
                else:
                    verdict = "trouxe trechos, mas os de outras fontes foram mais relevantes"
                lines.append(f"Consulta a {name} ({entry.get('url')}){extra}: {verdict}.")
            if web.get("saved_chunks"):
                lines.append(
                    f"{web['saved_chunks']} trecho(s) relevante(s) foram salvos na base vetorial "
                    "(na proxima vez, a resposta sai da base)."
                )
        outcome = decision.get("outcome")
        if outcome == "sem_informacao_encaminhar_central":
            lines.append(
                "Nem a base nem os sites tinham a informacao: o cliente foi orientado a procurar a "
                "central de atendimento."
            )
        if decision.get("keywords_matched"):
            terms = ", ".join(decision["keywords_matched"])
            lines.append(f"Keywords do dominio encontradas na mensagem: {terms}.")
        if decision.get("playbook_mode") == "playbook_especifico":
            how = (
                "pelas Keywords"
                if decision.get("playbook_reason") == "keyword"
                else "por similaridade com a mensagem"
            )
            topics = ", ".join(decision.get("topic_playbooks") or [])
            lines.append(f"Sub-playbook do assunto escolhido {how}: {topics}.")
        elif decision.get("playbook_mode") == "sem_playbook":
            lines.append(
                "Nenhum sub-playbook cobre este assunto: o agente seguiu so o principal e "
                "raciocinou sobre o que reuniu (historico, base e paginas) para decidir se dava "
                "para responder."
            )
        if decision.get("skills_applied"):
            skills = ", ".join(decision["skills_applied"])
            lines.append(f"Skills aplicadas no prompt: {skills}.")
        if decision.get("playbooks_applied"):
            playbooks = ", ".join(decision["playbooks_applied"])
            lines.append(f"Playbook seguido como procedimento: {playbooks}.")

    for event in events:
        if event.event_type == "support_case":
            lines.append(_support_case_line(event.details or {}))
    grounding = _details(_first(events, "grounding_evaluated"))
    if grounding:
        lines.append(
            f"Grounding: nota {grounding.get('score')}/5 (minimo {grounding.get('min_score')}). "
            f"Justificativa: {grounding.get('reasoning')}"
        )
    for event in events:
        if event.event_type == "auto_feedback_created":
            info = event.details or {}
            lines.append(
                f"Nota de grounding {info.get('score')}/5 (baixa): foi registrado um feedback "
                f"automatico ({info.get('problem_classification')}) para o Feedback Agent "
                "propor como melhorar (nada e aplicado sem aprovacao humana)."
            )
    grounding_node = _details(_first(events, "node_call", "grounding"))
    if grounding_node.get("retry_requested"):
        lines.append(
            "A resposta foi reprovada pelo grounding e o agente tentou de novo com o "
            "feedback do avaliador."
        )

    blocked = _details(_first(events, "output_blocked"))
    if blocked:
        lines.append(
            f"A resposta gerada pelo agente {blocked.get('agent')} foi BARRADA pela verificacao de "
            f"saida (motivo: {blocked.get('reason')}) e trocada por uma mensagem de erro segura."
        )
    final = _details(_first(events, "final_response"))
    if final:
        lines.append(
            f"Resposta final: status {final.get('status')} pelo agente {final.get('agent')}: "
            f'"{final.get("message")}"'
        )
    return lines


def build_summary(events: list[AuditEvent]) -> dict[str, Any]:
    router = _details(_first(events, "node_call", "router"))
    decision = _details(_first(events, "knowledge_decision"))
    grounding = _details(_first(events, "grounding_evaluated"))
    final = _details(_first(events, "final_response"))
    usage = [e.safe_metadata for e in events if e.event_type == "llm_call"]
    return {
        "route": {
            k: router.get(k)
            for k in ("intent", "target_agent", "target_sequence", "confidence", "reason_code")
        }
        if router
        else None,
        "agents": [
            {"agent": e.actor_name, "status": e.status, "duration_ms": e.duration_ms}
            for e in events
            if e.event_type == "agent_call"
        ],
        "nodes": [
            {"node": e.actor_name, "status": e.status, "duration_ms": e.duration_ms}
            for e in events
            if e.event_type == "node_call"
        ],
        "tools": [
            {"tool": e.actor_name, "status": e.status, "duration_ms": e.duration_ms}
            for e in events
            if e.event_type == "tool_call"
        ],
        "knowledge": decision or None,
        "grounding": {"score": grounding.get("score"), "reasoning": grounding.get("reasoning")}
        if grounding
        else None,
        "llm": {
            "calls": len(usage),
            "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
            "completion_tokens": sum(u.get("completion_tokens", 0) for u in usage),
            "total_tokens": sum(u.get("total_tokens", 0) for u in usage),
        },
        "final": {"status": final.get("status"), "agent": final.get("agent")} if final else None,
    }


def _turn(execution_id: str, events: list[AuditEvent], *, include_llm: bool) -> TurnAudit:
    visible = [e for e in events if include_llm or e.event_type != "llm_io"]
    started, finished = events[0].created_at, events[-1].created_at
    final = _details(_first(events, "final_response"))
    return TurnAudit(
        execution_id=execution_id,
        message_id=events[0].message_id,
        request_id=events[0].request_id,
        started_at=started,
        finished_at=finished,
        duration_ms=max(0.0, (finished - started).total_seconds() * 1000),
        user_input=_details(_first(events, "user_input")).get("message"),
        final_status=final.get("status"),
        final_response=final or None,
        explanation=build_explanation(events),
        summary=build_summary(events),
        timeline=[_timeline_event(i, e) for i, e in enumerate(visible, start=1)],
    )


def _group_by_execution(events: list[AuditEvent]) -> dict[str, list[AuditEvent]]:
    turns: dict[str, list[AuditEvent]] = {}
    for event in events:
        if event.execution_id and event.event_type != "http_request":
            turns.setdefault(event.execution_id, []).append(event)
    return turns


async def get_session_audit(
    db: AsyncIOMotorDatabase, session_id: str, *, include_llm: bool = True
) -> SessionAudit:
    session = await SessionsRepository(db).get(session_id)
    if session is None:
        raise SessionNotFound(session_id)
    events = await AuditEventsRepository(db).list_for_session(session_id)
    turns = [
        _turn(execution_id, group, include_llm=include_llm)
        for execution_id, group in _group_by_execution(events).items()
    ]
    turns.sort(key=lambda t: t.started_at)
    return SessionAudit(
        session_id=session.session_id,
        user_id=session.user_id,
        channel=session.channel,
        status=session.status.value,
        created_at=session.created_at,
        total_turns=len(turns),
        turns=turns,
    )


async def get_execution_audit(
    db: AsyncIOMotorDatabase, execution_id: str, *, include_llm: bool = True
) -> TurnAudit:
    events = await AuditEventsRepository(db).list_for_execution(execution_id)
    events = [e for e in events if e.event_type != "http_request"]
    if not events:
        raise ExecutionNotFound(execution_id)
    return _turn(execution_id, events, include_llm=include_llm)


async def list_session_events(
    db: AsyncIOMotorDatabase,
    session_id: str,
    *,
    event_type: str | None = None,
    limit: int = 200,
    cursor: int = 0,
) -> list[AuditEventOut]:
    if await SessionsRepository(db).get(session_id) is None:
        raise SessionNotFound(session_id)
    events = await AuditEventsRepository(db).list_for_session(
        session_id, event_types=[event_type] if event_type else None
    )
    page = events[cursor : cursor + limit]
    return [
        AuditEventOut(
            **_timeline_event(cursor + i, e).model_dump(),
            event_id=e.event_id,
            request_id=e.request_id,
            session_id=e.session_id,
            message_id=e.message_id,
            execution_id=e.execution_id,
        )
        for i, e in enumerate(page, start=1)
    ]


__all__ = [
    "ExecutionNotFound",
    "SessionNotFound",
    "build_explanation",
    "build_summary",
    "get_execution_audit",
    "get_session_audit",
    "list_session_events",
]
