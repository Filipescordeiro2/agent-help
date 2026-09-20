"""Mascaramento e truncamento de conteudo antes de grava-lo na trilha de auditoria.

A auditoria por sessao guarda o que o usuario disse e o que os agentes/ferramentas devolveram (para
explicar o "porque" de cada resposta). Isso pode conter dados pessoais/financeiros, entao TUDO passa
por aqui antes de ser gravado: numeros de cartao (validados por Luhn), CPF, CNPJ, e-mail, telefone,
chaves/tokens/senhas sao substituidos por marcadores; textos longos sao truncados.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

_MAX_ITEMS = 40

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "senha",
    "secret",
    "token",
    "x-api-key",
)

_KEY_LIKE = re.compile(r"(?i)\bsk-[a-z0-9_\-]{10,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{10,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|senha|authorization)\b(\s*[:=]\s*)[^\s,;]+"
)
_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CARD = re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)")
_PHONE = re.compile(
    r"(?<![A-Za-z0-9-])(?:\+?55[ .\-]?)?\(?\d{2}\)?[ .\-]?9?\d{4}[ .\-]?\d{4}(?![A-Za-z0-9-])"
)


def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def redact_text(text: str) -> str:
    """Substitui dados sensiveis por marcadores (`[CARTAO]`, `[CPF]`, `[EMAIL]`...)."""
    text = _KEY_LIKE.sub("[CHAVE]", text)
    text = _BEARER.sub("Bearer [TOKEN]", text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CNPJ.sub("[CNPJ]", text)
    text = _CPF.sub("[CPF]", text)
    text = _CARD.sub(lambda m: "[CARTAO]" if _luhn_ok(m.group(0)) else m.group(0), text)
    return _PHONE.sub("[TELEFONE]", text)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncado, {len(text) - max_chars} caracteres omitidos]"


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def sanitize(value: Any, *, max_chars: int, _depth: int = 0) -> Any:
    """Copia JSON-safe de `value` com strings mascaradas/truncadas e chaves sensiveis ocultas."""
    if _depth > 8:
        return "[profundidade maxima]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        return truncate(redact_text(value), max_chars)
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _sensitive_key(str(key))
                else sanitize(item, max_chars=max_chars, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set):
        items = list(value)
        cleaned = [
            sanitize(item, max_chars=max_chars, _depth=_depth + 1) for item in items[:_MAX_ITEMS]
        ]
        if len(items) > _MAX_ITEMS:
            cleaned.append(f"...[+{len(items) - _MAX_ITEMS} itens omitidos]")
        return cleaned
    if value is None or isinstance(value, bool | int | float):
        return value
    return truncate(redact_text(str(value)), max_chars)
