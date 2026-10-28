"""Catalog of the API's response codes and their human-readable messages.

The backend speaks in stable English codes; clients switch on `meta.code`, and the text in
`meta.message` always comes from this one table.
"""

from enum import StrEnum


class Code(StrEnum):
    CHAT_ANSWERED = "CHAT_ANSWERED"
    HEALTH_OK = "HEALTH_OK"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


MESSAGES: dict[Code, str] = {
    Code.CHAT_ANSWERED: "The message was answered.",
    Code.HEALTH_OK: "The service is healthy.",
    Code.VALIDATION_ERROR: "The request body is invalid.",
    Code.AGENT_UNAVAILABLE: "The assistant is temporarily unavailable. Please try again.",
    Code.NOT_FOUND: "The requested resource does not exist.",
    Code.INTERNAL_ERROR: "An unexpected error occurred.",
}


def message_for(code: Code) -> str:
    return MESSAGES[code]
