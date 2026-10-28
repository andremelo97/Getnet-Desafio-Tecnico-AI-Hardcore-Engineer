"""Every error converted to the envelope, so no response leaves without `{data, meta}`.

`AgentUnavailableError` is the one domain error the API knows: the graph itself could not
run (model provider down, misconfiguration), which maps to 503.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.envelope import error_body
from app.core.logging import log_event
from app.core.messages import Code


class AgentUnavailableError(Exception):
    """The orchestration could not produce an answer at all."""


def _validation_message(exc: RequestValidationError) -> str:
    details = [
        f"{'.'.join(str(part) for part in err.get('loc', ()))}: {err.get('msg', '')}"
        for err in exc.errors()
    ]
    return "The request body is invalid. " + "; ".join(details)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(
            status_code=422,
            content=error_body(Code.VALIDATION_ERROR, _validation_message(exc)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        code = Code.NOT_FOUND if exc.status_code == 404 else Code.INTERNAL_ERROR
        return JSONResponse(status_code=exc.status_code, content=error_body(code))

    @app.exception_handler(AgentUnavailableError)
    async def _unavailable(_: Request, __: AgentUnavailableError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse(status_code=503, content=error_body(Code.AGENT_UNAVAILABLE))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log_event("http.unhandled_error", path=request.url.path, error=type(exc).__name__)
        return JSONResponse(status_code=500, content=error_body(Code.INTERNAL_ERROR))
