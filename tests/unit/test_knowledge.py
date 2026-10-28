"""RAG building blocks: HTML cleaning, chunking, chunk ids, idempotent storage, retrieval.

The cleaning test pins dry-run lesson 2 (FAQ answers live in `o-modal`), and the storage
test pins lesson 3 (re-ingesting a page must not leave stale chunks behind).
"""

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import ToolMessage

from app.knowledge.loader import Source, clean_html, to_document
from app.knowledge.tools import NO_KB_RESULT, build_search_knowledge_base
from app.knowledge.vectorstore import (
    chunk_id,
    count_chunks,
    open_vectorstore,
    split_documents,
    store_chunks,
)
from tests.conftest import RelevanceInMemoryVectorStore, fake_embeddings, make_settings

FAQ_PAGE = """
<html>
<head><title>Dúvidas Get Smart - Getnet</title><style>.x{color:red}</style></head>
<body>
  <header>Menu do site</header>
  <nav class="gnt-nav">Maquininhas | Pix | Ajuda</nav>
  <div class="cookie-banner">Usamos cookies</div>
  <div class="popup-promo">Peça já a sua!</div>
  <div class="modal-cliente">Já é cliente? Entre aqui</div>
  <main>
    <h1>Perguntas frequentes</h1>
    <div class="o-modal"><p>A Get Smart aceita Pix, cartões e vale-refeição.</p></div>
    <form>Busca</form>
    <script>trackEverything()</script>
  </main>
  <footer>Getnet 2026</footer>
</body>
</html>
"""


def test_cleaning_keeps_faq_answers_in_o_modal_and_drops_boilerplate() -> None:
    title, text = clean_html(FAQ_PAGE)

    assert title == "Dúvidas Get Smart"
    assert "A Get Smart aceita Pix, cartões e vale-refeição." in text
    assert "Perguntas frequentes" in text
    for boilerplate in (
        "Menu do site",
        "Maquininhas | Pix",
        "Usamos cookies",
        "Peça já",
        "Já é cliente",
        "Busca",
        "trackEverything",
        "Getnet 2026",
        "color:red",
    ):
        assert boilerplate not in text, boilerplate
    assert "Dúvidas Get Smart" not in text  # the title does not leak into the body text
    assert "\n\n" not in text  # empty lines dropped


def test_document_metadata() -> None:
    source = Source(url="https://site.getnet.com.br/duvidas/get-smart/", market="Brasil", topic="X")

    document = to_document(source, FAQ_PAGE)

    assert document.metadata == {
        "url": source.url,
        "title": "Dúvidas Get Smart",
        "market": "Brasil",
        "topic": "X",
    }


def _page(url: str, text: str) -> Document:
    return Document(
        page_content=text,
        metadata={"url": url, "title": "Get Smart", "market": "Brasil", "topic": "Get Smart"},
    )


def test_chunks_are_prefixed_and_numbered() -> None:
    text = "\n".join(f"Parágrafo {i}: " + "texto " * 30 for i in range(10))

    chunks = split_documents([_page("https://a", text)], chunk_size=400, chunk_overlap=50)

    assert len(chunks) > 1
    assert all(chunk.page_content.startswith("[Brasil] Get Smart\n") for chunk in chunks)
    assert [chunk.metadata["chunk"] for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.metadata["url"] == "https://a" for chunk in chunks)


def test_chunk_id_is_stable_and_position_dependent() -> None:
    assert chunk_id("https://a", 0) == chunk_id("https://a", 0)
    assert chunk_id("https://a", 0) != chunk_id("https://a", 1)
    assert len(chunk_id("https://a", 0)) == 32


def test_reingestion_replaces_a_page_without_stale_chunks(tmp_path: Path) -> None:
    store = open_vectorstore(make_settings(tmp_path), fake_embeddings())
    long_text = "\n".join("linha " * 40 for _ in range(12))
    first = split_documents([_page("https://a", long_text)], chunk_size=400, chunk_overlap=0)
    other = split_documents([_page("https://b", "outra página")], chunk_size=400, chunk_overlap=0)
    store_chunks(store, first + other)
    assert count_chunks(store) == len(first) + 1

    shorter = split_documents([_page("https://a", "página encurtada")], 400, 0)
    store_chunks(store, shorter)  # upsert alone would keep the old chunks 1..n of page a

    assert count_chunks(store) == 2
    store_chunks(store, shorter)
    assert count_chunks(store) == 2  # idempotent


def _search_tool(min_score: float = 0.0):
    store = RelevanceInMemoryVectorStore(fake_embeddings())
    store.add_documents(
        [
            Document(
                page_content="[Brasil] Pix\nO Pix cai na hora.",
                metadata={"url": "https://site.getnet.com.br/pix/", "title": "Pix"},
            )
        ]
    )
    return build_search_knowledge_base(store, k=4, min_score=min_score)


def test_search_returns_snippets_and_a_source_artifact() -> None:
    message = _search_tool().invoke(
        {
            "type": "tool_call",
            "name": "search_knowledge_base",
            "args": {"query": "[Brasil] Pix\nO Pix cai na hora."},
            "id": "call-1",
        }
    )

    assert isinstance(message, ToolMessage)
    assert "O Pix cai na hora." in str(message.content)
    assert message.artifact == [{"title": "Pix", "url": "https://site.getnet.com.br/pix/"}]


def test_search_below_the_threshold_returns_nothing() -> None:
    message = _search_tool(min_score=1.01).invoke(
        {"type": "tool_call", "name": "search_knowledge_base", "args": {"query": "x"}, "id": "c"}
    )

    assert isinstance(message, ToolMessage)
    assert message.content == NO_KB_RESULT
    assert message.artifact == []
