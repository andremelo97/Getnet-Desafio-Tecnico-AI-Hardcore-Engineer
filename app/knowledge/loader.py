"""Load step of the RAG: fetch the curated Getnet pages and strip the boilerplate.

Getnet's FAQ answers live inside `o-modal` elements, so only the classes matching
`popup|modal-cliente|cookie|gnt-nav` are removed: a broad `modal` filter deleted the
answers in the dry run. Each page becomes one `Document` with its url, title, market, topic.
"""

import asyncio
import json
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from pydantic import BaseModel, TypeAdapter

from app.core.logging import log_event

BOILERPLATE_TAGS = ["header", "footer", "nav", "script", "style", "noscript", "svg", "form"]
BOILERPLATE_CLASSES = re.compile(r"popup|modal-cliente|cookie|gnt-nav")
USER_AGENT = "getnet-challenge-ingestion/1.0 (knowledge base for a support assistant)"
CONCURRENCY = 5
TIMEOUT_SECONDS = 30


class Source(BaseModel):
    url: str
    market: str
    topic: str


def load_sources(path: Path) -> list[Source]:
    return TypeAdapter(list[Source]).validate_python(json.loads(path.read_text("utf-8")))


def clean_html(html: str) -> tuple[str, str]:
    """Return `(title, text)`: the title from `<title>`, the text from `<body>` only."""
    soup = BeautifulSoup(html, "html.parser")
    raw_title = soup.title.get_text(strip=True) if soup.title else ""
    title = raw_title.split(" - ")[0].strip()

    for tag in soup.find_all(BOILERPLATE_TAGS):
        tag.decompose()
    for element in soup.find_all(class_=BOILERPLATE_CLASSES):
        if not element.decomposed:
            element.decompose()

    body = soup.body or soup
    lines = (line.strip() for line in body.get_text("\n").splitlines())
    return title, "\n".join(line for line in lines if line)


def to_document(source: Source, html: str) -> Document:
    title, text = clean_html(html)
    return Document(
        page_content=text,
        metadata={
            "url": source.url,
            "title": title or source.topic,
            "market": source.market,
            "topic": source.topic,
        },
    )


async def fetch_documents(sources: list[Source]) -> list[Document]:
    """Fetch every source, 5 at a time; log one `ingest.page` line per page as it completes."""
    semaphore = asyncio.Semaphore(CONCURRENCY)
    total = len(sources)
    done = 0

    async def fetch(client: httpx.AsyncClient, source: Source) -> Document | None:
        nonlocal done
        async with semaphore:
            try:
                response = await client.get(source.url)
                status: int | str = response.status_code
                document = to_document(source, response.text) if status == 200 else None
            except httpx.HTTPError as exc:
                status, document = type(exc).__name__, None
        done += 1
        chars = len(document.page_content) if document else 0
        log_event(
            "ingest.page",
            page=f"{done}/{total}",
            url=source.url,
            status=status,
            chars=chars,
        )
        return document

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        results = await asyncio.gather(*(fetch(client, source) for source in sources))
    return [document for document in results if document is not None]
