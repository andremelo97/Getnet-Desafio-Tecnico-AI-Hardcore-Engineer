"""The orchestration: a LangGraph `StateGraph` that decides who runs, and in which order.

The typed `ChatState` is the message bus between agents: each node reads what it needs and
returns a partial update; `agents` and `tools_used` accumulate through `operator.add`.
A specialist that raises becomes a human handoff, so a failure never becomes a 500.

    START -> router -> knowledge | support | escalation (human) | refusal (unsupported)
    knowledge, support -> escalation if needs_human, else END
"""

import operator
from typing import Annotated, Any, Literal, Required, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.escalation_agent import escalate
from app.agents.messages import DEFAULT_LANGUAGE, REFUSAL, text_for
from app.agents.router_agent import route_message
from app.agents.schemas import AgentGraph, EscalationRequest, Route, SourceRef
from app.agents.specialist import run_specialist
from app.core.logging import log_event
from app.customers.schemas import CustomerContext
from app.tickets.repository import TicketRepository

type Update = dict[str, Any]
type Specialist = Literal["knowledge", "support"]

ROUTE_TO_NODE: dict[Route, str] = {
    "knowledge": "knowledge",
    "support": "support",
    "human": "escalation",
    "unsupported": "refusal",
}


class ChatState(TypedDict, total=False):
    user_id: Required[str]
    message: Required[str]
    route: Route
    route_reason: str
    language: str
    answer: str
    sources: list[SourceRef]
    tools_used: Annotated[list[str], operator.add]
    agents: Annotated[list[str], operator.add]
    needs_human: bool
    handoff_reason: str | None
    ticket_id: str | None


def build_graph(
    *,
    router: AgentGraph,
    knowledge: AgentGraph,
    support: AgentGraph,
    escalation: AgentGraph,
    tickets: TicketRepository,
) -> AgentGraph:
    """Wire the nodes. The agents are injected, so tests pass agents on fake models."""

    async def router_node(state: ChatState) -> Update:
        try:
            decision = await route_message(router, state["message"])
        except Exception as exc:
            log_event("router.failed", user_id=state["user_id"], error=type(exc).__name__)
            return {
                "route": "human",
                "route_reason": "The Router is unavailable, so the case goes to a person.",
                "language": DEFAULT_LANGUAGE,
                "agents": ["router"],
            }
        return {
            "route": decision.route,
            "route_reason": decision.reason,
            "language": decision.language,
            "agents": ["router"],
        }

    def specialist_node(name: Specialist, agent: AgentGraph):  # noqa: ANN202
        async def node(state: ChatState) -> Update:
            context = CustomerContext(user_id=state["user_id"]) if name == "support" else None
            try:
                result = await run_specialist(
                    agent,
                    message=state["message"],
                    language=state.get("language", DEFAULT_LANGUAGE),
                    context=context,
                )
            except Exception as exc:
                log_event("specialist.failed", agent=name, error=type(exc).__name__)
                return {
                    "agents": [name],
                    "needs_human": True,
                    "handoff_reason": f"The {name} agent failed ({type(exc).__name__}).",
                }
            return {
                "agents": [name],
                "answer": result.answer,
                "sources": result.sources,
                "tools_used": result.tools_used,
                "needs_human": result.needs_human,
                "handoff_reason": result.handoff_reason,
            }

        return node

    async def escalation_node(state: ChatState) -> Update:
        route = state.get("route", "human")
        origin = route if route in ("knowledge", "support") else "router"
        result = await escalate(
            escalation,
            tickets,
            EscalationRequest(
                user_id=state["user_id"],
                message=state["message"],
                language=state.get("language", DEFAULT_LANGUAGE),
                origin=origin,
                findings=state.get("answer"),
                handoff_reason=state.get("handoff_reason"),
            ),
        )
        return {
            "agents": ["escalation"],
            "answer": result.answer,
            "ticket_id": result.ticket_id,
            "needs_human": True,
        }

    def refusal_node(state: ChatState) -> Update:
        return {"answer": text_for(REFUSAL, state.get("language", DEFAULT_LANGUAGE))}

    def after_router(state: ChatState) -> str:
        return ROUTE_TO_NODE[state.get("route", "unsupported")]

    def after_specialist(state: ChatState) -> str:
        return "escalation" if state.get("needs_human") else END

    graph = StateGraph(ChatState)
    graph.add_node("router", router_node)
    graph.add_node("knowledge", specialist_node("knowledge", knowledge))
    graph.add_node("support", specialist_node("support", support))
    graph.add_node("escalation", escalation_node)
    graph.add_node("refusal", refusal_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router", after_router, ["knowledge", "support", "escalation", "refusal"]
    )
    graph.add_conditional_edges("knowledge", after_specialist, ["escalation", END])
    graph.add_conditional_edges("support", after_specialist, ["escalation", END])
    graph.add_edge("escalation", END)
    graph.add_edge("refusal", END)
    return graph.compile(name="getnet_support")
