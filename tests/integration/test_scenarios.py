"""The challenge's 10 Example Test Scenarios, offline, one pytest id per scenario (S1..S10).

The fake model scripts the decisions a good model makes for each message (the route, the
tool calls, the cited sources). What these tests prove is everything around the model: the
graph routes to the right specialist, the real tools run on the test fixtures, the sources
are verified and the response honours the contract. The model's own judgment on the same
messages is measured by the evals (`evals/run_evals.py`), with the real model.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.conftest import KB_PAGES, WEB_URL, answer, build_harness, call, route

KB = {title: url for url, (title, _) in KB_PAGES.items()}


@dataclass(frozen=True)
class Scenario:
    id: str
    message: str
    route: str
    script: list[AIMessage]
    tools: list[str]
    sources: list[str] = field(default_factory=list[str])

    @property
    def agent(self) -> str:
        return self.route


def kb_answer(query: str, *titles: str) -> list[AIMessage]:
    urls = [KB[title] for title in titles]
    return [call("search_knowledge_base", query=query), answer("Scripted answer.", urls)]


def web_answer(query: str) -> list[AIMessage]:
    return [call("web_search", query=query), answer("Scripted answer.", [WEB_URL])]


SCENARIOS = [
    Scenario(
        "S1",
        "What's the difference between the Get Clássica and the Get Smart?",
        "knowledge",
        kb_answer("diferença entre Get Clássica e Get Smart", "Get Clássica", "Get Smart"),
        ["search_knowledge_base"],
        [KB["Get Clássica"], KB["Get Smart"]],
    ),
    Scenario(
        "S2",
        "What's the weather forecast in Porto Alegre tomorrow?",
        "knowledge",
        web_answer("previsão do tempo Porto Alegre amanhã"),
        ["web_search"],
        [WEB_URL],
    ),
    Scenario(
        "S3",
        "When will the money from yesterday's sales be deposited?",
        "support",
        [call("get_sales_and_settlements", days_ago=1), answer("Scripted answer.")],
        ["get_sales_and_settlements"],
    ),
    Scenario(
        "S4",
        "Do I need a bank account to receive my sales via Pix?",
        "knowledge",
        kb_answer("preciso de conta para receber vendas via Pix", "Dúvidas Pix"),
        ["search_knowledge_base"],
        [KB["Dúvidas Pix"]],
    ),
    Scenario(
        "S5",
        "My card machine won't connect to the internet, what should I do?",
        "support",
        [
            call("get_terminal_status"),
            call("search_knowledge_base", query="erro ADQ 1-18 sinal fraco Wi-Fi"),
            answer("Scripted answer.", [KB["Erro ADQ 1-18"]]),
        ],
        ["get_terminal_status", "search_knowledge_base"],
        [KB["Erro ADQ 1-18"]],
    ),
    Scenario(
        "S6",
        "How does receivables advance (antecipação) work with Getnet?",
        "knowledge",
        kb_answer("como funciona a antecipação de recebíveis", "Antecipação de vendas"),
        ["search_knowledge_base"],
        [KB["Antecipação de vendas"]],
    ),
    Scenario(
        "S7",
        "What's the euro exchange rate today?",
        "knowledge",
        web_answer("cotação do euro em reais hoje"),
        ["web_search"],
        [WEB_URL],
    ),
    Scenario(
        "S8",
        "My card machine is showing a transaction decline error.",
        "support",
        [call("get_recent_transactions", status="declined", limit=10), answer("Scripted.")],
        ["get_recent_transactions"],
    ),
    Scenario(
        "S9",
        "How many installments can I split a sale into with the crediário?",
        "knowledge",
        kb_answer("crediário quantidade de parcelas", "Crediário"),
        ["search_knowledge_base"],
        [KB["Crediário"]],
    ),
    Scenario(
        "S10",
        "Can I sell through WhatsApp using the Payment Link?",
        "knowledge",
        kb_answer("vender pelo WhatsApp com Link de Pagamento", "Link de Pagamento"),
        ["search_knowledge_base"],
        [KB["Link de Pagamento"]],
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_challenge_scenario(scenario: Scenario) -> None:
    harness = build_harness(
        router=[route(scenario.route)],
        knowledge=scenario.script if scenario.agent == "knowledge" else (),
        support=scenario.script if scenario.agent == "support" else (),
    )

    response = harness.chat(scenario.message)

    assert response.route == scenario.route
    assert response.agents == ["router", scenario.agent]
    assert response.tools_used == scenario.tools
    assert [source.url for source in response.sources] == scenario.sources
    assert not response.escalated and response.ticket_id is None
    assert response.answer and response.language == "en"
    assert response.request_id == "test-request"
    other = harness.support if scenario.agent == "knowledge" else harness.knowledge
    assert other.calls == 0 and harness.escalation.calls == 0


def _tool_results(messages: list[ToolMessage], name: str) -> list[dict[str, Any]]:
    return [json.loads(str(m.content)) for m in messages if m.name == name]


def test_s3_the_model_receives_totals_per_deposit_date_not_raw_sums() -> None:
    harness = build_harness(router=[route("support")], support=SCENARIOS[2].script)

    harness.chat(SCENARIOS[2].message)

    tool_messages = [m for m in harness.support.received[-1] if isinstance(m, ToolMessage)]
    (result,) = _tool_results(tool_messages, "get_sales_and_settlements")
    assert [d["total_amount"] for d in result["deposits_by_date"]] == [
        47.0,
        137.65,
        249.9,
        120.0,
        120.0,
    ]


def test_s8_declines_are_explained_from_the_declined_transactions() -> None:
    harness = build_harness(router=[route("support")], support=SCENARIOS[7].script)

    harness.chat(SCENARIOS[7].message)

    tool_messages = [m for m in harness.support.received[-1] if isinstance(m, ToolMessage)]
    (result,) = _tool_results(tool_messages, "get_recent_transactions")
    codes = [(tx["response_code"], tx["response_meaning"]) for tx in result["transactions"]]
    assert codes == [
        ("55", "Wrong PIN entered."),
        ("51", "Insufficient funds or credit limit."),
        ("54", "Expired card."),
    ]


def test_s5_the_machine_status_points_to_the_error_page() -> None:
    harness = build_harness(router=[route("support")], support=SCENARIOS[4].script)

    harness.chat(SCENARIOS[4].message)

    seen = harness.support.seen_text()
    assert "ADQ 1-18 (sinal fraco)" in seen  # from get_terminal_status
    assert "aproxime a maquininha do roteador" in seen  # from the knowledge base
