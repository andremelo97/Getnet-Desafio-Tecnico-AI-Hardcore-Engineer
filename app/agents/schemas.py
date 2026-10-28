"""Contracts between the agents and the graph: what each agent must return.

The Router, specialists and Escalation agent use these models as `response_format`, so
their output is validated structured data, never free text the graph has to parse.
"""

from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.tickets.repository import Category, Origin, Priority

Route = Literal["knowledge", "support", "human", "unsupported"]

type Middleware = AgentMiddleware[Any, Any, Any]
type AgentGraph = CompiledStateGraph[Any, Any, Any, Any]


class RouteDecision(BaseModel):
    """The Router's decision for one merchant message."""

    route: Route = Field(description="Which path handles the message.")
    reason: str = Field(description="One short sentence, in English, explaining the route.")
    language: str = Field(
        description="ISO 639-1 code of the language the merchant wrote in, e.g. pt, en, es."
    )


class SourceRef(BaseModel):
    title: str
    url: str


class SpecialistAnswer(BaseModel):
    """What the Knowledge and Customer Support agents return."""

    answer: str = Field(
        description="The answer to the merchant, in the merchant's language, with no URLs."
    )
    source_urls: list[str] = Field(
        default_factory=list[str],
        description="URLs of the tool results actually used, copied exactly from the tools.",
    )
    needs_human: bool = Field(
        default=False, description="True only when a human operator must take over the case."
    )
    handoff_reason: str | None = Field(
        default=None, description="Why a human must take over, when needs_human is true."
    )


class SpecialistResult(BaseModel):
    """What a specialist node hands to the graph, after source verification."""

    answer: str
    sources: list[SourceRef] = Field(default_factory=list[SourceRef])
    tools_used: list[str] = Field(default_factory=list[str])
    needs_human: bool = False
    handoff_reason: str | None = None


class HandoffPlan(BaseModel):
    """What the Human Escalation agent writes for the operator and the merchant."""

    category: Category = Field(description="The kind of problem.")
    priority: Priority = Field(
        description=(
            "urgent: fraud, cloned card, security, unrecognized charge, cannot sell at all; "
            "high: money not received, repeated declines, no account found; "
            "medium: device or account issue with a workaround; low: information request."
        )
    )
    operator_summary: str = Field(
        description="2 to 4 sentences in Brazilian Portuguese for the human operator."
    )
    customer_message: str = Field(
        description=(
            "1 to 3 sentences to the merchant, in the merchant's language. Never a ticket "
            "number, protocol or deadline: the system appends the ticket number."
        )
    )


class EscalationRequest(BaseModel):
    user_id: str
    message: str
    language: str
    origin: Origin
    findings: str | None = None
    handoff_reason: str | None = None


class EscalationResult(BaseModel):
    answer: str
    ticket_id: str
    priority: Priority
