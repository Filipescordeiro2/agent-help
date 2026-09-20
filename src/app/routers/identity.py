"""Identidade do chamador nos cabecalhos (`X-User-Id`, `X-Session-Id`, ...) com fallback ao corpo.

O canal upstream (ja autenticado, ver `internal_trust_boundary_middleware`) informa quem e o usuario
e qual a sessao nos cabecalhos HTTP; o corpo continua aceito por compatibilidade. Regras:

- cabecalho e corpo presentes e DIFERENTES -> 400 `IDENTITY_MISMATCH` (nunca escolhe em silencio);
- so um deles presente -> vale esse;
- obrigatorio e ausente nos dois -> 400 `<CAMPO>_REQUIRED`.

Os valores sao apenas identificadores opacos (tamanho limitado, sem caracteres de controle) e nunca
sao tratados como autorizacao: quem garante a identidade e o canal interno autenticado.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header

USER_ID_HEADER = "X-User-Id"
SESSION_ID_HEADER = "X-Session-Id"
CHANNEL_HEADER = "X-Channel"
MESSAGE_ID_HEADER = "X-Message-Id"
EXECUTION_ID_HEADER = "X-Execution-Id"

_MAX_LENGTH = 256

_HEADER_OF = {
    "user_id": USER_ID_HEADER,
    "session_id": SESSION_ID_HEADER,
    "channel": CHANNEL_HEADER,
    "message_id": MESSAGE_ID_HEADER,
    "execution_id": EXECUTION_ID_HEADER,
}


class IdentityError(Exception):
    """Identidade ausente, invalida ou conflitante (vira 400 estruturado)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_LENGTH or any(ord(ch) < 32 for ch in cleaned):
        raise IdentityError("IDENTITY_INVALID", "Identificador invalido (tamanho ou caracteres).")
    return cleaned


@dataclass(frozen=True)
class IdentityHeaders:
    user_id: str | None = None
    session_id: str | None = None
    channel: str | None = None
    message_id: str | None = None
    execution_id: str | None = None

    def resolve(
        self, field: str, body_value: str | None = None, *, required: bool = True
    ) -> str | None:
        """Valor de `field` (user_id, session_id, ...) vindo do cabecalho ou do corpo."""
        header_value = getattr(self, field)
        body_clean = _clean(body_value)
        if header_value is not None and body_clean is not None and header_value != body_clean:
            raise IdentityError(
                "IDENTITY_MISMATCH",
                f"{field} do corpo difere do cabecalho {_HEADER_OF[field]}; envie o mesmo valor.",
            )
        value = header_value if header_value is not None else body_clean
        if value is None and required:
            raise IdentityError(
                f"{field.upper()}_REQUIRED",
                f"Informe o cabecalho {_HEADER_OF[field]} (o campo {field} no corpo e obsoleto).",
            )
        return value


def get_identity_headers(
    x_user_id: str | None = Header(default=None, alias=USER_ID_HEADER, description="Id do usuario"),
    x_session_id: str | None = Header(
        default=None, alias=SESSION_ID_HEADER, description="Id da sessao"
    ),
    x_channel: str | None = Header(default=None, alias=CHANNEL_HEADER, description="Canal"),
    x_message_id: str | None = Header(default=None, alias=MESSAGE_ID_HEADER),
    x_execution_id: str | None = Header(default=None, alias=EXECUTION_ID_HEADER),
) -> IdentityHeaders:
    return IdentityHeaders(
        user_id=_clean(x_user_id),
        session_id=_clean(x_session_id),
        channel=_clean(x_channel),
        message_id=_clean(x_message_id),
        execution_id=_clean(x_execution_id),
    )
