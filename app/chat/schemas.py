"""The public API contract: what `POST /v1/chat` accepts and returns, and `/health`.

These models generate the OpenAPI schema shown in Swagger (`/docs`), so the documentation
and the validation are the same code.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.schemas import Route, SourceRef


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "When will the money from yesterday's sales be deposited?",
                    "user_id": "cliente1988",
                }
            ]
        }
    )

    message: str = Field(
        min_length=1, max_length=2000, description="The merchant's message, 1 to 2000 chars."
    )
    user_id: str = Field(
        pattern=r"^[A-Za-z0-9_.-]{1,64}$",
        description="The merchant's id: letters, digits, '_', '.' or '-' (up to 64).",
    )

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value.strip()


class ChatResponse(BaseModel):
    answer: str = Field(description="The answer to the merchant, in the merchant's language.")
    route: Route = Field(description="The Router's decision.")
    route_reason: str = Field(description="Why the Router chose this route.")
    agents: list[str] = Field(description="The agents that ran, in order.")
    tools_used: list[str] = Field(description="The tools the agents called, in order.")
    sources: list[SourceRef] = Field(description="Verified sources: URLs a tool returned.")
    escalated: bool = Field(description="True when a handoff ticket was opened.")
    ticket_id: str | None = Field(description="The handoff ticket, when escalated.")
    language: str = Field(description="ISO 639-1 code of the merchant's language.")
    request_id: str = Field(description="Correlates this response, its log line and trace.")
    latency_ms: int = Field(description="Server-side processing time in milliseconds.")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="`ok`, or `degraded` when the knowledge base is empty."
    )
    knowledge_base_chunks: int = Field(description="Chunks in the vector store.")
