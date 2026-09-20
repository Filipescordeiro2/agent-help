"""T100: teste de integracao para retomada de uma execucao interrompida a partir do ultimo
checkpoint (US4, Acceptance Scenario 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.repository.executions_repository import (
    Execution,
    ExecutionsRepository,
    ExecutionStatus,
)
from app.repository.messages_repository import (
    Message,
    MessageSender,
    MessagesRepository,
)
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision


async def test_resume_endpoint_recovers_from_interrupted_execution(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]

    # Simula uma execucao interrompida: registro "running" com a mensagem original ja
    # persistida, mas sem o processo ter chegado ao no de persistencia (ex.: o container
    # caiu no meio do processamento).
    await MessagesRepository(test_db).insert(
        Message(
            message_id="msg_interrupted",
            session_id=session_id,
            sender=MessageSender.USER,
            payload={"message": "pergunta interrompida", "user_id": "client_xpto"},
        )
    )
    await ExecutionsRepository(test_db).insert(
        Execution(
            execution_id="exec_interrupted",
            session_id=session_id,
            message_id="msg_interrupted",
            status=ExecutionStatus.RUNNING,
        )
    )

    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.UNKNOWN,
        target_agent=None,
        confidence=0.9,
        requires_clarification=False,
        reason_code="OUT_OF_SCOPE",
    )

    response = client.post("/api/v1/executions/exec_interrupted/resume")

    assert response.status_code == 200
    assert response.json()["status"] == Status.CLARIFICATION_REQUIRED.value

    execution_after = await ExecutionsRepository(test_db).get("exec_interrupted")
    assert execution_after.status == ExecutionStatus.COMPLETED


async def test_resume_of_already_completed_execution_is_idempotent(
    client: TestClient, fake_llm: dict, test_db
) -> None:
    """Chamar /resume em uma execucao ja concluida nao reprocessa nada -- apenas retorna o
    resultado ja persistido (idempotencia, FR-037)."""
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]

    stored_response = {
        "status": "OK",
        "agent": "knowledge_agent",
        "message": "ja resolvido anteriormente",
        "sources": [],
        "metadata": {"execution_id": "exec_done", "confidence": 0.9},
    }
    await ExecutionsRepository(test_db).insert(
        Execution(
            execution_id="exec_done",
            session_id=session_id,
            message_id="msg_done",
            status=ExecutionStatus.COMPLETED,
            final_response=stored_response,
        )
    )

    response = client.post("/api/v1/executions/exec_done/resume")

    assert response.status_code == 200
    assert response.json()["message"] == "ja resolvido anteriormente"


async def test_get_execution_returns_404_for_unknown_execution(client: TestClient) -> None:
    response = client.get("/api/v1/executions/does-not-exist")
    assert response.status_code == 404


async def test_list_session_executions_returns_stored_executions(
    client: TestClient, test_db
) -> None:
    session_id = client.post("/api/v1/sessions", json={"user_id": "client_xpto"}).json()[
        "session_id"
    ]
    await ExecutionsRepository(test_db).insert(
        Execution(execution_id="exec_x", session_id=session_id, message_id="msg_x")
    )

    response = client.get(f"/api/v1/sessions/{session_id}/executions")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["execution_id"] == "exec_x"
