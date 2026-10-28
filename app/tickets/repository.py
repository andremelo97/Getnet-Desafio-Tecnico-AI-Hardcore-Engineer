"""Human handoff tickets, kept in memory for the challenge.

The ticket id is generated here, by code, so the Escalation agent can never invent a
protocol number. In production this is the boundary to a real ticketing system (and the
one place where a message queue would fit).
"""

import uuid
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "settlement_and_payments",
    "card_machine",
    "transactions_and_declines",
    "account_and_registration",
    "fraud_or_security",
    "complaint",
    "other",
]
Priority = Literal["urgent", "high", "medium", "low"]
Origin = Literal["router", "knowledge", "support"]


class Ticket(BaseModel):
    id: str
    user_id: str
    category: Category
    priority: Priority
    origin: Origin
    operator_summary: str
    customer_message: str
    language: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def new_ticket_id() -> str:
    return f"GN-{uuid.uuid4().hex[:8].upper()}"


class TicketRepository:
    def __init__(self) -> None:
        self._tickets: dict[str, Ticket] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        user_id: str,
        category: Category,
        priority: Priority,
        origin: Origin,
        operator_summary: str,
        customer_message: str,
        language: str,
    ) -> Ticket:
        with self._lock:
            ticket_id = new_ticket_id()
            while ticket_id in self._tickets:
                ticket_id = new_ticket_id()
            ticket = Ticket(
                id=ticket_id,
                user_id=user_id,
                category=category,
                priority=priority,
                origin=origin,
                operator_summary=operator_summary,
                customer_message=customer_message,
                language=language,
            )
            self._tickets[ticket_id] = ticket
            return ticket

    def get(self, ticket_id: str) -> Ticket | None:
        return self._tickets.get(ticket_id)

    def count(self) -> int:
        return len(self._tickets)
