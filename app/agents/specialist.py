"""The two specialists (Knowledge and Customer Support) and source verification.

Both are `create_agent` tool loops with PII masking, a tool-call limit and tool-error
handling. After a run, the code keeps only the cited URLs that a tool really returned
(its `content_and_artifact` artifact), so a source is never invented.
"""

from typing import Any

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]  # partially unknown overloads
from langchain.agents.middleware import (
    ToolCallLimitMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
)
from langchain.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from app.agents.prompts import KNOWLEDGE_PROMPT, SUPPORT_PROMPT
from app.agents.schemas import AgentGraph, Middleware, SourceRef, SpecialistAnswer, SpecialistResult
from app.core.logging import log_event
from app.customers.schemas import CustomerContext
from app.guardrails.pii import pii_middleware, redact_text


def tool_error_message(exc: Exception, request: ToolCallRequest) -> str:
    """Turn a tool exception into a message the model can react to (never re-raise)."""
    name = request.tool_call["name"]
    log_event("tool.failed", tool=name, error=type(exc).__name__)
    return (
        f"The tool `{name}` failed ({type(exc).__name__}). Do not call it again with the same "
        "arguments; answer with what you have, or hand the case off to a human."
    )


def specialist_middleware(tool_call_limit: int) -> list[Middleware]:
    return [
        *pii_middleware(),
        ToolCallLimitMiddleware(run_limit=tool_call_limit),
        ToolErrorMiddleware(tool_error_message),
    ]


def build_knowledge_agent(
    model: BaseChatModel, tools: list[BaseTool], tool_call_limit: int
) -> AgentGraph:
    return create_agent(
        model,
        tools=tools,
        system_prompt=KNOWLEDGE_PROMPT,
        middleware=specialist_middleware(tool_call_limit),
        response_format=SpecialistAnswer,
        name="knowledge",
    )


def build_support_agent(
    model: BaseChatModel, tools: list[BaseTool], tool_call_limit: int
) -> AgentGraph:
    return create_agent(
        model,
        tools=tools,
        system_prompt=SUPPORT_PROMPT,
        middleware=specialist_middleware(tool_call_limit),
        response_format=SpecialistAnswer,
        context_schema=CustomerContext,
        name="support",
    )


def specialist_input(message: str, language: str) -> dict[str, Any]:
    return {"messages": [HumanMessage(f"Merchant language: {language}\n\n{message}")]}


def returned_sources(messages: list[BaseMessage]) -> dict[str, str]:
    """Every `{url: title}` that a tool returned as its artifact during the run."""
    sources: dict[str, str] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.artifact, list):
            for item in message.artifact:  # pyright: ignore[reportUnknownVariableType]  # artifact is Any
                if isinstance(item, dict) and item.get("url"):  # pyright: ignore[reportUnknownMemberType]
                    url = str(item["url"])  # pyright: ignore[reportUnknownArgumentType]
                    sources.setdefault(url, str(item.get("title") or url))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return sources


def verify_sources(cited: list[str], returned: dict[str, str]) -> list[SourceRef]:
    """Keep only cited URLs that a tool really returned, in citation order, without repeats."""
    verified: list[SourceRef] = []
    for url in dict.fromkeys(cited):
        if url in returned:
            verified.append(SourceRef(title=returned[url], url=url))
        else:
            log_event("sources.unverified_dropped", url=url)
    return verified


def tools_called(messages: list[BaseMessage]) -> list[str]:
    """Names of the tools that ran, in order. The structured output is not a tool use: with
    the tool strategy (fake models) it shows up as a `SpecialistAnswer` tool message."""
    return [
        message.name
        for message in messages
        if isinstance(message, ToolMessage)
        and message.name
        and message.name != SpecialistAnswer.__name__
    ]


async def run_specialist(
    agent: AgentGraph,
    *,
    message: str,
    language: str,
    context: CustomerContext | None = None,
) -> SpecialistResult:
    result = await agent.ainvoke(specialist_input(message, language), context=context)
    answer = result.get("structured_response")
    if not isinstance(answer, SpecialistAnswer):
        msg = "The specialist returned no SpecialistAnswer."
        raise TypeError(msg)

    messages: list[BaseMessage] = result.get("messages", [])
    return SpecialistResult(
        answer=redact_text(answer.answer),
        sources=verify_sources(answer.source_urls, returned_sources(messages)),
        tools_used=tools_called(messages),
        needs_human=answer.needs_human,
        handoff_reason=answer.handoff_reason,
    )
