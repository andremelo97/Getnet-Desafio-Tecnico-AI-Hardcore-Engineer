# Getnet multi-agent support: API image.
# Stage 1 resolves the locked dependencies with uv; stage 2 runs them as a non-root user.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app ./app
COPY --chown=app:app data ./data
RUN mkdir -p /app/.vectorstore && chown app:app /app/.vectorstore
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

# Ingest the knowledge base on first start (skipped when the volume already has chunks),
# then serve the API.
CMD ["sh", "-c", "python -m app.knowledge.vectorstore --if-empty && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
