"""StateGraph do Feedback Agent (Constitution Principio I -- orquestracao so via LangGraph).

Nos explicitos: load_pending_feedback -> classify_feedback -> group_actionable ->
draft_proposals -> persist_proposals -> END, com curto-circuito para END quando nao ha feedback
pendente. Cada no e instrumentado (span, log, auditoria) como os do grafo principal.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.feedback_agent import nodes
from app.agent.feedback_agent.state import FeedbackAgentState
from app.agent.feedback_validation.agent import FeedbackValidationAgent
from app.observability.instrumentation import instrument_node


def build_feedback_agent_graph(
    db: AsyncIOMotorDatabase, agent: FeedbackValidationAgent | None = None
):
    agent = agent or FeedbackValidationAgent()
    graph = StateGraph(FeedbackAgentState)

    graph.add_node(
        "load_pending_feedback",
        instrument_node("load_pending_feedback", partial(nodes.load_pending_feedback, db=db)),
    )
    graph.add_node(
        "classify_feedback",
        instrument_node("classify_feedback", partial(nodes.classify_feedback, agent=agent)),
    )
    graph.add_node("group_actionable", instrument_node("group_actionable", nodes.group_actionable))
    graph.add_node(
        "draft_proposals",
        instrument_node("draft_proposals", partial(nodes.draft_proposals, db=db, agent=agent)),
    )
    graph.add_node(
        "persist_proposals",
        instrument_node("persist_proposals", partial(nodes.persist_proposals, db=db)),
    )

    graph.set_entry_point("load_pending_feedback")
    graph.add_conditional_edges(
        "load_pending_feedback",
        nodes.route_after_load,
        {"classify": "classify_feedback", "done": END},
    )
    graph.add_edge("classify_feedback", "group_actionable")
    graph.add_edge("group_actionable", "draft_proposals")
    graph.add_edge("draft_proposals", "persist_proposals")
    graph.add_edge("persist_proposals", END)
    return graph.compile()
