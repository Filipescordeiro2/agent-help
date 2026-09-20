"""Fontes de dados mock de cliente/dispositivo/transacao/chamado -- claramente identificadas
como mock (spec FR-018). Usadas apenas quando nao ha integracao real disponivel.

Cada funcao retorna dados deterministicos com o mesmo formato que uma integracao real usaria,
sempre com `"is_mock": True` explicito -- nunca apresentados como dado real.
"""

from __future__ import annotations

_MOCK_CUSTOMERS: dict[str, dict] = {
    "client_xpto": {
        "user_id": "client_xpto",
        "name": "Cliente Exemplo",
        "plan": "maquininha_pro",
        "is_mock": True,
    }
}

_MOCK_DEVICES: dict[str, list[dict]] = {
    "client_xpto": [
        {
            "device_id": "dev_001",
            "type": "maquininha",
            "model": "GetPay S1",
            "status": "offline",
            "last_seen_minutes_ago": 42,
            "is_mock": True,
        }
    ]
}

_MOCK_TICKETS: dict[str, list[dict]] = {"client_xpto": []}

_MOCK_INCIDENTS: list[dict] = [
    {
        "incident_id": "inc_001",
        "title": "Instabilidade de conexao Wi-Fi em maquininhas GetPay S1",
        "affected_models": ["GetPay S1"],
        "is_mock": True,
    }
]

_MOCK_TRANSACTIONS: dict[str, list[dict]] = {
    "client_xpto": [
        {"transaction_id": "txn_001", "status": "approved", "amount": 150.00, "is_mock": True}
    ]
}


def get_customer_profile(user_id: str) -> dict | None:
    return _MOCK_CUSTOMERS.get(user_id)


def get_customer_devices(user_id: str) -> list[dict]:
    return _MOCK_DEVICES.get(user_id, [])


def get_device_status(device_id: str) -> dict | None:
    for devices in _MOCK_DEVICES.values():
        for device in devices:
            if device["device_id"] == device_id:
                return device
    return None


def get_device_diagnostics(device_id: str) -> dict:
    device = get_device_status(device_id)
    return {
        "device_id": device_id,
        "diagnostics": "sinal_wifi_fraco" if device and device["status"] == "offline" else "ok",
        "is_mock": True,
    }


def get_customer_tickets(user_id: str) -> list[dict]:
    return _MOCK_TICKETS.get(user_id, [])


def get_transaction_status(user_id: str) -> list[dict]:
    return _MOCK_TRANSACTIONS.get(user_id, [])


def get_known_incidents() -> list[dict]:
    return list(_MOCK_INCIDENTS)


def create_ticket(user_id: str, *, subject: str, description: str) -> dict:
    """Cria um chamado -- idempotente por (user_id, subject): reaproveita um chamado aberto
    ja existente para o mesmo assunto em vez de abrir um duplicado (spec FR-038 -- evitar
    escalacoes duplicadas para o mesmo evento)."""
    for existing in _MOCK_TICKETS.get(user_id, []):
        if existing["subject"] == subject and existing["status"] == "open":
            return existing

    ticket = {
        "ticket_id": f"tkt_mock_{len(_MOCK_TICKETS.get(user_id, [])) + 1}",
        "user_id": user_id,
        "subject": subject,
        "description": description,
        "status": "open",
        "is_mock": True,
    }
    _MOCK_TICKETS.setdefault(user_id, []).append(ticket)
    return ticket
