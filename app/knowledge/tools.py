"""Retrieve step of the RAG (`search_knowledge_base`) and the general web search tool.

Both tools use `response_format="content_and_artifact"`: the model reads numbered snippets,
while the code receives the list of `{title, url}` it really returned. That artifact is
what source verification checks: a cited URL that no tool returned is dropped.
"""

from typing import Annotated, Any, cast

from langchain.tools import BaseTool, tool
from langchain_core.vectorstores import VectorStore
from langchain_tavily import TavilySearch
from pydantic import SecretStr

GETNET_DOMAINS = ["getnet.com.br", "getnet.net"]
NO_KB_RESULT = "No relevant passage was found in the Getnet knowledge base for this query."
NO_WEB_RESULT = "The web search returned no result for this query."

type SourceArtifact = list[dict[str, str]]


def build_search_knowledge_base(store: VectorStore, k: int, min_score: float) -> BaseTool:
    @tool(response_format="content_and_artifact")
    def search_knowledge_base(
        query: Annotated[str, "What to look for, written in Brazilian Portuguese."],
    ) -> tuple[str, SourceArtifact]:
        """Search Getnet's official website (products, fees, Pix, receivables advance,
        crediário, Payment Link, card machines and their error codes). Returns the most
        relevant passages with their title and URL."""
        results = [
            (document, score)
            for document, score in store.similarity_search_with_relevance_scores(query, k=k)
            if score >= min_score
        ]
        if not results:
            return NO_KB_RESULT, []

        snippets: list[str] = []
        sources: SourceArtifact = []
        for index, (document, score) in enumerate(results, start=1):
            title = str(document.metadata.get("title", ""))
            url = str(document.metadata.get("url", ""))
            snippets.append(
                f"[{index}] {title} ({url}) relevance={score:.2f}\n{document.page_content}"
            )
            if url and url not in {source["url"] for source in sources}:
                sources.append({"title": title, "url": url})
        return "\n\n".join(snippets), sources

    return search_knowledge_base


def build_web_search(api_key: SecretStr, max_results: int) -> BaseTool:
    tavily = TavilySearch(max_results=max_results, tavily_api_key=api_key)

    @tool("web_search", response_format="content_and_artifact")
    def web_search(
        query: Annotated[str, "The search query."],
        getnet_only: Annotated[bool, "True to search only Getnet's own websites."] = False,
    ) -> tuple[str, SourceArtifact]:
        """Search the web for general or current facts the knowledge base cannot hold:
        weather, exchange rates, news. Returns snippets with their title and URL."""
        payload: dict[str, Any] = {"query": query}
        if getnet_only:
            payload["include_domains"] = GETNET_DOMAINS
        raw: Any = tavily.invoke(payload)  # Tavily returns an untyped JSON payload
        if isinstance(raw, str):  # Tavily reports "no results" as a handled tool error
            return raw or NO_WEB_RESULT, []
        response = cast("dict[str, Any]", raw)
        if "error" in response:
            msg = f"web search failed: {type(response['error']).__name__}"
            raise RuntimeError(msg)

        results = cast("list[dict[str, Any]]", response.get("results") or [])
        if not results:
            return NO_WEB_RESULT, []
        snippets = [
            f"[{index}] {item.get('title', '')} ({item.get('url', '')})\n{item.get('content', '')}"
            for index, item in enumerate(results, start=1)
        ]
        sources = [
            {"title": str(item.get("title", "")), "url": str(item.get("url", ""))}
            for item in results
            if item.get("url")
        ]
        return "\n\n".join(snippets), sources

    return web_search
