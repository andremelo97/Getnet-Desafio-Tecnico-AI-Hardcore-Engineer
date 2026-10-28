"""The Human Escalation agent: writes the handoff, while code opens the ticket.

The agent fills a `HandoffPlan` (category, priority, operator summary, customer message).
The ticket id comes from the repository, never from the model. If the model fails, a
deterministic fallback still opens the ticket, so the handoff works with the LLM down.
"""

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]  # partially unknown overloads
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.agents.messages import (
    ESCALATION_FALLBACK,
    FALLBACK_OPERATOR_SUMMARY,
    TICKET_LINE,
    text_for,
)
from app.agents.prompts import ESCALATION_PROMPT
from app.agents.schemas import AgentGraph, EscalationRequest, EscalationResult, HandoffPlan
from app.core.logging import log_event
from app.guardrails.pii import pii_middleware, redact_text
from app.tickets.repository import TicketRepository


def build_escalation_agent(model: BaseChatModel) -> AgentGraph:
    return create_agent(
        model,
        tools=[],
        system_prompt=ESCALATION_PROMPT,
        middleware=pii_middleware(),
        response_format=HandoffPlan,
        name="escalation",
    )


def render_request(request: EscalationRequest) -> str:
    return "\n".join(
        [
            f"Merchant user id: {request.user_id}",
            f"Merchant language: {request.language}",
            f"Escalated by: {request.origin}",
            f"Handoff reason: {request.handoff_reason or 'the merchant asked for a person'}",
            f"Specialist findings: {request.findings or 'none'}",
            "",
            "Merchant message:",
            request.message,
        ]
    )


def fallback_plan(request: EscalationRequest) -> HandoffPlan:
    return HandoffPlan(
        category="other",
        priority="high",
        operator_summary=FALLBACK_OPERATOR_SUMMARY.format(
            origin=request.origin,
            message=redact_text(request.message),
            reason=request.handoff_reason or "pedido de atendimento humano",
        ),
        customer_message=text_for(ESCALATION_FALLBACK, request.language),
    )


async def escalate(
    agent: AgentGraph, tickets: TicketRepository, request: EscalationRequest
) -> EscalationResult:
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(render_request(request))]})
        plan = result.get("structured_response")
        if not isinstance(plan, HandoffPlan):
            msg = "The Escalation agent returned no HandoffPlan."
            raise TypeError(msg)
    except Exception as exc:
        log_event("escalation.fallback", user_id=request.user_id, error=type(exc).__name__)
        plan = fallback_plan(request)

    ticket = tickets.create(
        user_id=request.user_id,
        category=plan.category,
        priority=plan.priority,
        origin=request.origin,
        operator_summary=redact_text(plan.operator_summary),
        customer_message=redact_text(plan.customer_message),
        language=request.language,
    )
    log_event(
        "ticket.opened",
        ticket_id=ticket.id,
        user_id=ticket.user_id,
        category=ticket.category,
        priority=ticket.priority,
        origin=ticket.origin,
    )
    ticket_line = text_for(TICKET_LINE, request.language).format(ticket_id=ticket.id)
    return EscalationResult(
        answer=f"{ticket.customer_message}\n\n{ticket_line}",
        ticket_id=ticket.id,
        priority=ticket.priority,
    )
