"""PII guardrails: the model never receives a card number or CPF, the answer never returns
one, and the trace anonymizer masks card, CPF, CVV and password in any payload.

The fake models record every prompt, so "the model never saw it" is asserted directly.
"""

from app.guardrails.pii import redact_text, trace_anonymizer
from tests.conftest import answer, build_harness, call, route

CARD = "4111 1111 1111 1111"  # a Luhn-valid test card number
CPF = "123.456.789-09"


def test_detectors_redact_each_type() -> None:
    text = f"cartão {CARD}, CPF {CPF}, CVV: 123, senha: segredo42"

    redacted = redact_text(text)

    assert redacted == (
        "cartão [REDACTED_CREDIT_CARD], CPF [REDACTED_CPF], [REDACTED_CVV], [REDACTED_PASSWORD]"
    )


def test_amounts_dates_and_masked_numbers_are_left_alone() -> None:
    text = "R$ 137,65 em 28/10/2026 (2026-10-28), cartão **** **** **** 1111, TX-1102"

    assert redact_text(text) == text


def test_models_never_receive_the_card_number_or_cpf() -> None:
    harness = build_harness(
        router=[route("support", language="pt")],
        support=[
            call("get_recent_transactions", status="declined", limit=5),
            answer(f"A venda no cartão {CARD} foi negada: saldo insuficiente."),
        ],
    )

    response = harness.chat(f"Minha venda no cartão {CARD} foi negada hoje. Meu CPF é {CPF}.")

    for model in harness.models():
        seen = model.seen_text()
        assert CARD not in seen and "4111111111111111" not in seen
        assert CPF not in seen
    assert "**** **** **** 1111" in harness.router.seen_text()  # masked, not dropped
    assert CARD not in response.answer  # the model echoed it; the code masked it
    assert "[REDACTED_CREDIT_CARD]" in response.answer


def test_escalation_model_never_receives_the_card_number() -> None:
    harness = build_harness(
        router=[route("human", language="pt")], failing=frozenset({"escalation"})
    )

    response = harness.chat(f"Quero um atendente, clonaram meu cartão {CARD}")

    assert CARD not in harness.escalation.seen_text()
    ticket = harness.tickets.get(response.ticket_id or "")
    assert ticket is not None and CARD not in ticket.operator_summary  # fallback summary


def test_trace_anonymizer_masks_nested_payloads() -> None:
    anonymize = trace_anonymizer()
    payload = {
        "inputs": {"message": f"card {CARD} cpf {CPF}"},
        "messages": [{"content": "CVV: 987 and password: hunter2"}],
    }

    masked = anonymize(payload)

    assert masked == {
        "inputs": {"message": "card [REDACTED_CREDIT_CARD] cpf [REDACTED_CPF]"},
        "messages": [{"content": "[REDACTED_CVV] and [REDACTED_PASSWORD]"}],
    }
