"""Mascaramento de dados sensiveis antes de gravar na trilha de auditoria."""

from __future__ import annotations

from pydantic import BaseModel

from app.observability.redaction import redact_text, sanitize, truncate


def test_valid_card_numbers_are_masked_but_lookalike_numbers_are_not() -> None:
    assert redact_text("meu cartao 4111 1111 1111 1111 falhou") == "meu cartao [CARTAO] falhou"
    assert redact_text("4111-1111-1111-1111") == "[CARTAO]"
    # 16 digitos que nao passam no Luhn nao sao cartao
    assert redact_text("protocolo 1234 5678 9012 3456") == "protocolo 1234 5678 9012 3456"


def test_brazilian_documents_email_and_phone_are_masked() -> None:
    assert redact_text("CPF 123.456.789-09") == "CPF [CPF]"
    assert redact_text("cpf 12345678909") == "cpf [CPF]"
    assert redact_text("CNPJ 12.345.678/0001-95") == "CNPJ [CNPJ]"
    assert redact_text("escreva para maria.silva@exemplo.com.br") == "escreva para [EMAIL]"
    assert redact_text("ligue (11) 91234-5678") == "ligue [TELEFONE]"
    assert redact_text("whats +55 11 91234-5678") == "whats [TELEFONE]"


def test_keys_tokens_and_passwords_are_masked() -> None:
    assert redact_text("chave sk-or-v1-abcdef1234567890") == "chave [CHAVE]"
    assert redact_text("Authorization: Bearer abcdef1234567890xyz").endswith("[TOKEN]")
    assert redact_text("minha senha: 123456") == "minha senha: [REDACTED]"
    assert redact_text("api_key=xyz123") == "api_key=[REDACTED]"


def test_ordinary_business_text_is_untouched() -> None:
    text = "A taxa no debito e de 1,99% e o prazo e D+30 (30 dias). Plano 4 de 12x."
    assert redact_text(text) == text


def test_truncate_marks_how_much_was_dropped() -> None:
    out = truncate("a" * 50, 10)
    assert out.startswith("a" * 10) and "40 caracteres omitidos" in out
    assert truncate("curto", 10) == "curto"


class _Model(BaseModel):
    message: str
    api_key: str


def test_sanitize_masks_recursively_hides_sensitive_keys_and_caps_lists() -> None:
    payload = {
        "message": "cpf 123.456.789-09",
        "headers": {"X-API-Key-LLM": "sk-or-abcdefghijk", "Authorization": "Bearer abcdefghijk123"},
        "nested": [{"password": "x", "ok": "1,99%"}],
        "items": list(range(100)),
        "model": _Model(message="mail a@b.com", api_key="segredo"),
        "n": 3,
        "flag": True,
        "nothing": None,
    }

    clean = sanitize(payload, max_chars=100)

    assert clean["message"] == "cpf [CPF]"
    assert clean["headers"] == {"X-API-Key-LLM": "[REDACTED]", "Authorization": "[REDACTED]"}
    assert clean["nested"] == [{"password": "[REDACTED]", "ok": "1,99%"}]
    assert len(clean["items"]) == 41 and "itens omitidos" in clean["items"][-1]
    assert clean["model"] == {"message": "mail [EMAIL]", "api_key": "[REDACTED]"}
    assert (clean["n"], clean["flag"], clean["nothing"]) == (3, True, None)
    assert "segredo" not in repr(clean) and "123.456.789" not in repr(clean)


def test_sanitize_truncates_long_strings() -> None:
    assert sanitize({"t": "x" * 500}, max_chars=50)["t"].startswith("x" * 50)


def test_phone_mask_does_not_eat_digits_inside_uuids_and_ids() -> None:
    from app.observability.redaction import redact_text

    uuid = "5fc34673-3c46-4c6c-8b3d-2fb0a1b7a21e"
    assert redact_text(f"documento {uuid}") == f"documento {uuid}"
    assert "[TELEFONE]" in redact_text("liga 11 99999-8888 agora")
    assert "[TELEFONE]" in redact_text("(11) 3333-4444")
