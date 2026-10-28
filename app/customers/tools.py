"""The four Customer Support tools, bound to the merchant of the current request.

No tool takes a `user_id` argument: each reads it from `ToolRuntime[CustomerContext]`
which the service fills from the request. The model can choose what to look up, never
whose data to read. Every tool returns a JSON string.
"""

import json
from typing import Annotated, Literal

from langchain.tools import BaseTool, ToolRuntime, tool
from pydantic import BaseModel

from app.customers.repository import CustomerRepository, mask_document
from app.customers.schemas import Customer, CustomerContext

CUSTOMER_NOT_FOUND = json.dumps(
    {"error": "CUSTOMER_NOT_FOUND", "detail": "No merchant account is linked to this user."}
)
MAX_DAYS_AGO = 90
MAX_TRANSACTIONS = 50


def _dump(model: BaseModel | list[BaseModel] | dict[str, object]) -> str:
    if isinstance(model, BaseModel):
        return model.model_dump_json()
    if isinstance(model, list):
        return json.dumps([item.model_dump(mode="json") for item in model], ensure_ascii=False)
    return json.dumps(model, ensure_ascii=False, default=str)


def build_customer_tools(repository: CustomerRepository) -> list[BaseTool]:
    def current_customer(
        runtime: ToolRuntime[CustomerContext, dict[str, object]],
    ) -> Customer | None:
        return repository.get(runtime.context.user_id)

    @tool
    def get_customer_profile(runtime: ToolRuntime[CustomerContext, dict[str, object]]) -> str:
        """Get the merchant's profile: business name, city, customer since, settlement terms,
        receivables advance settings, bank account (masked) and number of card machines."""
        customer = current_customer(runtime)
        if customer is None:
            return CUSTOMER_NOT_FOUND
        return _dump(
            {
                "business_name": customer.business_name,
                "document": mask_document(customer.document),
                "city": customer.city,
                "state": customer.state,
                "customer_since": customer.customer_since.isoformat(),
                "settlement_terms": customer.settlement_terms.model_dump(),
                "receivables_advance": customer.receivables_advance.model_dump(),
                "bank_account": customer.bank_account.model_dump(),
                "card_machines": len(customer.terminals),
            }
        )

    @tool
    def get_sales_and_settlements(
        runtime: ToolRuntime[CustomerContext, dict[str, object]],
        days_ago: Annotated[int, "Which day of sales: 0 = today, 1 = yesterday, and so on."] = 1,
    ) -> str:
        """Get the merchant's sales of one day and when each will be deposited.

        `deposits_by_date` already holds the total per deposit date, computed by the system:
        quote those totals as they are and never add amounts yourself. Statuses are `paid`,
        `due_today` or `scheduled`, relative to today."""
        customer = current_customer(runtime)
        if customer is None:
            return CUSTOMER_NOT_FOUND
        days_ago = min(max(days_ago, 0), MAX_DAYS_AGO)
        return _dump(repository.sales_day(customer, days_ago))

    @tool
    def get_recent_transactions(
        runtime: ToolRuntime[CustomerContext, dict[str, object]],
        status: Annotated[
            Literal["all", "approved", "declined"],
            "Filter by status. Use 'declined' to investigate a decline error.",
        ] = "all",
        limit: Annotated[int, "Maximum number of transactions, most recent first."] = 10,
    ) -> str:
        """Get the merchant's most recent card and Pix transactions. Declined transactions
        include the issuer response code, its meaning and the recommended action."""
        customer = current_customer(runtime)
        if customer is None:
            return CUSTOMER_NOT_FOUND
        limit = min(max(limit, 1), MAX_TRANSACTIONS)
        transactions = repository.recent_transactions(customer, status, limit)
        return _dump(
            {
                "today": repository.today().isoformat(),
                "status_filter": status,
                "transactions": [tx.model_dump(mode="json") for tx in transactions],
            }
        )

    @tool
    def get_terminal_status(runtime: ToolRuntime[CustomerContext, dict[str, object]]) -> str:
        """Get the merchant's card machines: model, serial, connectivity (Wi-Fi or chip),
        online or offline, hours since last seen and the last error code shown."""
        customer = current_customer(runtime)
        if customer is None:
            return CUSTOMER_NOT_FOUND
        return _dump({"terminals": [t.model_dump() for t in customer.terminals]})

    return [
        get_customer_profile,
        get_sales_and_settlements,
        get_recent_transactions,
        get_terminal_status,
    ]
