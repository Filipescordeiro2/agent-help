"""No de boas-vindas -- responde saudacao, agradecimento, despedida e "o que voce faz?".

Sem consulta a base, sites ou modelo: e uma resposta fixa e amigavel que apresenta o que o
assistente sabe fazer, com exemplos de perguntas (a mensagem pode ser trocada pela variavel
`WELCOME_MESSAGE`). Nao passa pelo grounding (nao ha conteudo factual a fundamentar).
"""

from __future__ import annotations

from app.agent import small_talk
from app.agent.state import GraphState
from app.config.settings import get_settings
from app.schemas.agent_response import AgentResponse, Status
from app.schemas.common import ResponseMetadata

WELCOME_AGENT = "welcome"

_CAPABILITIES = (
    "Posso ajudar com:\n"
    "- Taxas e prazos: taxa por transação (MDR), plano de recebimento reduzido, quando o "
    "dinheiro da venda cai.\n"
    "- Estornos e chargeback: como estornar uma venda na maquininha ou pelo aplicativo.\n"
    "- Pix: como habilitar, vender com Pix na maquininha e usar o Pix por biometria.\n"
    "- Maquininha: como vender, configurar o Wi-Fi, consultar vendas, erros comuns e "
    "manutenção.\n"
    "- Cadastro e conta: atualizar dados, finalizar o cadastro, domicílio bancário e "
    "redefinir a senha do aplicativo.\n"
    "- Link de pagamento e a documentação de API para desenvolvedores.\n"
    "\n"
    "Exemplos do que você pode me perguntar:\n"
    '- "Como faço para estornar uma venda na maquininha?"\n'
    '- "Como habilito o Pix na minha maquininha?"\n'
    '- "Qual a taxa por transação?"\n'
    '- "Minha maquininha não conecta, o que eu faço?"\n'
    '- "Esqueci a senha do aplicativo."\n'
    "\n"
    "É só escrever a sua dúvida com as suas palavras. Se eu não encontrar a resposta, te "
    "oriento a falar com a central de atendimento da Getnet."
)

_KINDS = (small_talk.GREETING, small_talk.THANKS, small_talk.FAREWELL, small_talk.HELP_REQUEST)


def build_welcome_message(kind: str, user_message: str) -> str:
    override = get_settings().welcome_message
    if override and kind in (small_talk.GREETING, small_talk.HELP_REQUEST):
        return override
    if kind == small_talk.THANKS:
        return (
            "Por nada! Fico feliz em ajudar. Se surgir outra dúvida, é só me chamar.\n\n"
            f"{_CAPABILITIES}"
        )
    if kind == small_talk.FAREWELL:
        return "Até mais! Foi um prazer ajudar. Quando precisar, é só chamar."
    saudacao = small_talk.time_of_day_greeting(user_message)
    return (
        f"{saudacao}! Eu sou o assistente virtual de atendimento da Getnet. "
        f"Como posso te ajudar hoje?\n\n{_CAPABILITIES}"
    )


async def welcome_node(state: GraphState) -> GraphState:
    state["node_name"] = "welcome"

    decision = state.get("routing_decision")
    kind = decision.reason_code if decision else small_talk.GREETING
    if kind not in _KINDS:
        kind = small_talk.GREETING
    message = build_welcome_message(kind, state["user_message"].message)

    state["final_response"] = AgentResponse(
        status=Status.OK,
        agent=WELCOME_AGENT,
        message=message,
        sources=[],
        metadata=ResponseMetadata(execution_id=state["execution_id"], confidence=1.0),
    )
    return state
