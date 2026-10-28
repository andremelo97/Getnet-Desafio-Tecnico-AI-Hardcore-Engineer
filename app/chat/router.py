"""HTTP routes: `POST /v1/chat` and `GET /health`, both wrapped in the envelope.

The routes only translate HTTP to the chat service and back; the request id comes from
the middleware in `app.main`, which takes it from `X-Request-ID` or generates one.
"""

from fastapi import APIRouter, Request

from app.chat.schemas import ChatRequest, ChatResponse, HealthResponse
from app.container import Services
from app.core.envelope import Envelope, ok
from app.core.errors import AgentUnavailableError
from app.core.messages import Code

router = APIRouter()

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    422: {"model": Envelope[None], "description": "Invalid body (`VALIDATION_ERROR`)."},
    503: {"model": Envelope[None], "description": "The agents are unavailable."},
    500: {"model": Envelope[None], "description": "Unexpected error (`INTERNAL_ERROR`)."},
}


def get_services(request: Request) -> Services:
    services: Services | None = getattr(request.app.state, "services", None)
    if services is None:
        raise AgentUnavailableError
    return services


@router.post(
    "/v1/chat",
    tags=["chat"],
    summary="Send a merchant message to the multi-agent assistant",
    description=(
        "The Router classifies the message (knowledge, support, human or unsupported) and the "
        "graph runs the matching agent: Knowledge (RAG over Getnet's website plus web search), "
        "Customer Support (the merchant's own data) or Human Escalation (opens a ticket). "
        "Send `X-Request-ID` to correlate the call with its log line and LangSmith trace; "
        "one is generated otherwise and returned in the header and in the body."
    ),
    response_model=Envelope[ChatResponse],
    responses=ERROR_RESPONSES,
)
async def chat(body: ChatRequest, request: Request) -> Envelope[ChatResponse]:
    services = get_services(request)
    response = await services.chat.chat(body, request_id=request.state.request_id)
    return ok(response, Code.CHAT_ANSWERED)


@router.get(
    "/health",
    tags=["health"],
    summary="Service health and knowledge base size",
    description=(
        "Returns `ok` and the number of chunks in the knowledge base, or `degraded` when the "
        "knowledge base is empty (ingestion has not run). Used by the Docker HEALTHCHECK."
    ),
    response_model=Envelope[HealthResponse],
    responses={503: ERROR_RESPONSES[503]},
)
async def health(request: Request) -> Envelope[HealthResponse]:
    chunks = get_services(request).knowledge_base_chunks()
    status = "ok" if chunks > 0 else "degraded"
    return ok(HealthResponse(status=status, knowledge_base_chunks=chunks), Code.HEALTH_OK)
