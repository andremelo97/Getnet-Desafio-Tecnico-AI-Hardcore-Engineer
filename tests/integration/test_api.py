"""The HTTP contract: envelope, validation (422), unavailability (503), `X-Request-ID`,
`/health`, 404, and one end-to-end request through the real graph on fake models.
"""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.chat.schemas import ChatRequest, ChatResponse
from app.chat.service import ChatService
from app.container import Services
from app.core.errors import AgentUnavailableError
from app.main import create_app
from tests.conftest import answer, build_harness, call, route


class FakeChatService(ChatService):
    """Answers instantly, or fails as if the graph could not run."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable

    async def chat(self, request: ChatRequest, request_id: str) -> ChatResponse:
        if self.unavailable:
            raise AgentUnavailableError
        return ChatResponse(
            answer=f"echo: {request.message}",
            route="knowledge",
            route_reason="fake",
            agents=["router", "knowledge"],
            tools_used=["search_knowledge_base"],
            sources=[],
            escalated=False,
            ticket_id=None,
            language="en",
            request_id=request_id,
            latency_ms=1,
        )


def make_client(chat: ChatService, chunks: int = 135) -> TestClient:
    services = Services(chat=chat, graph=None, knowledge_base_chunks=lambda: chunks)
    return TestClient(create_app(services))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client(FakeChatService()) as test_client:
        yield test_client


BODY = {"message": "How does Pix work?", "user_id": "cliente1988"}


def test_chat_returns_the_envelope_and_a_generated_request_id(client: TestClient) -> None:
    response = client.post("/v1/chat", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {"code": "CHAT_ANSWERED", "message": "The message was answered."}
    assert body["data"]["answer"] == "echo: How does Pix work?"
    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert body["data"]["request_id"] == request_id


def test_the_caller_request_id_is_kept(client: TestClient) -> None:
    response = client.post("/v1/chat", json=BODY, headers={"X-Request-ID": "demo-42"})

    assert response.headers["X-Request-ID"] == "demo-42"
    assert response.json()["data"]["request_id"] == "demo-42"


def test_an_unsafe_request_id_is_replaced(client: TestClient) -> None:
    response = client.post("/v1/chat", json=BODY, headers={"X-Request-ID": "bad id;drop"})

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


@pytest.mark.parametrize(
    "body",
    [
        {"message": "   ", "user_id": "cliente1988"},
        {"message": "", "user_id": "cliente1988"},
        {"message": "x" * 2001, "user_id": "cliente1988"},
        {"message": "hi", "user_id": "bad id!"},
        {"message": "hi", "user_id": ""},
        {"message": "hi"},
    ],
    ids=["blank", "empty", "too-long", "invalid-user-id", "empty-user-id", "missing-user-id"],
)
def test_invalid_bodies_return_422_in_the_envelope(
    client: TestClient, body: dict[str, str]
) -> None:
    response = client.post("/v1/chat", json=body)

    assert response.status_code == 422
    assert response.json()["data"] is None
    assert response.json()["meta"]["code"] == "VALIDATION_ERROR"
    assert "X-Request-ID" in response.headers


def test_unavailable_agents_return_503() -> None:
    with make_client(FakeChatService(unavailable=True)) as client:
        response = client.post("/v1/chat", json=BODY)

    assert response.status_code == 503
    assert response.json() == {
        "data": None,
        "meta": {
            "code": "AGENT_UNAVAILABLE",
            "message": "The assistant is temporarily unavailable. Please try again.",
        },
    }


def test_health_reports_the_chunk_count(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["meta"]["code"] == "HEALTH_OK"
    assert response.json()["data"] == {"status": "ok", "knowledge_base_chunks": 135}


def test_health_is_degraded_with_an_empty_knowledge_base() -> None:
    with make_client(FakeChatService(), chunks=0) as client:
        assert client.get("/health").json()["data"]["status"] == "degraded"


def test_unknown_routes_return_404_in_the_envelope(client: TestClient) -> None:
    response = client.get("/v1/nothing")

    assert response.status_code == 404
    assert response.json() == {
        "data": None,
        "meta": {"code": "NOT_FOUND", "message": "The requested resource does not exist."},
    }


def test_end_to_end_through_the_real_graph() -> None:
    harness = build_harness(
        router=[route("support")],
        support=[call("get_terminal_status"), answer("Your Get Smart is offline.")],
    )

    with make_client(ChatService(harness.graph)) as client:
        response = client.post(
            "/v1/chat",
            json={"message": "My machine is offline", "user_id": "cliente1988"},
            headers={"X-Request-ID": "e2e-1"},
        )

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["route"] == "support"
    assert data["agents"] == ["router", "support"]
    assert data["tools_used"] == ["get_terminal_status"]
    assert data["answer"] == "Your Get Smart is offline."
    assert data["request_id"] == "e2e-1"
    assert data["escalated"] is False and data["ticket_id"] is None
    assert isinstance(data["latency_ms"], int)
