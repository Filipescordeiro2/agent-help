"""No de seguranca do grafo -- primeiro no apos a entrada (Constitution Principio VIII).

Aplica o InputScanner sobre a mensagem do usuario; em caso de deteccao, curto-circuita o
fluxo marcando security_blocked=True, o que a montagem do grafo usa para desviar
diretamente para o no de validacao/resposta com status SECURITY_BLOCKED (spec FR-023).
"""

from __future__ import annotations

from app.agent.state import GraphState
from app.security.scanners import get_input_scanner


async def security_node(state: GraphState) -> GraphState:
    scanner = get_input_scanner()
    result = scanner.scan(state["user_message"].message)

    state["node_name"] = "security"
    state["security_blocked"] = not result.is_safe
    state["security_reason"] = result.reason_code
    return state
