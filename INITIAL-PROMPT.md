# Build brief: Getnet multi-agent support system

> **How to run:** open Claude Code in this folder and send `Execute @INITIAL-PROMPT.md`.
>
> I am recording my screen. Do not ask me questions: every decision is in this brief or in
> `specs/`. If something is still ambiguous, pick the simplest option consistent with the
> specs, write it down and report it at the end.

---

## 0. What is already here

These files were prepared before the recording. Read them in this order before writing code.

| Path | What it is |
|---|---|
| `INITIAL-PROMPT.md` | This brief: the context, the decisions and **why** they were made |
| `AI_Hardcore_Engineer_Multi-Agent.md` | The challenge statement |
| `specs/001-multi-agent-support/spec.md` | **WHAT and WHY**: scenarios, requirements, acceptance criteria |
| `specs/001-multi-agent-support/plan.md` | **HOW**: stack, architecture, contracts, known pitfalls |
| `specs/001-multi-agent-support/tasks.md` | **In what ORDER**: tasks linked to requirements, with checkpoints |
| `data/sources.json` | The 42 curated knowledge-base URLs. This is the **input** of the ingestion, the result of the source research (§3 and §4); the ingestion itself (load → split → embed → store) runs live |
| `data/customers.json` | Fictitious merchants (`cliente1988`, `cliente2000`) and card response codes |
| `evals/dataset.jsonl` | The 10 challenge scenarios plus 5 guardrail and escalation cases, with expected route and tools |
| `.gitignore` | Prepared first, so `.env` can never be committed |
| `.env` | The real API keys. **Never read, print or overwrite it.** |

The specs are the source of truth for details. This brief explains the reasoning. If the two
disagree, follow the specs and report the conflict.

---

## 1. Context

Getnet serves merchants in Brazil: card machines (Get Clássica, Get Smart, Get Mini), Pix,
Payment Link, crediário, receivables advance. A merchant asks two very different kinds of
question in the same channel:

- how a product works ("How does receivables advance work?");
- what is happening with their own account ("When will yesterday's sales be deposited?").

A single generic assistant either invents product facts or has no access to account data. So
we build a **multi-agent orchestration behind one HTTP endpoint**:

- a Router that understands the message;
- specialists that each own one kind of problem, grounded in Getnet's website and in the
  merchant's (fictitious) data;
- a human handoff for what the system cannot resolve.

This is the core build of a technical challenge. It is judged on:

- engineering quality and agent design;
- the RAG pipeline;
- tests, Docker and the README.

---

## 2. Objective and differentiators

### Required by the challenge

- At least 3 agent types with a clear communication mechanism: Router, Knowledge (RAG plus
  web search) and Customer Support (2 tools or more).
- `POST` endpoint receiving `{"message", "user_id"}` and returning meaningful JSON.
- Dockerfile and compose, so it runs with standard Docker commands.
- A testing strategy, including how to integration-test the orchestration.
- A README covering:
  - how to build, configure, run and test;
  - the architecture, design choices and message workflow;
  - the RAG pipeline and how LLM tools were used.

### Bonus: all four, built with library features

| Bonus | How |
|---|---|
| 4th agent | **Human Escalation agent**: opens a ticket and writes the handoff for the operator |
| Guardrails | The Router refuses unsafe, irrelevant and unsupported requests. LangChain `PIIMiddleware` masks card numbers, CPF, CVV and passwords before the LLM; the LangSmith anonymizer does the same in traces; a tool-call limit stops loops |
| Human handoff | A specialist that cannot resolve sets `needs_human` and the graph sends the case to escalation. Works even with the LLM down |
| Evaluation and observability | One LangSmith trace and one JSON log line per request, correlated by `request_id`. An eval dataset with deterministic evaluators (local report or LangSmith experiment). A production monitoring plan in the README |

### Principles

These hold for every file.

1. **Library before custom code.** Use LangChain, LangGraph and LangSmith features first:
   `create_agent`, middleware, structured output, `StateGraph`, the fake models for tests. No
   home-made framework.
2. **One folder per domain subject** (chat, agents, knowledge, customers, tickets), never one
   folder per technical type. Whoever wants to understand the RAG opens only `knowledge/`.
3. **Every module starts with a docstring** of 2 to 4 lines saying why it exists.
4. **Arithmetic and dates live in code**, never in the model.
5. **The model never chooses whose data to read.** `user_id` comes from the runtime
   context, not from a tool argument.
6. **Answers come only from tool results.** A cited source must be a URL that a tool returned.
7. **Everything is testable offline.** `pytest` needs no API key.

---

## 3. Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12, **uv** | Market standard for agents; reproducible lockfile |
| API | FastAPI, Pydantic v2, pydantic-settings | Typed contracts, generated OpenAPI, settings validated at startup |
| Orchestration | LangGraph `StateGraph` | Deterministic routing, typed shared state, conditional edges |
| Agents | LangChain `create_agent` (x4) | Tool loop, structured output and middleware out of the box |
| LLM | OpenAI `gpt-4.1-mini` via `init_chat_model`, temperature 0 | Fast, cheap, native structured output; provider is config |
| Embeddings | `text-embedding-3-small` | Multilingual: English questions retrieve Portuguese pages |
| Vector store | Chroma, local, cosine, persisted in a Docker volume | Evaluators need no extra account; swappable behind one function |
| Web search | Tavily Search (`langchain-tavily`) | Search API built for LLM tools |
| Ingestion | httpx + BeautifulSoup over a curated URL list | Measured against Tavily; see below |
| Observability | LangSmith, JSON logs | One trace per request; logs searchable by request and user |
| Quality | ruff, pyright strict, pytest | |

### What we decided not to use, and why

| Not used | Why |
|---|---|
| **Tavily Crawl / Extract for ingestion** | Measured offline (about 22 credits). The help center lists its 21 articles through JavaScript, with no `<a href>`, so no crawler reaches them. Extract `basic` kept 63% of the text (2% to 22% on FAQ pages, whose answers sit in modals) and dropped the troubleshooting steps of error ADQ 1-18. `advanced` was unstable: the same page came back at 97%, then at 2%. **Tavily stays as the web search tool.** |
| `langchain-community` loaders | The package is being sunset; it emits a deprecation warning |
| A managed vector store (Pinecone) | Evaluators would need another account. Chroma sits behind one function (`open_vectorstore`), so swapping it is a one-file change |
| A supervisor (agents as tools) for routing | One LLM call fewer; the route is explicit in the state and in the trace; testable with a fake model |
| A message queue between agents | One request, one response, one process: the graph state is enough. In production a queue fits the escalation ticket, not the conversation |

---

## 4. Lessons from the offline dry run

I built and tested this system offline before recording. These lessons are already in the
specs. Respect them:

1. **The suggested `www.getnet.net` is Getnet's global and Portugal site** (euros, pt-PT).
   The Brazilian products live on `site.getnet.com.br`. So the sources are 40 Brazilian pages
   plus 2 `getnet.net/en` pages tagged `[Global]`. `/duvidas/pix/` is excluded on purpose:
   it is from 2020 and contradicts the current Pix FAQ.
2. **FAQ answers live inside `o-modal` elements.**
   - Strip only the classes matching `popup|modal-cliente|cookie|gnt-nav`, plus the
     `header`, `footer`, `nav`, `script`, `style`, `noscript`, `svg` and `form` tags.
   - A broad `modal` filter deleted the answers.
3. **Chroma upsert alone left stale chunks** (146 instead of 135). Re-ingestion deletes a
   URL's chunks before adding them again, so it is idempotent.
4. **The model added deposit amounts wrong.** The tool returns totals per deposit date, and
   the prompt forbids arithmetic.
5. **A decline error was answered with connectivity steps.**
   - For declines, Support must call `get_recent_transactions(status="declined")` first.
   - For connectivity, `get_terminal_status` plus the knowledge base.
6. **Answer formatting.** Exchange rates are in BRL by default. No URLs in the answer text:
   they go in the `sources` field.
7. **Library versions are newer than your training data** (LangChain 1.4, LangGraph 1.2).
   - Read the installed source in `.venv/Lib/site-packages/` before using an API.
   - `plan.md` lists the typing and testing pitfalls already solved.

---

## 5. Architecture

### 5.1 Message workflow

```text
POST /v1/chat  {"message": "...", "user_id": "cliente1988"}
   │
   ▼
app/chat/router.py          validates the body (Pydantic), takes or creates X-Request-ID
   │
   ▼
app/chat/service.py         opens the LangSmith trace, calls the graph, logs the result
   │
   ▼
app/orchestration/graph.py  ── LangGraph StateGraph ─────────────────────
   │
   START ──► router ──┬──► knowledge ──┬─────────────────────► END
                      │                └─ needs_human? ─┐
                      ├──► support ────┬─────────────────────► END
                      │                └─ needs_human? ─┤
                      ├──► escalation ◄─────────────────┘ ──► END
                      └──► refusal ────────────────────────► END
   │
   ▼
{data: {answer, route, route_reason, agents, tools_used, sources,
        escalated, ticket_id, language, request_id, latency_ms},
 meta: {code, message}}
```

### 5.2 Orchestration and communication mechanism

**Two layers, two responsibilities:**
- the **graph** decides **who** runs and in which order, deterministically;
- each **agent** decides **how** to solve its part, in a tool-calling loop.

**Communication: direct in-process calls through the graph.** The typed state (`ChatState`)
is the message bus:
- each node reads what it needs and returns a partial update;
- list fields (`agents`, `tools_used`) accumulate through reducers (`operator.add`).

**Edges:**
- `START → router`;
- conditional edge on `route`: `knowledge`, `support`, `escalation` (route `human`) or
  `refusal` (route `unsupported`);
- `knowledge` and `support` → `escalation` if `needs_human`, else `END`;
- `escalation` and `refusal` → `END`.

**Failure becomes handoff.** A specialist node that raises is caught and turned into
`needs_human=True`. So a failure becomes a handoff, never a 500.

### 5.3 The agents

| Agent | Built with | Job | Tools | Output (contract) |
|---|---|---|---|---|
| **Router** | `create_agent`, `response_format=RouteDecision`, no tools | Classify the message and detect its language. Off-topic, prompt injection and requests for another merchant's data are `unsupported` | none | `RouteDecision {route: knowledge \| support \| human \| unsupported, reason, language}` |
| **Knowledge** | `create_agent` + middleware | Product questions through RAG; general questions (weather, exchange rate) through web search. Searches in Portuguese, answers in the merchant's language | `search_knowledge_base`, `web_search` | `SpecialistAnswer {answer, source_urls, needs_human, handoff_reason}` |
| **Customer Support** | `create_agent` + middleware, `context_schema=CustomerContext` | Questions about the merchant's own account: deposits, declines, machines | 4 customer tools + `search_knowledge_base` | `SpecialistAnswer` |
| **Human Escalation** | `create_agent`, `response_format=HandoffPlan` | Writes the handoff: category, priority, a summary for the operator (pt-BR) and the message to the merchant | none: the **ticket is opened by code**, so the protocol number is never invented | `HandoffPlan {category, priority, operator_summary, customer_message}` |
| *refusal* (node, not an agent) | plain code | Polite fixed refusal in the merchant's language | none | text |

The Escalation path also has a **deterministic fallback**: if the LLM fails, the ticket is
still opened with a standard message.

### 5.4 Tools

| Tool | Agent | What it returns |
|---|---|---|
| `search_knowledge_base(query)` | Knowledge, Support | Top 4 chunks above a relevance threshold, as text for the model plus a source artifact for the code (`response_format="content_and_artifact"`) |
| `web_search(query, getnet_only=False)` | Knowledge | Tavily Search; `getnet_only` restricts to Getnet domains |
| `get_customer_profile()` | Support | Merchant profile, document masked |
| `get_sales_and_settlements(days_ago)` | Support | Sales of a day and deposits **already totalled per deposit date** |
| `get_recent_transactions(status, limit)` | Support | Recent transactions with the response code and its meaning |
| `get_terminal_status()` | Support | The merchant's machines: model, connection, status, last error |

Customer tools take **no `user_id` argument**. They read the merchant from
`ToolRuntime[CustomerContext]`, filled by the service from the request. Settlement rules are
fixed in code:
- debit settles in 1 business day;
- the first credit installment settles in 31 days, then every 30 days;
- Pix is instant;
- a deposit that falls on a weekend rolls to the next business day.

### 5.5 RAG pipeline: ingestion → storage → retrieval → generation

1. **Sources:** `data/sources.json`, 42 curated URLs with `url`, `market` and `topic`.
2. **Load:**
   - httpx fetches the pages, 5 at a time;
   - BeautifulSoup removes the boilerplate (lesson 2 above).
3. **Split:**
   - `RecursiveCharacterTextSplitter`, 1200 characters with 200 of overlap;
   - each chunk is prefixed with `[market] page title`, so a chunk from the middle of a page
     keeps its subject.
4. **Embed and store:**
   - `text-embedding-3-small` into Chroma, cosine space;
   - chunk id = hash of `url#position`;
   - a URL's old chunks are deleted before adding.
   - Metadata on every chunk: `url` (the source), `title`, `market`, `topic`, `chunk`.
5. **Retrieve:** `similarity_search_with_relevance_scores`, top 4, minimum score 0.2.
6. **Generate:**
   - the specialist answers only from tool results;
   - code keeps only the cited URLs that a tool really returned.

**Ingestion must be visible**, because I show it in the video:
- the CLI (`python -m app.knowledge.vectorstore`, flags `--rebuild` and `--if-empty`) logs
  one structured JSON line per page as it loads (`ingest.page`, with `"page": "12/42"`, the url
  and the number of characters);
- then the chunk count and the final total in the collection;
- the container runs it with `--if-empty` before starting the API, so it shows in
  `docker compose logs`;
- `/health` reports the chunk count.

### 5.6 Guardrails and reliability

- **`PIIMiddleware`:**
  - `credit_card` (built-in, Luhn-checked) is masked;
  - custom detectors for CPF, CVV and passwords are redacted;
  - it applies to input and output.
- **Traces:** a LangSmith client with an anonymizer masks the same patterns in traces.
- **Router `unsupported` → refusal node:** no specialist runs.
- **`ToolCallLimitMiddleware(run_limit=6)`** stops tool loops.
- **`ToolErrorMiddleware`** turns a tool exception into a message the model can react to.
- **The model client** (`init_chat_model`) uses a 30 s timeout and 2 retries. The API
  returns 503 in the envelope if the graph itself is unavailable.

### 5.7 Observability

- **LangSmith:** one trace per request (`run_name="chat_request"`, metadata `request_id`
  and `user_id`), through the anonymized client.
- **JSON logs:** one `chat.completed` line per request, with `request_id`, `user_id`,
  `route`, `agents`, `tools_used`, `escalated` and `latency_ms`.
- **`X-Request-ID`:** accepted from the caller or generated, and returned in the response.

### 5.8 API contract and envelope

- `POST /v1/chat`: body `{"message": str (1 to 2000 chars, not blank), "user_id": str
  (letters, digits, "_.-")}`.
- `GET /health`: status and number of chunks in the knowledge base.
- `/docs`: the Swagger UI generated by FastAPI. Every route is documented with summary,
  description and response models.
- **Envelope on every response**, success or error: `{"data": ..., "meta": {"code",
  "message"}}`.
  - The backend returns an English `code`: `CHAT_ANSWERED`, `VALIDATION_ERROR` (422),
    `AGENT_UNAVAILABLE` (503), `NOT_FOUND` (404), `INTERNAL_ERROR` (500).
  - Its message comes from a catalog (`app/core/messages.py`).
  - Errors carry `data: null`.

### 5.9 Folder structure

```text
getnet-challenge/
├── app/
│   ├── main.py                   create_app(): FastAPI, lifespan, error handlers
│   ├── container.py              composition root: builds models, tools, agents, graph, service
│   ├── core/                     infrastructure, no business rules
│   │   ├── settings.py           configuration from the environment (.env)
│   │   ├── logging.py            JSON logs, one line per event
│   │   ├── envelope.py           {data, meta: {code, message}}
│   │   ├── messages.py           message catalog by code
│   │   └── errors.py             every error converted to the envelope
│   ├── chat/                     the HTTP layer
│   │   ├── router.py             POST /v1/chat, GET /health
│   │   ├── schemas.py            ChatRequest, ChatResponse (the API contract)
│   │   └── service.py            ChatService: calls the graph, traces, measures, logs
│   ├── orchestration/
│   │   └── graph.py              ChatState, nodes and edges
│   ├── agents/                   the 4 agents
│   │   ├── router_agent.py       Router
│   │   ├── specialist.py         Knowledge and Customer Support, plus source verification
│   │   ├── escalation_agent.py   Human Escalation, plus deterministic fallback
│   │   ├── prompts.py            the 4 system prompts
│   │   ├── schemas.py            contracts between agents
│   │   ├── messages.py           fixed texts: refusal, ticket line, fallback (pt, es, en)
│   │   └── llm.py                init_chat_model
│   ├── knowledge/                the RAG
│   │   ├── loader.py             fetch and clean the pages
│   │   ├── vectorstore.py        split, embed, store in Chroma; also the ingestion CLI
│   │   └── tools.py              search_knowledge_base, web_search
│   ├── customers/                the merchant's data (fictitious)
│   │   ├── schemas.py
│   │   ├── repository.py         reads the JSON, computes deposit dates
│   │   └── tools.py              the 4 Support tools
│   ├── guardrails/
│   │   └── pii.py                PII middleware and trace anonymizer
│   └── tickets/
│       └── repository.py         handoff tickets (in memory)
├── data/                         sources.json, customers.json (prepared)
├── evals/                        dataset.jsonl (prepared), run_evals.py
├── scripts/
│   ├── chat.py                   live debug: streams each node of the graph
│   └── check_api_docs.py         fails if a route is undocumented in OpenAPI
├── specs/001-multi-agent-support/  spec.md, plan.md, tasks.md (prepared)
├── tests/
│   ├── conftest.py               scripted fake model, fixtures
│   ├── unit/
│   ├── integration/              graph, scenarios, API: all offline
│   └── MANUAL-TESTS.md           manual test cases with steps
├── Dockerfile, docker-compose.yml, .dockerignore
├── .env.example                  every variable, commented
├── pyproject.toml, uv.lock
├── INITIAL-PROMPT.md             this brief
└── README.md
```

`container.py` is the only place that knows "who uses what". Modules never build their own
dependencies; they receive them. That is what lets the tests swap the real model for a fake
without touching anything else.

### 5.10 Configuration and Docker

- **`.env.example`** lists every variable with a comment: what it is, whether it is
  required, where to get it, and the default.
  - Required: `OPENAI_API_KEY`, `TAVILY_API_KEY`.
  - Optional: `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and the
    overrides of the settings defaults.
  - If `.env` does not exist, create it as a copy of `.env.example`. If it exists, do not
    touch it.
- **`Dockerfile`:**
  - two stages, dependencies installed with uv;
  - runs as a non-root user;
  - `HEALTHCHECK` on `/health`;
  - command: ingestion with `--if-empty`, then uvicorn on port 8000.
- **`docker-compose.yml`:**
  - service `api`, `container_name: getnet-challenge`, `image: getnet-challenge`;
  - `env_file: .env`, port `8000:8000`;
  - a named volume for the vector store, so ingestion runs only once.

---

## 6. Specs (Spec-Driven Development)

The specs were written from this design before the recording. Use them this way:

- `spec.md`: the acceptance criteria are your target.
- `plan.md`: contracts, settings defaults and known pitfalls.
- `tasks.md`: execute it in order. Tick `[x]` as each task is finished. Run the gate named
  at each checkpoint.

Do not rewrite the specs. If you must deviate, add a short **Deviations** section at the end
of `plan.md`.

---

## 7. Tests

### 7.1 Strategy: a pyramid where every layer is automated

| Layer | What it proves | How | Keys? |
|---|---|---|---|
| **Unit** | Rules in isolation: settlement dates and totals, HTML cleaning, chunk ids, PII masking, customer tools exposing no `user_id` | pytest | no |
| **Integration: orchestration** | The real graph, real agents, real tools and middleware, with a scripted fake model: routing per route, specialist → escalation handoff, a failing specialist → ticket, merchant isolation | `GenericFakeChatModel`, `DeterministicFakeEmbedding`, `InMemoryVectorStore` | no |
| **Integration: challenge scenarios** | The 10 *Example Test Scenarios*, parametrized: for each message, the expected route, specialist, tools and escalation | Same fakes | no |
| **API** | Envelope, 422, 503, `X-Request-ID`, `/health` | FastAPI `TestClient` with fake services | no |
| **Evals** | Real model behaviour on the 10 scenarios plus the 5 guardrail and escalation cases: `route_correct`, `tools_expected`, `escalation_expected`, `no_card_leak` | `evals/run_evals.py`: local markdown report, or `--langsmith` for an experiment | yes |
| **Manual** | The live, exploratory run I do in the video | `tests/MANUAL-TESTS.md`, `scripts/chat.py`, Swagger, LangSmith | yes |

**Be explicit in the scenario tests and the README:**
- in offline tests, the fake model scripts the model's *decisions*;
- what they prove is everything *around* the model: wiring, real tools on test fixtures,
  the handoff and the response contract;
- the model's *judgment* is measured by the evals.

### 7.2 `tests/MANUAL-TESTS.md`

Write it in ISTQB style, in this structure:

1. **Preconditions table**, referenced by ID:
   - `P1`: keys in `.env`;
   - `P2`: knowledge base ingested;
   - `P3`: API running in Docker;
   - `P4`: a PowerShell helper `Ask($msg, $user = "cliente1988")` that posts to `/v1/chat`
     with a UTF-8 body (`[Text.Encoding]::UTF8.GetBytes(...)`), so accents arrive intact.
2. **Summary table:** TC, title, traces to, and an empty *Status* column for me to fill live.
3. **One section per test case** (`TC-01`, `TC-02`...), each with:
   - preconditions;
   - steps with the exact command (`uv run python -m scripts.chat ...` or `Ask ...`);
   - expected result: route, agents, tools, key facts in the answer, ticket;
   - an empty *Actual / Status* line.

Cover at least (IDs from `spec.md`, about 23 cases):
- the 10 challenge scenarios, `S1` to `S10`;
- **human escalation**, both ways:
  - `H1`: the merchant asks for a person ("Quero falar com um atendente, acho que clonaram
    meu cartão");
  - `H2`: a specialist cannot resolve and hands off (a `user_id` with no account);
- guardrails, `G1` to `G5`:
  - prompt injection asking for another merchant's data;
  - a full card number in the message (masked in the answer and in the LangSmith trace);
  - an off-topic request;
- validation: `422` for a blank message and for an invalid `user_id`;
- **ingestion**: Docker logs on first start, `/health` count, re-run unchanged;
- observability: the `chat.completed` log line and the LangSmith trace, found by
  `request_id`.

### 7.3 `scripts/chat.py`: live debugging

Usage: `uv run python -m scripts.chat "message" --user cliente1988`.

- It runs the real graph with `astream(stream_mode="updates")` and prints each node as it
  finishes:
  - the router's route, reason and language;
  - the specialist's tools and sources;
  - the escalation ticket;
  - if the installed LangGraph supports subgraph streaming, the tool calls inside each agent.
- Then it prints the final response JSON.
- Readable colours, no extra dependency.

### 7.4 No UI of our own

We do not build a UI. Each need already has a tool:
- the graph running node by node: `scripts/chat.py`;
- the HTTP contract: Swagger at `/docs`;
- traces, dashboards and dataset results: LangSmith (traces, the project's monitoring charts
  and the eval experiments).

LangGraph Studio was tested in the dry run and left out: with the installed versions
(`langgraph-api` 0.16.8, Starlette 1.10) the dev server answers Chrome's private-network
preflight with `400`, so the browser cannot reach it.

---

## 8. README.md

Written for an evaluator with ten minutes. Quick start first. Sections:

1. **Overview**: what it does, with a table *challenge requirement → where it is satisfied*.
2. **Quick start**:
   - with Docker: `.env` → `docker compose up --build` → first request;
   - locally with uv.
3. **Running the tests**: offline suite, evals (local and `--langsmith`), manual test cases and
   `scripts/chat.py`.
4. **Architecture**:
   - orchestration;
   - communication mechanism;
   - message workflow diagram;
   - the agents table;
   - design choices, including "what we decided not to use".
5. **RAG pipeline**:
   - ingestion → storage → retrieval → generation;
   - the source curation;
   - *Why not Tavily for ingestion*, with the numbers;
   - known limitation.
6. **LLM tools**: every tool, which agent uses it, and why `user_id` is not an argument.
7. **Guardrails and reliability.**
8. **Evaluation and observability**: traces, logs, evals, and a production monitoring plan:
   - online evaluators on sampled traces;
   - alerts on escalation rate, latency and error rate;
   - dashboards.
9. **Testing strategy**: the pyramid, and how to approach comprehensive integration testing
   of the orchestration.
10. **Project structure.**
11. **Spec-Driven Development**: how `INITIAL-PROMPT.md` and `specs/` drove the build.
12. **Production roadmap**:
    - conversation memory;
    - real authentication;
    - a managed vector store;
    - hybrid search for error codes;
    - a holiday calendar.

---

## 9. Execution plan

The phases match the checkpoints in `tasks.md`. At the end of each phase:
- run its gate;
- print **one status line**: phase, files created, gate result.

I narrate on top of it, so keep moving.

| Phase | Build | Gate | Shown on video |
|---|---|---|---|
| 1. Foundation | `uv init --app --no-package --python 3.12`, dependencies, ruff/pyright/pytest config, `core/`, `.env.example`, `.env` if missing, Docker files | ruff, pyright | the tree and `.env.example` |
| 2. Merchant data | `customers/`, `tickets/`, unit tests | pytest | |
| 3. Knowledge | `knowledge/`, unit tests (do **not** run the ingestion) | pytest | the RAG code |
| 4. Agents | `agents/`, `guardrails/` | ruff, pyright | |
| 5. Orchestration and API | `orchestration/graph.py`, `chat/`, `container.py`, `main.py` | pytest | |
| 6. Tests and tooling | scenario and API tests, `evals/run_evals.py`, `scripts/chat.py`, `scripts/check_api_docs.py`, `tests/MANUAL-TESTS.md` | full pytest, docs check | **the test run** |
| 7. Ship | the README, then `docker compose config --quiet` to validate the compose file (do **not** build or start the container) | ruff, pyright, pytest, compose config | the README and the Docker files |

**The split: you write and check the code; I run the system.**

- You run only the quality gates: `ruff`, `pyright`, `pytest` and `docker compose config`.
- You do **not** run the ingestion, `docker compose up`, `scripts/chat.py` or the evals.
- I run them live after the build, in this order:
  1. `docker compose up --build -d`, which builds in the background;
  2. `uv run pytest -v`;
  3. the ingestion;
  4. `scripts/chat.py` with S1, S3 and H1;
  5. the evals;
  6. the container logs, `/health` and one request through the API.
- Their first run is on video. If one fails, I will paste the error here and ask you to fix
  it.

---

## 10. Definition of done

**Yours, before you report:**

- [ ] `uv run pytest` green with no API keys.
- [ ] `uv run ruff check .` and `uv run pyright` clean (strict).
- [ ] `docker compose config --quiet` passes.
- [ ] README complete, `tests/MANUAL-TESTS.md` written, every task in `tasks.md` ticked.

**Mine, live, after your report:**

- [ ] Ingestion: 42 pages, chunk count printed; a second run leaves the count unchanged.
- [ ] `docker compose up --build`: container `getnet-challenge` healthy; `/health` shows the
      chunk count.
- [ ] S1, S3 and H1 through `scripts/chat.py`, each with the expected route.
- [ ] Evals: `route_correct`, `tools_expected`, `escalation_expected` and `no_card_leak` at
      100%.

**Final report:**
- the phases with their gate results;
- any deviation from the specs;
- the exact commands I will run live, in the order above.

---

## 11. Rules

- **Never read, print or overwrite `.env`.** The keys are already there.
- **No git commit or push.** I commit at the end.
- Do not add `langchain-community`. OpenAI models only.
- When pyright strict flags a partially unknown LangChain type, use a targeted
  `# pyright: ignore[rule]` with the reason, never a file-wide ignore.
- Never start a long-running process in the foreground (uvicorn, a dev server). If you need
  one for a quick check, run it in the background, follow its logs with a bounded command
  and stop it. Never block the session.
- No em-dashes in documentation. English everywhere in the repository.
- Do not stop to ask, and do not revisit decisions already made here.
- Host: Windows 11 with Docker Desktop running.
