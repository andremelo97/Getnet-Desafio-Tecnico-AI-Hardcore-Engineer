"""Split, embed and store steps of the RAG, plus the ingestion CLI.

Chroma sits behind `open_vectorstore`, so swapping it for a managed store is a one-file
change. Ingestion is idempotent: a URL's old chunks are deleted before its new chunks are
added (upsert alone left stale chunks in the dry run: 146 instead of 135).

Usage: `python -m app.knowledge.vectorstore [--rebuild] [--if-empty]`.
"""

import argparse
import asyncio
import hashlib
import sys

from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore, VectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.logging import configure_logging, log_event
from app.core.settings import Settings, get_settings
from app.knowledge.loader import fetch_documents, load_sources


def build_embeddings(settings: Settings) -> Embeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )


def open_vectorstore(settings: Settings, embeddings: Embeddings) -> Chroma:
    settings.vectorstore_dir.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=settings.collection_name,
        embedding_function=embeddings,
        persist_directory=str(settings.vectorstore_dir),
        client_settings=ChromaSettings(anonymized_telemetry=False),
        collection_configuration={"hnsw": {"space": "cosine"}},
    )


def count_chunks(store: VectorStore) -> int:
    if isinstance(store, Chroma):
        return len(store.get(include=[])["ids"])
    if isinstance(store, InMemoryVectorStore):
        return len(store.store)
    return 0


def chunk_id(url: str, chunk: int) -> str:
    return hashlib.sha256(f"{url}#{chunk}".encode()).hexdigest()[:32]


def split_documents(
    documents: list[Document], chunk_size: int, chunk_overlap: int
) -> list[Document]:
    """Split each page and prefix every chunk with `[market] title`, so a chunk from the
    middle of a page keeps its subject."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks: list[Document] = []
    for document in documents:
        prefix = f"[{document.metadata['market']}] {document.metadata['title']}"
        for index, text in enumerate(splitter.split_text(document.page_content)):
            chunks.append(
                Document(
                    page_content=f"{prefix}\n{text}",
                    metadata={**document.metadata, "chunk": index},
                )
            )
    return chunks


def store_chunks(store: VectorStore, chunks: list[Document]) -> None:
    """Replace every chunk of the given pages: delete by URL, then add with stable ids."""
    if not chunks:
        return
    urls = sorted({chunk.metadata["url"] for chunk in chunks})
    store.delete(where={"url": {"$in": urls}})
    ids = [chunk_id(chunk.metadata["url"], chunk.metadata["chunk"]) for chunk in chunks]
    store.add_documents(chunks, ids=ids)


async def ingest(settings: Settings, *, rebuild: bool = False, if_empty: bool = False) -> int:
    store = open_vectorstore(settings, build_embeddings(settings))
    if rebuild:
        store.reset_collection()
    existing = count_chunks(store)
    if if_empty and existing > 0:
        log_event("ingest.skipped", reason="store_not_empty", total_in_store=existing)
        return 0

    sources = load_sources(settings.sources_file)
    log_event("ingest.started", sources=len(sources), rebuild=rebuild)
    documents = await fetch_documents(sources)
    chunks = split_documents(documents, settings.chunk_size, settings.chunk_overlap)
    store_chunks(store, chunks)
    log_event(
        "ingest.completed",
        pages_loaded=len(documents),
        pages_failed=len(sources) - len(documents),
        chunks=len(chunks),
        total_in_store=count_chunks(store),
    )
    return 0 if documents else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest the Getnet knowledge base.")
    parser.add_argument("--rebuild", action="store_true", help="drop the collection first")
    parser.add_argument(
        "--if-empty", action="store_true", help="skip when the store already has chunks"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(ingest(settings, rebuild=args.rebuild, if_empty=args.if_empty))


if __name__ == "__main__":
    sys.exit(main())
