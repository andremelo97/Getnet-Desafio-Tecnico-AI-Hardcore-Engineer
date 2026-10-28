"""The Router agent: classifies each message into a route and detects its language.

It has no tools and one structured output (`RouteDecision`). One model call per request,
and the route stays explicit in the graph state and in the trace.
"""

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]  # partially unknown overloads
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.agents.prompts import ROUTER_PROMPT
from app.agents.schemas import AgentGraph, RouteDecision
from app.guardrails.pii import pii_middleware


def build_router_agent(model: BaseChatModel) -> AgentGraph:
    return create_agent(
        model,
        tools=[],
        system_prompt=ROUTER_PROMPT,
        middleware=pii_middleware(),
        response_format=RouteDecision,
        name="router",
    )


async def route_message(agent: AgentGraph, message: str) -> RouteDecision:
    result = await agent.ainvoke({"messages": [HumanMessage(message)]})
    decision = result.get("structured_response")
    if not isinstance(decision, RouteDecision):
        msg = "The Router returned no RouteDecision."
        raise TypeError(msg)
    return decision
