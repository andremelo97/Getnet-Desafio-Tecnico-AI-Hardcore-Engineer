"""Reads the fictitious merchant data and computes settlement dates and totals.

Dates in `data/customers.json` are relative (`days_ago`), so the data never goes stale.
The settlement rules live here, in code, because the model added deposit amounts wrong
in the dry run: the tools hand it totals per deposit date, never raw sums to do.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from app.customers.schemas import (
    Customer,
    CustomerData,
    Deposit,
    DepositStatus,
    DepositTotal,
    ResponseCode,
    SalesDay,
    SaleView,
    Transaction,
    TransactionView,
)

_CENT = Decimal("0.01")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def today_in(timezone: str) -> Callable[[], date]:
    zone = ZoneInfo(timezone)
    return lambda: datetime.now(zone).date()


def next_business_day_on_or_after(day: date) -> date:
    """Roll a Saturday or Sunday forward to Monday (no holiday calendar yet)."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def add_business_days(day: date, business_days: int) -> date:
    for _ in range(business_days):
        day += timedelta(days=1)
        day = next_business_day_on_or_after(day)
    return day


def split_installments(amount: float, installments: int) -> list[Decimal]:
    """Split in cents; the rounding remainder goes to the first installment."""
    total = Decimal(str(amount)).quantize(_CENT)
    base = (total / installments).quantize(_CENT, rounding=ROUND_DOWN)
    parts = [base] * installments
    parts[0] = total - base * (installments - 1)
    return parts


def deposit_status(deposit_date: date, today: date) -> DepositStatus:
    if deposit_date < today:
        return "paid"
    if deposit_date == today:
        return "due_today"
    return "scheduled"


def mask_document(document: str) -> str:
    """Keep only the last two digits of a CPF or CNPJ document."""
    digit_positions = [i for i, char in enumerate(document) if char.isdigit()]
    keep = set(digit_positions[-2:])
    return "".join(
        "*" if char.isdigit() and i not in keep else char for i, char in enumerate(document)
    )


class CustomerRepository:
    def __init__(self, data_file: Path, today: Callable[[], date]) -> None:
        self._data = CustomerData.model_validate_json(data_file.read_text(encoding="utf-8"))
        self._by_id = {customer.user_id: customer for customer in self._data.customers}
        self._today = today

    def today(self) -> date:
        return self._today()

    def get(self, user_id: str) -> Customer | None:
        return self._by_id.get(user_id)

    def response_code(self, code: str | None) -> ResponseCode | None:
        return self._data.response_codes.get(code) if code else None

    def deposits_for(self, customer: Customer, transaction: Transaction) -> list[Deposit]:
        if transaction.status != "approved":
            return []
        today = self.today()
        terms = customer.settlement_terms
        sale_date = today - timedelta(days=transaction.days_ago)

        if transaction.method == "pix":
            schedule = [(sale_date, Decimal(str(transaction.amount)).quantize(_CENT))]
        elif transaction.method == "debit":
            paid_on = add_business_days(sale_date, terms.debit_business_days)
            schedule = [(paid_on, Decimal(str(transaction.amount)).quantize(_CENT))]
        else:
            parts = split_installments(transaction.amount, transaction.installments)
            schedule = [
                (
                    next_business_day_on_or_after(
                        sale_date
                        + timedelta(
                            days=terms.credit_first_installment_days
                            + index * terms.credit_installment_interval_days
                        )
                    ),
                    part,
                )
                for index, part in enumerate(parts)
            ]

        return [
            Deposit(
                transaction_id=transaction.id,
                method=transaction.method,
                installment=f"{index + 1}/{len(schedule)}",
                deposit_date=paid_on,
                amount=float(amount),
                status=deposit_status(paid_on, today),
            )
            for index, (paid_on, amount) in enumerate(schedule)
        ]

    def sales_day(self, customer: Customer, days_ago: int) -> SalesDay:
        today = self.today()
        day_sales = sorted(
            (tx for tx in customer.transactions if tx.days_ago == days_ago),
            key=lambda tx: tx.time,
        )
        approved = [tx for tx in day_sales if tx.status == "approved"]
        deposits = [deposit for tx in approved for deposit in self.deposits_for(customer, tx)]

        totals: dict[date, tuple[Decimal, list[str]]] = {}
        for deposit in deposits:
            amount, ids = totals.get(deposit.deposit_date, (Decimal(0), []))
            if deposit.transaction_id not in ids:
                ids = [*ids, deposit.transaction_id]
            totals[deposit.deposit_date] = (amount + Decimal(str(deposit.amount)), ids)

        return SalesDay(
            sale_date=today - timedelta(days=days_ago),
            today=today,
            approved_count=len(approved),
            approved_total=_money(sum((Decimal(str(tx.amount)) for tx in approved), Decimal(0))),
            declined_count=len(day_sales) - len(approved),
            sales=[
                SaleView(
                    id=tx.id,
                    time=tx.time,
                    method=tx.method,
                    brand=tx.brand,
                    amount=tx.amount,
                    installments=tx.installments,
                    status=tx.status,
                    response_code=tx.response_code,
                )
                for tx in day_sales
            ],
            deposits=sorted(deposits, key=lambda deposit: deposit.deposit_date),
            deposits_by_date=[
                DepositTotal(
                    deposit_date=paid_on,
                    weekday=_WEEKDAYS[paid_on.weekday()],
                    total_amount=_money(amount),
                    status=deposit_status(paid_on, today),
                    transaction_ids=ids,
                )
                for paid_on, (amount, ids) in sorted(totals.items())
            ],
        )

    def recent_transactions(
        self,
        customer: Customer,
        status: Literal["all", "approved", "declined"],
        limit: int,
    ) -> list[TransactionView]:
        today = self.today()
        selected = [tx for tx in customer.transactions if status in ("all", tx.status)]
        selected.sort(key=lambda tx: (tx.days_ago, _reverse_time(tx.time)))
        views: list[TransactionView] = []
        for tx in selected[:limit]:
            code = self.response_code(tx.response_code)
            views.append(
                TransactionView(
                    id=tx.id,
                    date=today - timedelta(days=tx.days_ago),
                    time=tx.time,
                    method=tx.method,
                    brand=tx.brand,
                    amount=tx.amount,
                    installments=tx.installments,
                    status=tx.status,
                    terminal_serial=tx.terminal_serial,
                    response_code=tx.response_code,
                    response_meaning=code.meaning if code else None,
                    recommended_action=code.recommended_action if code else None,
                )
            )
        return views


def _money(value: Decimal) -> float:
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _reverse_time(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return -(int(hours) * 60 + int(minutes))
