# Plan 001: technical design

The reasoning behind each choice is in `INITIAL-PROMPT.md`; this file holds the contracts and the
details. Folder structure: `INITIAL-PROMPT.md` §5.9.

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12, uv (`uv init --app --no-package --python 3.12`) |
| API | FastAPI, Pydantic v2, pydantic-settings, uvicorn |
| Orchestration | LangGraph `StateGraph` |
| Agents | LangChain `create_agent` (x4) with middleware |
| LLM | OpenAI `gpt-4.1-mini` via `init_chat_model`, temperature 0 |
| Embeddings | OpenAI `text-embedding-3-small` |
| Vector store | Chroma (`langchain-chroma`), local, cosine |
| Web search | Tavily Search (`langchain-tavily`) |
| Ingestion | httpx, BeautifulSoup, `langchain-text-splitters` |
| Observability | LangSmith, JSON logs |
| Quality | ruff, pyright strict, pytest |

Runtime dependencies: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `python-dotenv`,
`langchain`, `langchain-openai`, `langchain-chroma`, `langchain-tavily`,
`langchain-text-splitters`, `langsmith`, `httpx`, `beautifulsoup4`.
Dev: `pytest`, `ruff`, `pyright`. **Not** `langchain-community`.

Tooling config in `pyproject.toml`: ruff line length 100, rules `E,F,I,B,UP,ANN,SIM,RUF`,
ignore `ANN401`, tests ignore `ANN`; pyright `strict` with `reportUnknownMemberType` and
`reportMissingTypeStubs` set to `none`; pytest `testpaths = ["tests"]`.

## Settings (`app/core/settings.py`)

`pydantic-settings`, loaded from the project's `.env`, cached with `lru_cache`.

| Setting | Default |
|---|---|
| `openai_api_key`, `tavily_api_key` | required (`SecretStr`), fail at startup if missing |
| `chat_model` | `openai:gpt-4.1-mini` |
| `embedding_model` | `text-embedding-3-small` |
| `llm_timeout_seconds` / `llm_max_retries` | 30 / 2 |
| `tool_call_limit` | 6 |
| `sources_file` / `customers_file` | `data/sources.json` / `data/customers.json` |
| `vectorstore_dir` / `collection_name` | `.vectorstore` / `getnet_kb` |
| `chunk_size` / `chunk_overlap` | 1200 / 200 |
| `retrieval_k` / `retrieval_min_score` | 4 / 0.2 |
| `web_search_max_results` | 3 |
| `timezone` | `America/Sao_Paulo` |
| `log_level` | `INFO` |

LangSmith reads its own variables (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
`LANGSMITH_PROJECT`); they are optional.

## Graph (`app/orchestration/graph.py`)

```python
class ChatState(TypedDict, total=False):
    user_id: Required[str]
    message: Required[str]
    route: Route
    route_reason: str
    language: str
    answer: str
    sources: list[SourceRef]
    tools_used: Annotated[list[str], operator.add]
    agents: Annotated[list[str], operator.add]
    needs_human: bool
    handoff_reason: str | None
    ticket_id: str | None
```

`build_graph(*, router, knowledge, support, escalation, tickets)` receives compiled agents
(dependency injection, so tests pass agents built on fake models).

- Nodes: `router`, `knowledge`, `support`, `escalation`, `refusal`.
- `START → router`; conditional on `route`: `knowledge`, `support`, `escalation` (`human`),
  `refusal` (`unsupported`). Read the route with `state.get("route", "unsupported")`.
- `knowledge` / `support` → `escalation` if `needs_human`, else `END`.
- A specialist node catches any exception and returns `needs_human=True` with the reason.
- The support node invokes its agent with `context=CustomerContext(user_id=state["user_id"])`.
- Compile with a name (`compile(name="getnet_support")`) so the trace reads well.

## Agent contracts (`app/agents/schemas.py`)

- `Route = Literal["knowledge", "support", "human", "unsupported"]`
- `RouteDecision {route: Route, reason: str, language: str (ISO 639-1)}`
- `SourceRef {title, url}`
- `SpecialistAnswer {answer, source_urls: list[str], needs_human: bool, handoff_reason: str | None}`
  (the agents' `response_format`)
- `SpecialistResult {answer, sources: list[SourceRef], tools_used, needs_human, handoff_reason}`
  (what a specialist node returns to the graph, after source verification)
- `HandoffPlan {category, priority, operator_summary, customer_message}`
  - `category`: `settlement_and_payments`, `card_machine`, `transactions_and_declines`,
    `account_and_registration`, `fraud_or_security`, `complaint`, `other`
  - `priority`: `urgent` (fraud, security, cannot sell at all), `high` (money not received,
    repeated declines), `medium` (device or account issue with a workaround), `low`
  - `operator_summary`: 2 to 4 sentences in Brazilian Portuguese
  - `customer_message`: in the customer's language; never invents a ticket number or deadline
- `EscalationRequest {user_id, message, language, origin: router | knowledge | support, findings, handoff_reason}`
- `EscalationResult {answer, ticket_id, priority}`

The escalation code appends the ticket line (`app/agents/messages.py`, pt/es/en) to the
customer message. If the Escalation LLM fails, a deterministic fallback opens the ticket
with priority `high` and a standard message.

## Prompts (`app/agents/prompts.py`)

- **Router:** the four routes with examples; prompt injection, other merchants' data and
  off-topic tasks are `unsupported`; a request for a person, fraud or security is `human`.
- **Knowledge:** search the knowledge base in Portuguese; use `web_search` only for general
  or current facts; money in BRL by default; answer in the customer's language; never put
  URLs in the answer text; if the tools return nothing useful, say so.
- **Support:**
  - deposits: call `get_sales_and_settlements` and use `deposits_by_date`; **never add
    amounts**;
  - declines: **always** call `get_recent_transactions(status="declined")` first and explain
    each response code;
  - machine or connectivity issues: `get_terminal_status`, then `search_knowledge_base` for
    the error or the procedure;
  - `CUSTOMER_NOT_FOUND`, or anything the tools cannot resolve: `needs_human=true` with the
    reason.
- **Escalation:** fill `HandoffPlan` from the request and the specialist's findings.

## Customer data (`app/customers/`)

- `data/customers.json`: merchants `cliente1988` (Padaria Pão Dourado, Porto Alegre: Get
  Smart offline over Wi-Fi with `ADQ 1-18 (sinal fraco)`, Get Clássica online over 3G,
  declines 51 and 55 today and 54 yesterday) and `cliente2000` (Ótica Boa Vista, Recife), plus
  `response_codes` 05, 51, 54, 55, 57, 91. Dates are relative (`days_ago`), so the data never
  goes stale.
- Settlement rules: debit in 1 business day; first credit installment in 31 days, then every
  30 days; Pix instant; a weekend date rolls to the next business day. Status `paid`,
  `due_today` or `scheduled` relative to today.
- `SalesDay.deposits_by_date`: totals per deposit date, computed in code.
- Tools return JSON strings. An unknown merchant returns
  `{"error": "CUSTOMER_NOT_FOUND", "detail": "No merchant account is linked to this user."}`.
  The document is masked in `get_customer_profile`.
- `CustomerContext` is a dataclass with `user_id`; every tool takes
  `runtime: ToolRuntime[CustomerContext, dict[str, object]]` and no `user_id` argument.

## Tickets (`app/tickets/repository.py`)

In memory. Id `GN-` plus 8 uppercase hex characters. `create` and `get`.

## RAG (`app/knowledge/`)

- **Loader:**
  - `httpx.AsyncClient`, follow redirects, 30 s timeout, a descriptive User-Agent;
  - `asyncio.Semaphore(5)`; non-200 pages are skipped and logged.
- **Cleaning:**
  - remove the tags `header, footer, nav, script, style, noscript, svg, form`;
  - remove the elements whose class matches `popup|modal-cliente|cookie|gnt-nav`; **keep**
    `o-modal`, where the FAQ answers live;
  - take the title from `soup.title`, cut at `" - "`;
  - take the text from `soup.body`, so the title does not leak into it;
  - drop empty lines.
- **Document metadata:** `url`, `title`, `market`, `topic`. Chunk metadata adds `chunk`.
- **Split:**
  - `RecursiveCharacterTextSplitter(chunk_size, chunk_overlap)`;
  - prefix every chunk with `[market] title`.
- **Chunk id:** the first 32 characters of the sha256 of `f"{url}#{chunk}"`.
- **Store:**
  - `Chroma(collection_name, embedding_function, persist_directory, collection_configuration={"hnsw": {"space": "cosine"}})`;
  - `store.delete(where={"url": {"$in": urls}})` before `add_documents(chunks, ids=...)`.
- **CLI** (`python -m app.knowledge.vectorstore`):
  - `--rebuild` deletes the collection first;
  - `--if-empty` skips when chunks exist;
  - logs JSON events: `ingest.started` (sources, rebuild), one `ingest.page` per page in
    completion order (`page: "12/42"`, `url`, `status`, `chars`), then `ingest.completed`
    (`pages_loaded`, `pages_failed`, `chunks`, `total_in_store`).
- **`search_knowledge_base(query)`:**
  - `@tool(response_format="content_and_artifact")`;
  - `similarity_search_with_relevance_scores(k)`, filtered by `retrieval_min_score`;
  - content: numbered snippets with title and url; artifact: `list[SourceRef]`.
- **`web_search(query, getnet_only=False)`:**
  - wraps `TavilySearch(max_results)`, with `include_domains=["getnet.net", "getnet.com.br"]`
    when `getnet_only`;
  - same `content_and_artifact` format.

## Guardrails (`app/guardrails/pii.py`)

- Agent middleware:
  - `PIIMiddleware("credit_card", strategy="mask", apply_to_input=True, apply_to_output=True)`;
  - one `PIIMiddleware(name, detector=regex, strategy="redact", apply_to_output=True)` per
    custom type:
    - `cpf`: `(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)`
    - `cvv`: `(?i)\b(?:cvv|cvc|c[oó]digo de seguran[cç]a)\s*[:=]?\s*\d{3,4}\b`
    - `password`: `(?i)\b(?:senha|password)\s*(?:[:=]|é|is)\s*\S+`
- Specialists also get `ToolCallLimitMiddleware(run_limit=tool_call_limit)` and
  `ToolErrorMiddleware(on_error)`.
- Traces:
  - `langsmith.Client(anonymizer=create_anonymizer([StringNodeRule(pattern=..., replace="[REDACTED_<TYPE>]")]))`;
  - the patterns are the custom ones plus `credit_card`: `(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)`;
  - `ChatService` runs the graph inside `tracing_context(client=...)`; the LangChain tracer
    honours it.

## API (`app/chat/`, `app/core/`)

- `ChatRequest`:
  - `message`: 1 to 2000 characters, not blank;
  - `user_id`: pattern `^[A-Za-z0-9_.-]{1,64}$`;
  - with an OpenAPI example.
- `ChatResponse`: `answer, route, route_reason, agents, tools_used, sources, escalated,
  ticket_id, language, request_id, latency_ms`.
- Envelope: `Envelope[T] {data: T | None, meta: {code, message}}`.
  - Codes: `CHAT_ANSWERED`, `HEALTH_OK`, `VALIDATION_ERROR` (422), `AGENT_UNAVAILABLE` (503),
    `NOT_FOUND` (404), `INTERNAL_ERROR` (500).
  - The messages live in `app/core/messages.py`.
- `X-Request-ID`: taken from the request or generated (uuid4 hex), returned in the response
  and in the body.
- `create_app(services: Services | None = None)`: without services, `lifespan` builds the
  real ones; tests pass fakes.
- `scripts/check_api_docs.py` fails if a route lacks a summary, a description or a response
  model.

## Observability

- JSON log formatter.
- Events: `chat.completed` (`request_id`, `user_id`, `route`, `agents`, `tools_used`,
  `escalated`, `latency_ms`), `chat.failed`, `ingest.page`, `ingest.completed`,
  `ingest.skipped`.
- LangSmith: `config={"run_name": "chat_request", "metadata": {"request_id", "user_id"}}`.

## Docker

- `Dockerfile`:
  - stage 1: `ghcr.io/astral-sh/uv` copies the uv binary; `uv sync --frozen --no-dev`;
  - stage 2: `python:3.12-slim`, non-root user, `.venv` on `PATH`;
  - `HEALTHCHECK` with Python's `urllib` on `/health`, start period 120 s;
  - `CMD`: `python -m app.knowledge.vectorstore --if-empty && exec uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- `docker-compose.yml`:
  - service `api`, `container_name: getnet-challenge`, `image: getnet-challenge`;
  - `env_file: .env`, port `8000:8000`;
  - named volume `vectorstore` mounted at `/app/.vectorstore`;
  - `restart: unless-stopped`.
- `.dockerignore`: `.venv`, `.env`, `.vectorstore`, caches, `evals/reports`.

## Evals (`evals/run_evals.py`)

- Reads `evals/dataset.jsonl` (`id, message, user_id, expected_route, expected_tools,
  expected_escalated?`) and runs the real graph.
- Evaluators:
  - `route_correct`;
  - `tools_expected`: every expected tool was called;
  - `escalation_expected`: only when the case defines it;
  - `no_card_leak`: no 13 to 19 digit card number in the answer.
- Output:
  - default: a markdown report in `evals/reports/` and a summary table on stdout;
  - `--langsmith`: uploads the dataset if missing and runs `langsmith.aevaluate` as an
    experiment.

## Live debug (`scripts/chat.py`)

- `uv run python -m scripts.chat "message" --user cliente1988`.
- `Services` exposes the compiled `graph` next to `chat` and `knowledge_base_chunks`, so the
  script runs the same graph the API runs.
- Builds the real services and runs
  `graph.astream(input, stream_mode=["updates", "values"], subgraphs=True)` inside
  `tracing_context(client=anonymized_tracing_client())`, `run_name="chat_cli"`.
- Each event is `(namespace, mode, data)`. An empty namespace is a graph node (router,
  specialist, escalation, refusal); a non-empty one is a step inside an agent, whose
  `messages` hold the model's tool calls (`AIMessage.tool_calls`) and the tool results
  (`ToolMessage`). The last top-level `values` event is the final state.
- Windows: `sys.stdout.reconfigure(encoding="utf-8")` when stdout is a `TextIOWrapper`;
  enable ANSI colours with `subprocess.run("", shell=True, check=False)` (pyright flags
  `os.system` as deprecated).
- Prints each node's update as it completes, with ANSI colours and no extra dependency:
  - the route, reason and language;
  - the tools and sources;
  - the ticket.
- Then prints the final `ChatResponse`-shaped JSON.

## Testing strategy

| Layer | Files | Proves |
|---|---|---|
| Unit | `tests/unit/` | Settlement dates and totals, HTML cleaning (keeps `o-modal` answers), chunk ids, PII detectors, customer tools expose no `user_id`, ticket ids |
| Integration: orchestration | `tests/integration/test_orchestration.py` | Real graph and agents on scripted fake models: each route, specialist → escalation, failing specialist → ticket, merchant isolation, unverified URLs dropped |
| Integration: scenarios | `tests/integration/test_scenarios.py` | S1 to S10 parametrized: route, specialist, real tools on test fixtures, response contract |
| API | `tests/integration/test_api.py` | Envelope, 422, 503, `X-Request-ID`, `/health` |
| Evals | `evals/` | Real model behaviour (AC-1) |
| Manual | `tests/MANUAL-TESTS.md` | Live checks, ISTQB format |

## Known pitfalls (solved in the offline dry run)

**Typing (pyright strict)**
- `from langchain.agents import create_agent` and `from langsmith import tracing_context` are
  partially unknown: `# pyright: ignore[reportUnknownVariableType]` on the import.
- Middleware lists are invariant: `type Middleware = AgentMiddleware[Any, Any, Any]`.
- Compiled agents: `type AgentGraph = CompiledStateGraph[Any, Any, Any, Any]`.
- `ToolRuntime` needs both parameters: `ToolRuntime[CustomerContext, dict[str, object]]`.
- `lifespan` returns `AsyncGenerator[None]` (pyright flags `AsyncIterator` with
  `asynccontextmanager`).
- `Settings()` with required fields from the environment:
  `# pyright: ignore[reportCallIssue]`.
- List defaults in Pydantic: `Field(default_factory=list[str])`.
- Non-required `TypedDict` keys: read with `.get`.
- Tavily raw results are untyped dicts: targeted ignores on the access lines only.

**Tests with fakes**
- `GenericFakeChatModel` has no `bind_tools`: subclass it, override `bind_tools` to return
  `self`, and record the received messages.
- With a fake model, `create_agent`'s structured output (tool strategy) is a **tool call
  named after the schema** (`RouteDecision`, `SpecialistAnswer`, `HandoffPlan`) with the
  fields as args.
- `InMemoryVectorStore` has no relevance function: subclass it and override
  `_select_relevance_score_fn` (cosine distance to relevance).
- `InMemoryVectorStore` scores by cosine (-1 to 1): map it with `(score + 1) / 2`, or
  langchain-core warns that relevance scores must be between 0 and 1.
- Embeddings: `DeterministicFakeEmbedding(size=32)`.
- Offline scenario tests need a stand-in for Tavily with the **same name and contract**:
  `@tool("web_search", response_format="content_and_artifact")` returning text plus
  `[{"title", "url"}]`, added to the Knowledge agent's tools in the test graph.
- OpenAI structured output is native (provider strategy): with the real model there is no
  `RouteDecision` tool call to print; with the fake model there is.
- Settings in tests: build `Settings(openai_api_key="test", tavily_api_key="test", ...)`
  with temporary paths; never read `.env`.

**Data and RAG**
- A broad `modal` class filter removed the FAQ answers (86 characters left on a FAQ page).
- Upsert alone left stale chunks (146 instead of 135); delete by URL first.
- `langchain-community` emits a deprecation warning; it is not needed.

**Model behaviour (found by the evals)**
- S8 was answered with connectivity steps; fixed by the decline rule in the Support prompt.
- The model added deposit amounts wrong; fixed with `deposits_by_date` computed in code.

## Deviations

Recorded during the build; none changes a requirement or an acceptance criterion.

- **Router failure goes to escalation, not 503.** A Router that raises sets `route=human`, so
  the deterministic escalation fallback still opens a ticket (NFR-003, "handoff works with the
  LLM down"). 503 stays for a graph that cannot run at all.
- **`PIIMiddleware` on all four agents**, not only the specialists: the Router and the
  Escalation agent also receive the merchant's message. In addition, `redact_text` (the trace
  patterns) is applied in code to every answer and ticket text, because with structured output
  the middleware's `after_model` only scans message content, not the structured response.
- **Tool artifacts are plain `{title, url}` dicts** (same contract as the offline `web_search`
  stand-in), so `app/knowledge` does not import `SourceRef` from `app/agents`; the specialist
  converts them during source verification.
- **`tools_used`** excludes the structured-output tool message (`SpecialistAnswer`, produced by
  the tool strategy) instead of receiving a list of tool names.
- **The refusal node does not append to `agents`**: it is a node, not an agent, and the route
  (`unsupported`) already shows it ran.
- **`get_settings()` also calls `load_dotenv`** (`override=False`), so `LANGSMITH_*` variables
  in `.env` reach `os.environ` for local runs (`scripts.chat`, evals); Docker's `env_file` still
  wins.
- **`/health` returns `status: degraded`** (still 200) when the knowledge base has no chunks.
- **`Services.graph` is optional** (`AgentGraph | None`), so API tests can pass fake services.
- **The ingestion CLI exits 1 when no page loads**, so the container never starts an API on an
  empty knowledge base after a total network failure (`restart: unless-stopped` retries).
- **Unknown languages fall back to Portuguese** for the fixed texts (Getnet's merchants are
  Brazilian).
- **Two extra offline tests:** `tests/integration/test_chat_cli.py` (the debugger's streaming,
  including tool calls inside the agents) and `tests/integration/test_container.py` (the
  composition root wires the real clients without a network call).
- **`uv init` created a git repository** (`.git`); nothing was committed.
- **`tasks.md` has no T13**; the numbering was kept as is.
