"""Live debugger: runs the real graph and prints each node, and each tool call inside the
agents, as it completes. Then prints the final response JSON.

Usage: `uv run python -m scripts.chat "message" --user cliente1988`
It streams `graph.astream(stream_mode=["updates", "values"], subgraphs=True)`: an empty
namespace is a graph node; a non-empty one is a step inside an agent.
"""

import argparse
import asyncio
import io
import json
import subprocess
import sys
import uuid
from collections.abc import Callable
from time import perf_counter
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langsmith import tracing_context  # pyright: ignore[reportUnknownVariableType]  # partially unknown kwargs

from app.agents.schemas import AgentGraph
from app.chat.schemas import ChatResponse
from app.chat.service import to_response
from app.container import build_services
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.guardrails.pii import anonymized_tracing_client

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
COLORS = {
    "router": "\033[95m",
    "knowledge": "\033[94m",
    "support": "\033[96m",
    "escalation": "\033[93m",
    "refusal": "\033[91m",
    "result": "\033[92m",
}
STRUCTURED_OUTPUTS = {"RouteDecision", "SpecialistAnswer", "HandoffPlan"}
PREVIEW = 160

type Write = Callable[[str], None]


def paint(text: str, color: str) -> str:
    return f"{COLORS.get(color, '')}{text}{RESET}"


def preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= PREVIEW else flat[: PREVIEW - 3] + "..."


def write_node(write: Write, node: str, update: dict[str, Any]) -> None:
    title = paint(f"{BOLD}[{node}]", node)
    if node == "router":
        write(f"{title} route={update.get('route')} language={update.get('language')}")
        write(f"         {DIM}reason: {update.get('route_reason')}{RESET}")
    elif node in ("knowledge", "support"):
        write(
            f"{title} tools={update.get('tools_used', [])} needs_human={update.get('needs_human')}"
        )
        for source in update.get("sources", []):
            write(f"         {DIM}source: {source.title} <{source.url}>{RESET}")
        if update.get("handoff_reason"):
            write(f"         handoff: {update['handoff_reason']}")
    elif node == "escalation":
        write(f"{title} ticket={update.get('ticket_id')}")
    else:
        write(f"{title} {preview(str(update.get('answer', '')))}")


def write_agent_step(write: Write, agent: str, messages: list[BaseMessage], seen: set[str]) -> None:
    for message in messages:
        key = message.id or str(id(message))
        if key in seen:
            continue
        seen.add(key)
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls:
                if tool_call["name"] not in STRUCTURED_OUTPUTS:
                    args = json.dumps(tool_call["args"], ensure_ascii=False)
                    write(paint(f"   {agent} -> {tool_call['name']}({args})", agent))
        elif isinstance(message, ToolMessage) and message.name not in STRUCTURED_OUTPUTS:
            write(f"   {DIM}{agent} <- {message.name}: {preview(str(message.content))}{RESET}")


async def stream_chat(
    graph: AgentGraph, message: str, user_id: str, write: Write = print
) -> ChatResponse:
    request_id = uuid.uuid4().hex
    config: RunnableConfig = {
        "run_name": "chat_cli",
        "metadata": {"request_id": request_id, "user_id": user_id},
    }
    started = perf_counter()
    final: dict[str, Any] = {}
    seen: set[str] = set()
    write(f"{BOLD}{user_id}{RESET}: {message}\n")

    async for event in graph.astream(
        {"user_id": user_id, "message": message},
        config=config,
        stream_mode=["updates", "values"],
        subgraphs=True,
    ):
        namespace, mode, data = cast("tuple[tuple[str, ...], str, Any]", event)
        if mode == "values":
            if not namespace:
                final = cast("dict[str, Any]", data)
            continue
        for node, update in cast("dict[str, dict[str, Any] | None]", data).items():
            if update is None:
                continue
            if namespace:
                agent = namespace[0].split(":")[0]
                write_agent_step(write, agent, update.get("messages", []), seen)
            else:
                write_node(write, node, update)

    response = to_response(final, request_id, int((perf_counter() - started) * 1000))
    write("\n" + paint(f"{BOLD}[response]", "result"))
    write(response.model_dump_json(indent=2))
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream one message through the graph.")
    parser.add_argument("message", help="the merchant's message")
    parser.add_argument("--user", default="cliente1988", help="the merchant's user_id")
    args = parser.parse_args(argv)

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform == "win32":
        subprocess.run("", shell=True, check=False)  # enables ANSI colours in the console

    settings = get_settings()
    configure_logging("WARNING")
    graph = build_services(settings).graph
    if graph is None:
        return 1
    with tracing_context(client=anonymized_tracing_client()):
        asyncio.run(stream_chat(graph, args.message, args.user))
    return 0


if __name__ == "__main__":
    sys.exit(main())
