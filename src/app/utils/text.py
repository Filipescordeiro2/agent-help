"""Normalizacao do tema-alvo de uma proposta (spec FR-018 -- equivalencia de feedbacks).

Dois feedbacks sao equivalentes quando compartilham o mesmo par (`action_type`, tema normalizado).
A normalizacao ignora maiusculas/minusculas, acentos, pontuacao e espacos repetidos.
"""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_topic(text: str | None) -> str | None:
    if not text:
        return None
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = _NON_WORD.sub(" ", without_marks)
    collapsed = _SPACES.sub(" ", cleaned).strip()
    return collapsed or None
