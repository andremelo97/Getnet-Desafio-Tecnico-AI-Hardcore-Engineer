"""FastAPI application factory: lifespan, request id middleware and error handlers.

`create_app()` builds the real services at startup; tests call `create_app(services)` with
services built on fake models, so the whole HTTP stack runs offline.
"""

import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.chat.router import router
from app.container import Services, build_services
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.settings import get_settings

REQUEST_ID_HEADER = "X-Request-ID"
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if services is None:
            settings = get_settings()
            configure_logging(settings.log_level)
            app.state.services = build_services(settings)
        else:
            app.state.services = services
        yield

    app = FastAPI(
        title="Getnet multi-agent support",
        version="1.0.0",
        description=(
            "One endpoint for Getnet merchants. A Router agent sends each message to the "
            "Knowledge agent (RAG over Getnet's website and web search), the Customer Support "
            "agent (the merchant's own data) or the Human Escalation agent (handoff ticket). "
            "Every response uses the envelope `{data, meta: {code, message}}`."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id(  # pyright: ignore[reportUnusedFunction]  # registered by decorator
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request.state.request_id = (
            incoming if VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
