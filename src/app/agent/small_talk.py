"""Conversa social (saudacao, agradecimento, despedida, pedido de ajuda) -- deterministica.

Uma mensagem como "Ola" nao tem nada a procurar na base de conhecimento nem nos sites: o cliente
so quer ser recebido. Reconhecemos esses casos por codigo (sem chamar o modelo) para responder
na hora, sem custo e sem depender da chave do LLM, com uma mensagem que apresenta o que o
assistente sabe fazer. So vale para mensagens que sao APENAS conversa social: se houver uma
pergunta junto ("Ola, como estorno uma venda?"), segue o fluxo normal.
"""

from __future__ import annotations

import re
import unicodedata

GREETING = "GREETING"
THANKS = "THANKS"
FAREWELL = "FAREWELL"
HELP_REQUEST = "HELP_REQUEST"

_MAX_TOKENS = 8

_GREETING_WORDS = {
    "oi", "ola", "oie", "opa", "eai", "eae", "hey", "hello", "hi", "salve", "alo", "bom", "boa",
    "dia", "tarde", "noite", "tudo", "bem", "com", "voce", "vc", "e", "como", "vai", "vao", "ai",
    "td", "beleza", "tranquilo", "prezados", "pessoal", "gente", "time", "equipe", "getnet",
    "de", "por", "favor", "aqui", "sr", "sra",
}  # fmt: skip
# Precisa de ao menos uma destas para ser saudacao.
_GREETING_ANCHORS = {
    "oi", "ola", "oie", "opa", "eai", "eae", "hey", "hello", "hi", "salve", "alo", "bom", "boa",
    "tudo", "beleza",
}  # fmt: skip
_THANKS_WORDS = {
    "obrigado", "obrigada", "obg", "obgd", "valeu", "vlw", "brigado", "brigada", "muito", "mto",
    "agradeco", "agradecido", "agradecida", "thanks", "thank", "you", "show", "ajudou", "me",
    "ok", "certo", "entendi", "perfeito", "otimo", "excelente", "grato", "grata", "demais", "a",
    "o", "pela", "pelo", "ajuda", "atencao", "e", "de", "pra", "para", "tudo", "sim",
}  # fmt: skip
_THANKS_ANCHORS = {
    "obrigado", "obrigada", "obg", "obgd", "valeu", "vlw", "brigado", "brigada", "agradeco",
    "agradecido", "agradecida", "thanks", "thank", "grato", "grata",
}  # fmt: skip
_FAREWELL_WORDS = {
    "tchau", "xau", "ate", "logo", "mais", "breve", "amanha", "falou", "flw", "adeus", "bye",
    "abraco", "abracos", "fui", "encerrar", "so", "isso", "era", "por", "hoje", "boa", "noite",
    "tarde", "dia", "um", "tenha", "otimo", "e",
}  # fmt: skip
_FAREWELL_ANCHORS = {
    "tchau", "xau", "adeus", "bye", "flw", "falou", "fui", "abraco", "abracos", "ate", "encerrar",
}  # fmt: skip

_HELP_PATTERNS = [
    re.compile(p)
    for p in (
        r"^(ajuda|help|menu|opcoes|comandos|inicio|start)$",
        r"^(me )?(ajuda|ajude)$",
        r"^quem (e|eh) (voce|vc)$",
        r"^(o que|oq|que) (voce|vc) (pode|consegue|sabe|faz|responde)( fazer| responder)?$",
        r"^como (voce|vc) (pode|consegue) (me )?ajudar$",
        r"^(com o que|em que) (voce|vc) (pode|consegue) (me )?ajudar$",
        r"^(no que|em que) (voce|vc) me ajuda$",
        r"^(o que|oq) (posso|eu posso|da pra) (te )?(perguntar|fazer|pedir)$",
        r"^(quais|que) (sao )?(as )?(suas )?(funcoes|funcionalidades|opcoes|servicos|assuntos)$",
    )
]


def _normalize(message: str) -> str:
    text = unicodedata.normalize("NFKD", message.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"(.)\1{2,}", r"\1", text)  # "oiii" -> "oi", "olaaaa" -> "ola"
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\be ai\b", "eai", text)


def classify_small_talk(message: str) -> str | None:
    """Devolve GREETING/THANKS/FAREWELL/HELP_REQUEST quando a mensagem e so conversa social."""
    text = _normalize(message)
    if not text:
        return None
    if any(pattern.match(text) for pattern in _HELP_PATTERNS):
        return HELP_REQUEST
    tokens = text.split()
    if len(tokens) > _MAX_TOKENS:
        return None
    words = set(tokens)
    # Despedida/agradecimento antes de saudacao: "boa noite, obrigado" e despedida.
    if words & _FAREWELL_ANCHORS and words <= _FAREWELL_WORDS | _THANKS_WORDS:
        return FAREWELL
    if words & _THANKS_ANCHORS and words <= _THANKS_WORDS | _GREETING_WORDS:
        return THANKS
    if words & _GREETING_ANCHORS and words <= _GREETING_WORDS:
        return GREETING
    return None


_WANTS_HUMAN = re.compile(
    r"\b(atendente|humano|pessoa de verdade|alguem de verdade|abrir (um )?(chamado|ticket)|"
    r"(chamado|ticket)|falar com (alguem|um atendente|o suporte|uma pessoa))\b"
)


def asks_for_human(message: str) -> bool:
    """O cliente pede um atendente humano / abrir chamado ("quero falar com um atendente")."""
    return _WANTS_HUMAN.search(_normalize(message)) is not None


def time_of_day_greeting(message: str) -> str:
    """Espelha a saudacao do cliente ("Bom dia!") e cai em "Ola" nos demais casos."""
    text = _normalize(message)
    for phrase, reply in (
        ("bom dia", "Bom dia"),
        ("boa tarde", "Boa tarde"),
        ("boa noite", "Boa noite"),
    ):
        if phrase in text:
            return reply
    return "Olá"
