"""Settlement rules, totals per deposit date and the Support tools' contract.

These are the rules the model must never compute itself (dry-run lesson 4), so they are
pinned here with a fixed "today".
"""

import json
from datetime import date

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.customers.repository import (
    CustomerRepository,
    add_business_days,
    mask_document,
    next_business_day_on_or_after,
    split_installments,
)
from app.customers.tools import build_customer_tools
from tests.conftest import TODAY, call_tool


def test_debit_settles_next_business_day_and_skips_the_weekend() -> None:
    assert add_business_days(date(2026, 10, 27), 1) == date(2026, 10, 28)  # Tue -> Wed
    assert add_business_days(date(2026, 10, 30), 1) == date(2026, 11, 2)  # Fri -> Mon
    assert add_business_days(date(2026, 10, 31), 1) == date(2026, 11, 2)  # Sat -> Mon


def test_weekend_dates_roll_to_monday() -> None:
    assert next_business_day_on_or_after(date(2026, 12, 27)) == date(2026, 12, 28)  # Sun
    assert next_business_day_on_or_after(date(2026, 11, 27)) == date(2026, 11, 27)  # Fri


def test_installments_are_split_in_cents_with_the_remainder_first() -> None:
    assert [str(p) for p in split_installments(100.0, 3)] == ["33.34", "33.33", "33.33"]
    assert sum(split_installments(360.0, 3)) == 360


def test_yesterday_sales_are_totalled_per_deposit_date(
    customer_repository: CustomerRepository,
) -> None:
    customer = customer_repository.get("cliente1988")
    assert customer is not None

    day = customer_repository.sales_day(customer, days_ago=1)

    assert day.sale_date == date(2026, 10, 27)
    assert (day.approved_count, day.declined_count) == (7, 1)
    assert day.approved_total == 674.55
    totals = {(d.deposit_date, d.total_amount, d.status) for d in day.deposits_by_date}
    assert totals == {
        (date(2026, 10, 27), 47.0, "paid"),  # Pix is instant: 32.00 + 15.00
        (date(2026, 10, 28), 137.65, "due_today"),  # debit +1 business day
        (date(2026, 11, 27), 249.9, "scheduled"),  # credit +31 days: 129.90 + 120.00
        (date(2026, 12, 28), 120.0, "scheduled"),  # +61 days is Sunday -> Monday
        (date(2027, 1, 26), 120.0, "scheduled"),  # third installment, +91 days
    }


def test_declined_sales_generate_no_deposit(customer_repository: CustomerRepository) -> None:
    customer = customer_repository.get("cliente1988")
    assert customer is not None
    day = customer_repository.sales_day(customer, days_ago=1)
    assert "TX-1006" not in {deposit.transaction_id for deposit in day.deposits}


def test_declined_transactions_carry_the_response_code_meaning(
    customer_repository: CustomerRepository,
) -> None:
    customer = customer_repository.get("cliente1988")
    assert customer is not None

    declined = customer_repository.recent_transactions(customer, "declined", limit=10)

    assert [tx.response_code for tx in declined] == ["55", "51", "54"]  # most recent first
    assert declined[0].response_meaning == "Wrong PIN entered."
    assert declined[0].date == TODAY


def test_document_is_masked() -> None:
    assert mask_document("12.345.678/0001-90") == "**.***.***/****-90"


def test_customer_tools_never_expose_user_id(customer_repository: CustomerRepository) -> None:
    tools = build_customer_tools(customer_repository)

    assert {tool.name for tool in tools} == {
        "get_customer_profile",
        "get_sales_and_settlements",
        "get_recent_transactions",
        "get_terminal_status",
    }
    for tool in tools:
        schema = convert_to_openai_tool(tool)  # exactly what the model sees
        properties = set(schema["function"]["parameters"].get("properties", {}))
        assert "user_id" not in properties, tool.name
        assert "runtime" not in properties, tool.name


@pytest.mark.parametrize("name", ["get_customer_profile", "get_terminal_status"])
def test_unknown_merchant_returns_customer_not_found(
    customer_repository: CustomerRepository, name: str
) -> None:
    tool = next(t for t in build_customer_tools(customer_repository) if t.name == name)

    result = json.loads(call_tool(tool, "cliente9999"))

    assert result["error"] == "CUSTOMER_NOT_FOUND"


def test_tools_read_only_the_runtime_merchant(customer_repository: CustomerRepository) -> None:
    tool = next(
        t for t in build_customer_tools(customer_repository) if t.name == "get_terminal_status"
    )

    mine = json.loads(call_tool(tool, "cliente1988"))
    other = json.loads(call_tool(tool, "cliente2000"))

    assert {t["serial"] for t in mine["terminals"]} == {"GSM-2024-000123", "GCL-2023-004567"}
    assert {t["serial"] for t in other["terminals"]} == {"GMN-2025-000777"}


def test_sales_tool_returns_deposits_by_date(customer_repository: CustomerRepository) -> None:
    tool = next(
        t
        for t in build_customer_tools(customer_repository)
        if t.name == "get_sales_and_settlements"
    )

    result = json.loads(call_tool(tool, "cliente1988", days_ago=1))

    assert result["deposits_by_date"][0] == {
        "deposit_date": "2026-10-27",
        "weekday": "Tuesday",
        "total_amount": 47.0,
        "status": "paid",
        "transaction_ids": ["TX-1002", "TX-1007"],
    }


def test_profile_masks_the_document(customer_repository: CustomerRepository) -> None:
    tool = next(
        t for t in build_customer_tools(customer_repository) if t.name == "get_customer_profile"
    )

    profile = json.loads(call_tool(tool, "cliente1988"))

    assert profile["document"] == "**.***.***/****-90"
    assert profile["business_name"] == "Padaria Pão Dourado"
