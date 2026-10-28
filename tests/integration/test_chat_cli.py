"""The live debugger's streaming, run on the offline test graph: each graph node and each
tool call inside an agent is printed, then the final response JSON.
"""

import asyncio
import json
import re

from scripts.chat import stream_chat
from tests.conftest import answer, build_harness, call, handoff, route

ANSI = re.compile(r"\033\[[0-9;]*m")


def run(message: str, **scripts: object) -> tuple[list[str], str]:
    harness = build_harness(**scripts)  # pyright: ignore[reportArgumentType]
    lines: list[str] = []
    response = asyncio.run(stream_chat(harness.graph, message, "cliente1988", lines.append))
    return [ANSI.sub("", line) for line in lines], response.model_dump_json()


def test_streams_nodes_and_the_tool_calls_inside_the_agent() -> None:
    lines, _ = run(
        "My card machine won't connect",
        router=[route("support")],
        support=[call("get_terminal_status"), answer("Your Get Smart is offline.")],
    )
    text = "\n".join(lines)

    assert "[router] route=support language=en" in text
    assert "support -> get_terminal_status({})" in text
    assert "support <- get_terminal_status:" in text
    assert "[support] tools=['get_terminal_status'] needs_human=False" in text
    assert text.index("[router]") < text.index("support -> get_terminal_status")


def test_prints_the_ticket_and_the_final_response_json() -> None:
    lines, response_json = run(
        "Quero falar com um atendente",
        router=[route("human", language="pt")],
        escalation=[handoff(priority="urgent")],
    )
    text = "\n".join(lines)

    ticket_id = json.loads(response_json)["ticket_id"]
    assert f"[escalation] ticket={ticket_id}" in text
    assert lines[-1].startswith("{") and json.loads(lines[-1])["escalated"] is True
