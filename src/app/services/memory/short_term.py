"""Memoria de curto prazo -- mensagens/estado recentes da sessao, injetados nos prompts dos
agentes (spec FR-031, curto prazo).

Curto prazo e servido diretamente por `sessions`/`messages`/`graph_checkpoints` (nao duplicado
em coleccao propria -- ver data-model.md secao "Memory Record"): este modulo apenas monta a
janela de contexto recente a partir das mensagens ja persistidas.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repository.messages_repository import MessageSender, MessagesRepository

DEFAULT_CONTEXT_WINDOW = 6


async def get_recent_turns(
    db: AsyncIOMotorDatabase, session_id: str, *, limit: int = DEFAULT_CONTEXT_WINDOW
) -> list[dict[str, str]]:
    """Retorna as ultimas `limit` mensagens da sessao, em ordem cronologica, como turnos
    {role, content} prontos para compor um prompt de LLM."""
    repo = MessagesRepository(db)
    messages = await repo.list_for_session(session_id, limit=1000)
    messages.sort(key=lambda m: m.created_at)
    recent = messages[-limit:]

    turns: list[dict[str, str]] = []
    for message in recent:
        content = message.payload.get("message")
        if not content:
            continue
        role = "user" if message.sender == MessageSender.USER else "assistant"
        turns.append({"role": role, "content": content})
    return turns


def format_turns_as_transcript(turns: list[dict[str, str]]) -> str:
    """Formata os turnos recentes como um pequeno transcript legivel para injetar no prompt
    do agente -- dado confiavel (historico da propria plataforma), diferente do conteudo
    externo de RAG/ferramentas, que sempre passa por `wrap_untrusted_content`."""
    if not turns:
        return ""
    lines = [f"{'Cliente' if t['role'] == 'user' else 'Assistente'}: {t['content']}" for t in turns]
    return "Contexto recente da conversa:\n" + "\n".join(lines)
