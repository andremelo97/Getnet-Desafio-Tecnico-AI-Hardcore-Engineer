"""PII guardrails: LangChain `PIIMiddleware` before and after the model, and a LangSmith
anonymizer for the traces.

Card numbers (Luhn-checked) are masked; CPF, CVV and passwords are redacted. The same
patterns mask the traces and the final answer, so no layer sends them anywhere.
"""

import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, PIIMiddleware
from langsmith import Client
from langsmith.anonymizer import StringNodeRule, create_anonymizer
from langsmith.utils import tracing_is_enabled  # pyright: ignore[reportUnknownVariableType]  # untyped ctx parameter

CPF_PATTERN = r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"
CVV_PATTERN = r"(?i)\b(?:cvv|cvc|c[oó]digo de seguran[cç]a)\s*[:=]?\s*\d{3,4}\b"
PASSWORD_PATTERN = r"(?i)\b(?:senha|password)\s*(?:[:=]|é|is)\s*\S+"
CARD_PATTERN = r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"  # 13 to 19 digits, for traces and text

CUSTOM_DETECTORS = {"cpf": CPF_PATTERN, "cvv": CVV_PATTERN, "password": PASSWORD_PATTERN}
REDACTION_RULES = [
    (re.compile(pattern), f"[REDACTED_{name.upper()}]")
    for name, pattern in {"credit_card": CARD_PATTERN, **CUSTOM_DETECTORS}.items()
]


def pii_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Mask card numbers and redact CPF, CVV and passwords, on input and on output."""
    return [
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True, apply_to_output=True),
        *(
            PIIMiddleware(
                name,
                detector=pattern,
                strategy="redact",
                apply_to_input=True,
                apply_to_output=True,
            )
            for name, pattern in CUSTOM_DETECTORS.items()
        ),
    ]


def redact_text(text: str) -> str:
    """The same patterns, applied in code: a last line of defence for answers and logs."""
    for pattern, replacement in REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


def trace_anonymizer() -> Callable[[Any], Any]:
    rules = [
        StringNodeRule(pattern=pattern, replace=replace) for pattern, replace in REDACTION_RULES
    ]
    return create_anonymizer(rules)


def anonymized_tracing_client() -> Client | None:
    """A LangSmith client that masks PII in every trace, or None when tracing is off."""
    if not tracing_is_enabled():
        return None
    return Client(anonymizer=trace_anonymizer())
