"""Composition root: the only place that knows who uses what.

It builds the model, the stores, the tools, the four agents, the graph and the chat
service from settings. Modules never build their own dependencies, which is what lets the
tests swap the real model for a scripted fake without touching anything else.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.agents.escalation_agent import build_escalation_agent
from app.agents.llm import build_chat_model
from app.agents.router_agent import build_router_agent
from app.agents.schemas import AgentGraph
from app.agents.specialist import build_knowledge_agent, build_support_agent
from app.chat.service import ChatService
from app.core.settings import Settings
from app.customers.repository import CustomerRepository, today_in
from app.customers.tools import build_customer_tools
from app.guardrails.pii import anonymized_tracing_client
from app.knowledge.tools import build_search_knowledge_base, build_web_search
from app.knowledge.vectorstore import build_embeddings, count_chunks, open_vectorstore
from app.orchestration.graph import build_graph
from app.tickets.repository import TicketRepository


@dataclass(frozen=True)
class Services:
    chat: ChatService
    graph: AgentGraph | None
    knowledge_base_chunks: Callable[[], int]


def build_services(settings: Settings) -> Services:
    model = build_chat_model(settings)
    store = open_vectorstore(settings, build_embeddings(settings))
    search_knowledge_base = build_search_knowledge_base(
        store, settings.retrieval_k, settings.retrieval_min_score
    )
    web_search = build_web_search(settings.tavily_api_key, settings.web_search_max_results)
    customers = CustomerRepository(settings.customers_file, today=today_in(settings.timezone))

    graph = build_graph(
        router=build_router_agent(model),
        knowledge=build_knowledge_agent(
            model, [search_knowledge_base, web_search], settings.tool_call_limit
        ),
        support=build_support_agent(
            model,
            [*build_customer_tools(customers), search_knowledge_base],
            settings.tool_call_limit,
        ),
        escalation=build_escalation_agent(model),
        tickets=TicketRepository(),
    )
    return Services(
        chat=ChatService(graph, anonymized_tracing_client()),
        graph=graph,
        knowledge_base_chunks=lambda: count_chunks(store),
    )
