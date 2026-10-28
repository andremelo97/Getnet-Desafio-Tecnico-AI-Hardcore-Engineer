"""Typed shapes of the merchant data and of what the Support tools return.

The input models mirror `data/customers.json`; the output models are the views the tools
serialize for the model, with every date and total already computed.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Method = Literal["debit", "credit", "pix"]
TransactionStatus = Literal["approved", "declined"]
DepositStatus = Literal["paid", "due_today", "scheduled"]


@dataclass(frozen=True)
class CustomerContext:
    """Runtime context of the Support agent: whose data the tools may read.

    Filled by the service from the request, never by the model.
    """

    user_id: str


class SettlementTerms(BaseModel):
    debit_business_days: int = 1
    credit_first_installment_days: int = 31
    credit_installment_interval_days: int = 30
    pix_instant: bool = True


class ReceivablesAdvance(BaseModel):
    automatic: bool
    eligible: bool


class BankAccount(BaseModel):
    bank: str
    branch: str
    account: str


class Terminal(BaseModel):
    serial: str
    model: str
    connectivity: str
    status: str
    last_seen_hours_ago: int
    last_error: str | None = None


class Transaction(BaseModel):
    id: str
    days_ago: int
    time: str
    method: Method
    brand: str | None = None
    amount: float
    installments: int = 1
    status: TransactionStatus
    response_code: str | None = None
    terminal_serial: str


class Customer(BaseModel):
    user_id: str
    business_name: str
    document: str
    city: str
    state: str
    customer_since: date
    settlement_terms: SettlementTerms
    receivables_advance: ReceivablesAdvance
    bank_account: BankAccount
    terminals: list[Terminal]
    transactions: list[Transaction]


class ResponseCode(BaseModel):
    meaning: str
    recommended_action: str


class CustomerData(BaseModel):
    customers: list[Customer]
    response_codes: dict[str, ResponseCode]


class Deposit(BaseModel):
    """One deposit of one sale (a credit sale in installments has several)."""

    transaction_id: str
    method: Method
    installment: str
    deposit_date: date
    amount: float
    status: DepositStatus


class DepositTotal(BaseModel):
    """All deposits that fall on the same date, totalled in code."""

    deposit_date: date
    weekday: str
    total_amount: float
    status: DepositStatus
    transaction_ids: list[str]


class SaleView(BaseModel):
    id: str
    time: str
    method: Method
    brand: str | None
    amount: float
    installments: int
    status: TransactionStatus
    response_code: str | None = None


class SalesDay(BaseModel):
    sale_date: date
    today: date
    approved_count: int
    approved_total: float
    declined_count: int
    sales: list[SaleView]
    deposits: list[Deposit]
    deposits_by_date: list[DepositTotal] = Field(default_factory=list[DepositTotal])


class TransactionView(BaseModel):
    id: str
    date: date
    time: str
    method: Method
    brand: str | None
    amount: float
    installments: int
    status: TransactionStatus
    terminal_serial: str
    response_code: str | None = None
    response_meaning: str | None = None
    recommended_action: str | None = None
