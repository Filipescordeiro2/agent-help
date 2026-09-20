"""Suporte guiado: entender -> passar a solucao ("deu certo?") -> chamado, tudo amarrado.

Cobre a maquina de estados do caso (`support_cases`), a abertura de chamado (`tickets`) e a trava
que impede abrir chamado sem todas as informacoes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent.customer_support.case_flow import (
    FollowUp,
    ProblemAssessment,
    classify_followup_text,
)
from app.agent.knowledge.agent import KnowledgeAgent, KnowledgeAnswerDraft
from app.repository.support_cases_repository import CaseStatus, SupportCase
from app.repository.tickets_repository import TicketReason
from app.schemas.agent_response import Status
from app.schemas.router import Intent, RouterDecision
from app.tools.web_tools import WebSearchOutput

STEPS = (
    "1. Clique na tecla de Funcao\n2. Insira o numero 38\n3. Selecione Inicializacao\n"
    "4. Faca uma venda teste."
)


@pytest.fixture
def support(client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch):
    """Sessao de suporte com o Router mandando tudo para o suporte e a base com o erro ADQ 4-83."""
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Erro ADQ 4-83",
            "source": "manual",
            "content": "Erro ADQ 4-83 na maquininha: erro de integracao. Solucao: funcao 38.",
        },
    )
    client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "Erro IDL 1-08",
            "source": "manual",
            "content": "Erro IDL 1-08 na maquininha: erro com o cartao. Solucao: funcao 60.",
        },
    )
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="SUPORTE",
    )
    fake_llm[KnowledgeAnswerDraft] = KnowledgeAnswerDraft(message=STEPS, grounded_in_sources=True)

    async def no_web(self, message: str) -> WebSearchOutput:
        return WebSearchOutput(results=[], errors=[])

    monkeypatch.setattr(KnowledgeAgent, "_search_web", no_web)
    session_id = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]

    def say(text: str) -> dict:
        return client.post(
            f"/api/v1/sessions/{session_id}/messages", json={"message": text, "user_id": "u1"}
        ).json()

    say.session_id = session_id  # type: ignore[attr-defined]
    say.client = client  # type: ignore[attr-defined]
    return say


def _vague(fake_llm: dict, question: str = "Qual e o problema ou a mensagem de erro?") -> None:
    fake_llm[ProblemAssessment] = ProblemAssessment(
        understood=False,
        category="other",
        problem_summary="",
        missing_information="qual o problema",
        clarifying_question=question,
        confidence=0.3,
    )


def _understood(fake_llm: dict, summary: str = "maquininha nao liga.") -> None:
    fake_llm[ProblemAssessment] = ProblemAssessment(
        understood=True,
        category="device_error",
        problem_summary=summary,
        confidence=0.9,
    )


async def _cases(test_db) -> list[SupportCase]:
    docs = await test_db["support_cases"].find({}).to_list(100)
    return [SupportCase.model_validate({k: v for k, v in d.items() if k != "_id"}) for d in docs]


# --- 1. entender: mensagem vaga -> pergunta, nunca chamado ----------------------------------------


async def test_vague_message_gets_one_question_and_no_ticket(support, fake_llm, test_db) -> None:
    _vague(fake_llm)

    body = support("preciso de ajuda com minha maquininha")

    assert body["status"] == Status.CLARIFICATION_REQUIRED.value
    assert body["message"] == "Qual e o problema ou a mensagem de erro?"
    assert body["metadata"]["ticket_id"] is None
    (case,) = await _cases(test_db)
    assert case.status == CaseStatus.COLLECTING_INFO and case.clarification_count == 1
    assert await test_db["tickets"].count_documents({}) == 0


async def test_error_code_answer_gets_the_steps_and_asks_if_it_worked(
    support, fake_llm, test_db
) -> None:
    _vague(fake_llm)
    support("preciso de ajuda com minha maquininha")

    body = support("Erro ADQ 4-83: erro de integracao com as bandeiras")

    assert body["status"] == Status.OK.value and body["agent"] == "customer_support_agent"
    assert "Tente fazer isso" in body["message"] and "numero 38" in body["message"]
    assert "Deu certo?" in body["message"]
    assert body["sources"], "a solucao vem das fontes (base/paginas), citadas"
    (case,) = await _cases(test_db)
    assert case.status == CaseStatus.AWAITING_CONFIRMATION
    assert case.error_code == "ADQ 4-83" and case.understood and case.searched
    assert len(case.attempts) == 1 and case.clarification_count == 1


# --- 2. confirmar: resolveu ou chamado -----------------------------------------------------------


async def test_customer_confirms_it_worked_closes_the_case_without_ticket(
    support, fake_llm, test_db
) -> None:
    support("estou com o Erro ADQ 4-83")

    body = support("deu certo, obrigado!")

    assert body["status"] == Status.OK.value and "deu certo" in body["message"].lower()
    (case,) = await _cases(test_db)
    assert case.status == CaseStatus.RESOLVED and case.ticket_id is None
    assert await test_db["tickets"].count_documents({}) == 0


async def test_not_resolved_opens_a_complete_ticket_and_returns_its_number(
    support, fake_llm, test_db
) -> None:
    _vague(fake_llm)
    support("preciso de ajuda com minha maquininha")
    support("Erro ADQ 4-83: erro de integracao com as bandeiras")

    body = support("nao deu certo")

    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert body["metadata"]["ticket_id"] == "TKT-000001"
    assert "TKT-000001" in body["message"] and "aguarde" in body["message"].lower()

    ticket = support.client.get("/api/v1/tickets/TKT-000001").json()
    assert ticket["status"] == "OPEN" and ticket["reason"] == "NOT_RESOLVED"
    assert ticket["customer"] == {
        "user_id": "u1",
        "session_id": support.session_id,
        "channel": None,
    }
    said = [line["content"] for line in ticket["conversation"] if line["role"] == "cliente"]
    assert "preciso de ajuda com minha maquininha" in said
    assert "Erro ADQ 4-83: erro de integracao com as bandeiras" in said
    assert "nao deu certo" in said
    analysis = ticket["analysis"]
    assert analysis["error_code"] == "ADQ 4-83" and analysis["clarifications_asked"] == 1
    assert len(analysis["solutions_tried"]) == 1
    assert {"cliente identificado", "problema entendido", "conversa do cliente registrada"} <= set(
        analysis["validated"]
    )
    assert "solucao ja indicada ao cliente" in analysis["validated"]
    assert "ADQ 4-83" in analysis["handoff_note"]
    (case,) = await _cases(test_db)
    assert case.status == CaseStatus.TICKET_OPENED and case.ticket_id == "TKT-000001"


async def test_customer_asking_for_a_human_after_the_solution_opens_the_ticket(
    support, fake_llm, test_db
) -> None:
    support("estou com o Erro ADQ 4-83")

    body = support("quero falar com um atendente")

    assert body["metadata"]["ticket_id"] == "TKT-000001"
    ticket = support.client.get("/api/v1/tickets/TKT-000001").json()
    assert ticket["reason"] == "CUSTOMER_REQUESTED_HUMAN"


async def test_new_error_code_after_the_solution_tries_again_then_ticket(
    support, fake_llm, test_db
) -> None:
    support("estou com o Erro ADQ 4-83")

    second = support("nao, agora aparece Erro ADQ 4-83 e tambem Erro IDL 1-08")

    assert second["status"] == Status.OK.value and "Tente fazer isso" in second["message"]
    (case,) = await _cases(test_db)
    assert len(case.attempts) == 2  # nova tentativa com a informacao nova
    third = support("ainda nao")  # limite de tentativas: agora vai para o atendimento
    assert third["metadata"]["ticket_id"] == "TKT-000001"


# --- 3. sem solucao: chamado, mas so depois de procurar -------------------------------------------


async def test_problem_understood_but_no_solution_found_opens_a_ticket(
    client: TestClient, fake_llm: dict, monkeypatch: pytest.MonkeyPatch, test_db
) -> None:
    fake_llm[RouterDecision] = RouterDecision(
        intent=Intent.CUSTOMER_SUPPORT,
        target_agent="customer_support_agent",
        confidence=0.9,
        requires_clarification=False,
        reason_code="SUPORTE",
    )
    _understood(fake_llm, "maquininha nao liga mesmo no carregador.")

    async def no_web(self, message: str) -> WebSearchOutput:
        return WebSearchOutput(results=[], errors=[])

    monkeypatch.setattr(KnowledgeAgent, "_search_web", no_web)
    sid = client.post("/api/v1/sessions", json={"user_id": "u1"}).json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"message": "minha maquininha nao liga mesmo no carregador", "user_id": "u1"},
    ).json()

    assert body["status"] == Status.ESCALATION_REQUIRED.value
    assert body["metadata"]["ticket_id"] == "TKT-000001"
    ticket = client.get("/api/v1/tickets/TKT-000001").json()
    assert ticket["reason"] == "NO_SOLUTION_FOUND"
    assert "busca de solucao realizada" in ticket["analysis"]["validated"]
    assert ticket["analysis"]["problem_summary"] == "maquininha nao liga mesmo no carregador."


# --- 4. amarracoes: nunca chamado sem informacao -------------------------------------------------


async def test_human_request_without_a_problem_asks_first_then_opens(
    support, fake_llm, test_db
) -> None:
    _vague(fake_llm)
    first = support("quero falar com um atendente")

    assert first["status"] == Status.CLARIFICATION_REQUIRED.value  # falta o problema
    assert await test_db["tickets"].count_documents({}) == 0

    _understood(fake_llm, "maquininha nao liga.")
    second = support("minha maquininha nao liga")

    assert second["metadata"]["ticket_id"] == "TKT-000001"
    ticket = support.client.get("/api/v1/tickets/TKT-000001").json()
    assert ticket["reason"] == "CUSTOMER_REQUESTED_HUMAN"
    assert ticket["analysis"]["solutions_tried"] == []  # pediu atendente: nao insistimos


async def test_clarifications_are_limited_and_no_ticket_is_opened_without_understanding(
    support, fake_llm, test_db
) -> None:
    _vague(fake_llm)

    for text in ("ajuda", "preciso de ajuda", "nao sei"):
        support(text)
    last = support("nada")

    assert last["status"] == Status.INSUFFICIENT_CONTEXT.value
    assert "central de atendimento" in last["message"]
    assert last["metadata"]["ticket_id"] is None
    assert await test_db["tickets"].count_documents({}) == 0
    assert (await _cases(test_db))[0].status == CaseStatus.NEEDS_CENTRAL


async def test_topic_change_while_collecting_supersedes_the_case(
    support, fake_llm, test_db
) -> None:
    _vague(fake_llm)
    support("preciso de ajuda")
    fake_llm[ProblemAssessment] = ProblemAssessment(
        understood=False,
        category="other",
        problem_summary="",
        is_new_topic=True,
        confidence=0.9,
    )

    support("qual a taxa da maquininha?")

    assert (await _cases(test_db))[0].status == CaseStatus.SUPERSEDED


async def test_greeting_during_a_case_does_not_lose_it(support, fake_llm, test_db) -> None:
    _vague(fake_llm)
    support("preciso de ajuda")

    body = support("ola")

    assert body["agent"] == "welcome"
    assert (await _cases(test_db))[0].status == CaseStatus.COLLECTING_INFO


# --- 5. a trava do servico (invariante) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "reason", "missing"),
    [
        ({"understood": False}, TicketReason.CUSTOMER_REQUESTED_HUMAN, "problema entendido"),
        ({"problem_summary": " "}, TicketReason.NO_SOLUTION_FOUND, "problema entendido"),
        ({"attempts": []}, TicketReason.NOT_RESOLVED, "solucao ja indicada ao cliente"),
        ({"searched": False}, TicketReason.NO_SOLUTION_FOUND, "busca de solucao realizada"),
        ({"user_id": ""}, TicketReason.NOT_RESOLVED, "cliente identificado"),
    ],
)
async def test_ticket_service_refuses_incomplete_tickets(
    test_db, changes: dict, reason: TicketReason, missing: str
) -> None:
    from app.repository.support_cases_repository import SolutionAttempt
    from app.repository.tickets_repository import ConversationLine
    from app.services.tickets import TicketNotReadyError, open_ticket

    complete = SupportCase(
        case_id="c1",
        session_id="s1",
        user_id="u1",
        understood=True,
        searched=True,
        problem_summary="maquininha nao liga.",
        attempts=[SolutionAttempt(number=1, solution="passos")],
    )
    case = complete.model_copy(update=changes)
    conversation = [ConversationLine(role="cliente", content="minha maquininha nao liga")]

    with pytest.raises(TicketNotReadyError) as error:
        await open_ticket(test_db, case=case, reason=reason, conversation=conversation)

    assert missing in error.value.missing
    assert await test_db["tickets"].count_documents({}) == 0


async def test_ticket_service_refuses_a_ticket_without_the_customer_conversation(test_db) -> None:
    from app.services.tickets import TicketNotReadyError, open_ticket

    case = SupportCase(
        case_id="c1",
        session_id="sem-mensagens",
        user_id="u1",
        understood=True,
        problem_summary="maquininha nao liga.",
    )

    with pytest.raises(TicketNotReadyError) as error:
        await open_ticket(test_db, case=case, reason=TicketReason.CUSTOMER_REQUESTED_HUMAN)

    assert "conversa do cliente registrada" in error.value.missing


async def test_open_ticket_is_idempotent_per_case_and_numbers_never_repeat(test_db) -> None:
    from app.repository.tickets_repository import ConversationLine
    from app.services.tickets import open_ticket

    conversation = [ConversationLine(role="cliente", content="minha maquininha nao liga")]

    def case(case_id: str) -> SupportCase:
        return SupportCase(
            case_id=case_id,
            session_id="s1",
            user_id="u1",
            understood=True,
            problem_summary="maquininha nao liga.",
        )

    a = await open_ticket(
        test_db,
        case=case("c1"),
        reason=TicketReason.CUSTOMER_REQUESTED_HUMAN,
        conversation=conversation,
    )
    again = await open_ticket(
        test_db,
        case=case("c1"),
        reason=TicketReason.CUSTOMER_REQUESTED_HUMAN,
        conversation=conversation,
    )
    b = await open_ticket(
        test_db,
        case=case("c2"),
        reason=TicketReason.CUSTOMER_REQUESTED_HUMAN,
        conversation=conversation,
    )

    assert a.ticket_id == again.ticket_id == "TKT-000001" and b.ticket_id == "TKT-000002"
    assert await test_db["tickets"].count_documents({}) == 2


# --- 6. API de chamados e auditoria --------------------------------------------------------------


async def test_tickets_api_lists_by_user_and_returns_404_for_unknown(support, fake_llm) -> None:
    support("estou com o Erro ADQ 4-83")
    support("nao resolveu")

    listed = support.client.get("/api/v1/tickets", params={"user_id": "u1"}).json()
    other = support.client.get("/api/v1/tickets", params={"user_id": "outro"}).json()

    assert [t["ticket_id"] for t in listed] == ["TKT-000001"] and other == []
    assert support.client.get("/api/v1/tickets/tkt-000001").status_code == 200  # case-insensitive
    assert support.client.get("/api/v1/tickets/TKT-999999").status_code == 404


async def test_audit_explains_the_support_case_steps(support, fake_llm) -> None:
    _vague(fake_llm)
    support("preciso de ajuda com minha maquininha")
    support("Erro ADQ 4-83")
    support("nao deu certo")

    audit = support.client.get(f"/api/v1/audit/sessions/{support.session_id}").json()
    lines = [line for turn in audit["turns"] for line in turn["explanation"]]

    assert any("PERGUNTOU ao cliente" in line for line in lines)
    assert any("aguardando a confirmacao" in line for line in lines)
    assert any("foi aberto o chamado TKT-000001" in line for line in lines)


# --- 7. interpretacao do "deu certo?" ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("deu certo", FollowUp.RESOLVED),
        ("Funcionou, obrigado!", FollowUp.RESOLVED),
        ("sim", FollowUp.RESOLVED),
        ("nao deu certo", FollowUp.NOT_RESOLVED),
        ("Não funcionou", FollowUp.NOT_RESOLVED),
        ("continua o mesmo erro", FollowUp.NOT_RESOLVED),
        ("nao", FollowUp.NOT_RESOLVED),
        ("quero falar com um atendente", FollowUp.WANTS_HUMAN),
        ("abre um chamado por favor", FollowUp.WANTS_HUMAN),
        ("nao, agora aparece o erro IDL 1-08", FollowUp.NEW_INFORMATION),
        ("nao sei", None),
        ("a tela ficou branca depois disso", None),
    ],
)
def test_followup_text_classification(text: str, expected) -> None:
    case = SupportCase(case_id="c", session_id="s", user_id="u", error_code="ADQ 4-83")
    assert classify_followup_text(text, case) == expected
