# Tasks 001

Execute in order. Tick `[x]` when a task is done. Each task names the requirement it closes.
At each checkpoint, run the gate and print one status line: phase, files created, gate
result.

## Phase 1: Foundation

- [x] T01 uv project (Python 3.12), runtime and dev dependencies, ruff / pyright / pytest
      config in `pyproject.toml`
- [x] T02 `app/core/`: settings, JSON logging, envelope, message catalog, error handlers
      (FR-001, NFR-004)
- [x] T03 `.env.example` with every variable commented; create `.env` from it only if it
      does not exist (FR-009)
- [x] T04 `Dockerfile`, `docker-compose.yml` (container `getnet-challenge`), `.dockerignore`
      (FR-009)

**Checkpoint 1:** `uv run ruff check .` and `uv run pyright`

## Phase 2: Merchant data

- [x] T05 `app/customers/schemas.py` and `repository.py`: relative dates, settlement rules,
      `deposits_by_date` (FR-004)
- [x] T06 `app/customers/tools.py`: 4 tools on `ToolRuntime[CustomerContext]`, no `user_id`
      argument, `CUSTOMER_NOT_FOUND` (FR-004, NFR-001)
- [x] T07 `app/tickets/repository.py` (FR-005)
- [x] T08 Unit tests: settlement dates and totals, tool schemas without `user_id`, unknown
      merchant, ticket ids

**Checkpoint 2:** `uv run pytest`

## Phase 3: Knowledge

- [x] T09 `app/knowledge/loader.py`: fetch with httpx, clean with BeautifulSoup (FR-008)
- [x] T10 `app/knowledge/vectorstore.py`: split, chunk ids, idempotent ingestion, CLI with
      per-page logs, `--rebuild`, `--if-empty` (FR-008)
- [x] T11 `app/knowledge/tools.py`: `search_knowledge_base`, `web_search` (FR-003, FR-007)
- [x] T12 Unit tests: cleaning keeps `o-modal` answers and drops boilerplate, chunk ids,
      knowledge tool on an in-memory store
**Checkpoint 3:** `uv run pytest`. Do **not** run the ingestion: I run it live after the
build (AC-3)

## Phase 4: Agents and guardrails

- [x] T14 `app/agents/schemas.py`, `prompts.py`, `messages.py`, `llm.py` (FR-002 to FR-006)
- [x] T15 `app/guardrails/pii.py`: PII middleware and trace anonymizer (NFR-002)
- [x] T16 `router_agent.py`, `specialist.py` (Knowledge, Support, source verification),
      `escalation_agent.py` with deterministic fallback (FR-002 to FR-007, NFR-003)

**Checkpoint 4:** `uv run ruff check .` and `uv run pyright`

## Phase 5: Orchestration and API

- [x] T17 `app/orchestration/graph.py`: state, nodes, conditional edges, failure → escalation
      (FR-002, FR-005, NFR-003)
- [x] T18 `app/chat/`: schemas, service (LangSmith trace, `chat.completed` log), routes
      `/v1/chat` and `/health` (FR-001, NFR-004)
- [x] T19 `app/container.py`, `app/main.py`
- [x] T20 `tests/conftest.py` and `tests/integration/test_orchestration.py`: each route,
      handoff, failing specialist, merchant isolation, unverified URLs dropped
- [x] T20b `tests/unit/test_guardrails.py`: the model never receives a card number or CPF
      (the fake model records its prompts); the trace anonymizer masks card, CPF, CVV and
      password in any payload (NFR-002)

**Checkpoint 5:** `uv run pytest`

## Phase 6: Tests and tooling

- [x] T21 `tests/integration/test_scenarios.py`: S1 to S10 parametrized, offline, with the
      scenario id as the pytest id, so `pytest -v` shows `[S1]` to `[S10]` (NFR-007)
- [x] T22 `tests/integration/test_api.py`: envelope, 422, 503, `X-Request-ID`, `/health`
      (FR-001)
- [x] T23 `evals/run_evals.py`: local report and `--langsmith` (NFR-005)
- [x] T24 `scripts/chat.py` (live stream of the graph) and `scripts/check_api_docs.py`
      (NFR-007)
- [x] T25 `tests/MANUAL-TESTS.md`: S1 to S10, G1 to G5, H1, H2, validation, ingestion,
      observability (NFR-007)

**Checkpoint 6:** `uv run pytest`, `uv run python -m scripts.check_api_docs`

## Phase 7: Ship

- [x] T26 Validate the compose file with `docker compose config --quiet`. Do **not** build or
      start the container: I do it live after the build (FR-009, AC-4)
- [x] T27 `README.md` (AC-6)
- [x] T28 Final report. Do **not** run the ingestion, `docker compose up`,
      `scripts/chat.py` or the evals: I run them live after the build (AC-1, AC-3 to AC-5)

**Checkpoint 7:** the definition of done in `INITIAL-PROMPT.md` §10
