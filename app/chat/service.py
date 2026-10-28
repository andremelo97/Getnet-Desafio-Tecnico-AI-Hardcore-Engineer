"""ChatService: one request in, one graph run, one response out.

It opens the LangSmith trace (through the anonymized client), measures the latency and
writes one `chat.completed` log line, all correlated by `request_id`. If the graph itself
cannot run, it raises `AgentUnavailableError`, which the API turns into a 503.
"""

import logging
from time import perf_counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langsmith import (
    Client,
    tracing_context,  # pyright: ignore[reportUnknownVariableType]  # partially unknown kwargs
)

from app.agents.messages import DEFAULT_LANGUAGE
from app.agents.schemas import AgentGraph
from app.chat.schemas import ChatRequest, ChatResponse
from app.core.errors import AgentUnavailableError
from app.core.logging import log_event


def to_response(state: dict[str, Any], request_id: str, latency_ms: int) -> ChatResponse:
    ticket_id = state.get("ticket_id")
    return ChatResponse(
        answer=state.get("answer", ""),
        route=state.get("route", "unsupported"),
        route_reason=state.get("route_reason", ""),
        agents=state.get("agents", []),
        tools_used=state.get("tools_used", []),
        sources=state.get("sources", []),
        escalated=ticket_id is not None,
        ticket_id=ticket_id,
        language=state.get("language", DEFAULT_LANGUAGE),
        request_id=request_id,
        latency_ms=latency_ms,
    )


class ChatService:
    def __init__(self, graph: AgentGraph, tracing_client: Client | None = None) -> None:
        self._graph = graph
        self._tracing_client = tracing_client

    async def chat(self, request: ChatRequest, request_id: str) -> ChatResponse:
        started = perf_counter()
        config: RunnableConfig = {
            "run_name": "chat_request",
            "metadata": {"request_id": request_id, "user_id": request.user_id},
        }
        try:
            with tracing_context(client=self._tracing_client):
                state = await self._graph.ainvoke(
                    {"user_id": request.user_id, "message": request.message}, config=config
                )
        except Exception as exc:
            log_event(
                "chat.failed",
                level=logging.ERROR,
                request_id=request_id,
                user_id=request.user_id,
                error=type(exc).__name__,
            )
            raise AgentUnavailableError from exc

        response = to_response(state, request_id, int((perf_counter() - started) * 1000))
        log_event(
            "chat.completed",
            request_id=request_id,
            user_id=request.user_id,
            route=response.route,
            agents=response.agents,
            tools_used=response.tools_used,
            escalated=response.escalated,
            ticket_id=response.ticket_id,
            latency_ms=response.latency_ms,
        )
        return response
