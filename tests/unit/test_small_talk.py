"""Conversa social: so mensagens que sao apenas saudacao/agradecimento/despedida/ajuda."""

import pytest

from app.agent import small_talk
from app.agent.nodes.welcome_node import build_welcome_message


@pytest.mark.parametrize(
    "message",
    [
        "Ola",
        "oi",
        "Oiii!!",
        "Olá, tudo bem?",
        "Bom dia",
        "boa tarde!",
        "e ai",
        "Hello",
        "oi tudo bem com você",
    ],
)
def test_greetings(message: str) -> None:
    assert small_talk.classify_small_talk(message) == small_talk.GREETING


@pytest.mark.parametrize("message", ["obrigado", "Muito obrigada!", "valeu", "ok, obrigado"])
def test_thanks(message: str) -> None:
    assert small_talk.classify_small_talk(message) == small_talk.THANKS


@pytest.mark.parametrize("message", ["tchau", "Até mais", "obrigado, tchau", "flw"])
def test_farewell(message: str) -> None:
    assert small_talk.classify_small_talk(message) == small_talk.FAREWELL


@pytest.mark.parametrize(
    "message",
    ["ajuda", "Menu", "O que você pode fazer?", "Como você pode me ajudar?", "quem é você"],
)
def test_help_request(message: str) -> None:
    assert small_talk.classify_small_talk(message) == small_talk.HELP_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "Ola, como estorno uma venda?",
        "Oi, minha maquininha nao conecta",
        "Qual a taxa?",
        "Preciso de ajuda",
        "obrigado, mas como habilito o Pix?",
        "",
        "   ",
    ],
)
def test_messages_with_a_real_question_are_not_small_talk(message: str) -> None:
    assert small_talk.classify_small_talk(message) is None


def test_welcome_mirrors_the_time_of_day_and_lists_examples() -> None:
    message = build_welcome_message(small_talk.GREETING, "Bom dia!")
    assert message.startswith("Bom dia!") and "Exemplos" in message and "estornar" in message
    assert build_welcome_message(small_talk.GREETING, "oi").startswith("Olá!")
    assert "Até mais" in build_welcome_message(small_talk.FAREWELL, "tchau")
