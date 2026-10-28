"""Shared fixtures: fixed dates, the real merchant data, tool runtimes and the test graph.

The test graph is the real one: real `build_graph`, real agents with their middleware, real
tools on test fixtures. Only the model is fake: a scripted model plays the decisions a real
model would make (which route, which tool, which answer) and records every prompt it gets.
So these tests prove the wiring around the model; the evals measure the model's judgment.
"""

import asyncio
import itertools
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.vectorstores import InMemoryVectorStore
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from app.agents.escalation_agent import build_escalation_agent
from app.agents.router_agent import build_router_agent
from app.agents.schemas import AgentGraph
from app.agents.specialist import build_knowledge_agent, build_support_agent
from app.chat.schemas import ChatRequest, ChatResponse
from app.chat.service import ChatService
from app.core.settings import PROJECT_ROOT, Settings
from app.customers.repository import CustomerRepository
from app.customers.schemas import CustomerContext
from app.customers.tools import build_customer_tools
from app.knowledge.tools import build_search_knowledge_base
from app.orchestration.graph import build_graph
from app.tickets.repository import TicketRepository

TODAY = date(2026, 10, 28)  # a Wednesday: yesterday is Tuesday, +61 days is a Sunday
CUSTOMERS_FILE = PROJECT_ROOT / "data" / "customers.json"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """No test may send a trace, whatever the developer's shell exports."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)


# --- Settings and stores -------------------------------------------------------------------


class OfflineSettings(Settings):
    """Settings that never read the project's `.env`."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings for tests: fake keys and temporary paths."""
    return OfflineSettings(
        openai_api_key=SecretStr("test"),
        tavily_api_key=SecretStr("test"),
        vectorstore_dir=tmp_path / "vectorstore",
        **overrides,
    )


class RelevanceInMemoryVectorStore(InMemoryVectorStore):
    """`InMemoryVectorStore` with a relevance function: cosine (-1..1) mapped to 0..1."""

    def _select_relevance_score_fn(self) -> Callable[[float], float]:
        return lambda score: (score + 1) / 2


def fake_embeddings() -> DeterministicFakeEmbedding:
    return DeterministicFakeEmbedding(size=32)


# Test fixtures in the shape of the real knowledge base (not real Getnet content).
KB_PAGES = {
    "https://site.getnet.com.br/maquininha/get-classica/": (
        "Get Clássica",
        "A Get Clássica imprime o comprovante e conecta por chip.",
    ),
    "https://site.getnet.com.br/maquininha/get-smart/": (
        "Get Smart",
        "A Get Smart tem tela touch, sistema Android e conecta por Wi-Fi e chip.",
    ),
    "https://site.getnet.com.br/duvidas/pix-mais-respostas/": (
        "Dúvidas Pix",
        "Para receber vendas via Pix na maquininha é preciso ter uma conta.",
    ),
    "https://site.getnet.com.br/duvidas/antecipacao-de-vendas/": (
        "Antecipação de vendas",
        "A antecipação adianta o recebimento das vendas no crédito mediante uma taxa.",
    ),
    "https://site.getnet.com.br/crediario/": (
        "Crediário",
        "No crediário o cliente parcela a compra e o lojista recebe à vista.",
    ),
    "https://site.getnet.com.br/link-de-pagamento/": (
        "Link de Pagamento",
        "O Link de Pagamento pode ser enviado por WhatsApp, e-mail ou redes sociais.",
    ),
    "https://site.getnet.com.br/get-ajuda-erros-maquininha/erro-adq-1-18/": (
        "Erro ADQ 1-18",
        "O erro ADQ 1-18 indica sinal fraco: aproxime a maquininha do roteador Wi-Fi.",
    ),
}
WEB_URL = "https://www.example.com/search-result"


def knowledge_store() -> RelevanceInMemoryVectorStore:
    store = RelevanceInMemoryVectorStore(fake_embeddings())
    store.add_documents(
        [
            Document(
                page_content=f"[Brasil] {title}\n{text}",
                metadata={"url": url, "title": title, "market": "Brasil", "chunk": 0},
            )
            for url, (title, text) in KB_PAGES.items()
        ]
    )
    return store


@tool("web_search", response_format="content_and_artifact")
def fake_web_search(query: str, getnet_only: bool = False) -> tuple[str, list[dict[str, str]]]:
    """Search the web for general or current facts (offline stand-in, same name and
    contract as the real Tavily tool)."""
    scope = "getnet" if getnet_only else "web"
    return (
        f"[1] Result ({WEB_URL})\nA {scope} result for: {query}",
        [{"title": "Result", "url": WEB_URL}],
    )


# --- Tool runtimes for direct tool calls -------------------------------------------------


def make_runtime(user_id: str) -> ToolRuntime[CustomerContext, dict[str, object]]:
    """The runtime a ToolNode would inject, built by hand for direct tool calls."""
    return ToolRuntime[CustomerContext, dict[str, object]](
        state={},
        context=CustomerContext(user_id=user_id),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="test-call",
        store=None,
    )


def call_tool(tool: Any, user_id: str, **args: Any) -> str:
    result = tool.invoke({**args, "runtime": make_runtime(user_id)})
    assert isinstance(result, str)
    return result


@pytest.fixture
def customer_repository() -> CustomerRepository:
    return CustomerRepository(CUSTOMERS_FILE, today=lambda: TODAY)


# --- The scripted fake model ---------------------------------------------------------------


class ScriptedChatModel(GenericFakeChatModel):
    """Plays a script of model decisions and records every prompt it receives.

    `GenericFakeChatModel` has no `bind_tools`; tools are part of the script instead.
    """

    received: list[list[BaseMessage]] = Field(default_factory=list[list[BaseMessage]])
    fail_with: str | None = None

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        try:
            return super()._generate(messages, stop, run_manager, **kwargs)
        except StopIteration:
            msg = "The scripted model ran out of steps."
            raise RuntimeError(msg) from None

    @property
    def calls(self) -> int:
        return len(self.received)

    def seen_text(self) -> str:
        """Everything this model was shown, as one string."""
        return "\n".join(str(m.content) for prompt in self.received for m in prompt)


_call_ids = itertools.count()


def call(name: str, **args: Any) -> AIMessage:
    """A model step that calls one tool (or returns structured output, named after its schema)."""
    return AIMessage(
        content="",
        tool_calls=[ToolCall(name=name, args=args, id=f"call_{next(_call_ids)}", type="tool_call")],
    )


def route(route: str, language: str = "en") -> AIMessage:
    return call("RouteDecision", route=route, reason=f"scripted: {route}", language=language)


def answer(
    text: str,
    source_urls: Sequence[str] = (),
    *,
    needs_human: bool = False,
    handoff_reason: str | None = None,
) -> AIMessage:
    return call(
        "SpecialistAnswer",
        answer=text,
        source_urls=list(source_urls),
        needs_human=needs_human,
        handoff_reason=handoff_reason,
    )


def handoff(category: str = "other", priority: str = "medium") -> AIMessage:
    return call(
        "HandoffPlan",
        category=category,
        priority=priority,
        operator_summary="Lojista pediu atendimento humano.",
        customer_message="A Getnet specialist will take over your case.",
    )


# --- The test graph ------------------------------------------------------------------------


@dataclass
class Harness:
    graph: AgentGraph
    router: ScriptedChatModel
    knowledge: ScriptedChatModel
    support: ScriptedChatModel
    escalation: ScriptedChatModel
    tickets: TicketRepository

    def chat(self, message: str, user_id: str = "cliente1988") -> ChatResponse:
        request = ChatRequest(message=message, user_id=user_id)
        return asyncio.run(ChatService(self.graph).chat(request, request_id="test-request"))

    def models(self) -> Iterator[ScriptedChatModel]:
        yield from (self.router, self.knowledge, self.support, self.escalation)


def build_harness(
    *,
    router: Sequence[AIMessage] = (),
    knowledge: Sequence[AIMessage] = (),
    support: Sequence[AIMessage] = (),
    escalation: Sequence[AIMessage] = (),
    failing: frozenset[str] = frozenset(),
) -> Harness:
    """The real graph and agents, with one scripted model per agent."""

    def model(name: str, script: Sequence[AIMessage]) -> ScriptedChatModel:
        return ScriptedChatModel(
            messages=iter(script), fail_with="LLM down" if name in failing else None
        )

    models = {
        "router": model("router", router),
        "knowledge": model("knowledge", knowledge),
        "support": model("support", support),
        "escalation": model("escalation", escalation),
    }
    search = build_search_knowledge_base(knowledge_store(), k=len(KB_PAGES), min_score=0.0)
    customers = CustomerRepository(CUSTOMERS_FILE, today=lambda: TODAY)
    tickets = TicketRepository()
    graph = build_graph(
        router=build_router_agent(models["router"]),
        knowledge=build_knowledge_agent(models["knowledge"], [search, fake_web_search], 6),
        support=build_support_agent(
            models["support"], [*build_customer_tools(customers), search], 6
        ),
        escalation=build_escalation_agent(models["escalation"]),
        tickets=tickets,
    )
    return Harness(graph=graph, tickets=tickets, **models)
