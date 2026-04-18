from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


def broker_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BrokerOrderRequest(BaseModel):
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    limit_price: float
    exchange_code: str | None = None
    order_type: str = "limit"
    intent: str = ""
    strategy_bucket: str = ""
    market_phase: str = ""
    created_at: datetime = Field(default_factory=broker_utc_now)


class BrokerReplaceRequest(BaseModel):
    client_order_id: str
    new_quantity: int | None = None
    new_limit_price: float | None = None
    created_at: datetime = Field(default_factory=broker_utc_now)


class BrokerOrder(BaseModel):
    client_order_id: str
    broker_name: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int = 0
    remaining_quantity: int = 0
    limit_price: float | None = None
    avg_fill_price: float | None = None
    exchange_code: str = ""
    status: str = "PENDING"
    intent: str = ""
    strategy_bucket: str = ""
    market_phase: str = ""
    order_type: str = "limit"
    broker_order_id: str | None = None
    original_broker_order_id: str | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_payload: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=broker_utc_now)
    updated_at: datetime = Field(default_factory=broker_utc_now)


class BrokerPosition(BaseModel):
    broker_name: str
    account_id: str
    symbol: str
    exchange_code: str = ""
    quantity: int
    available_quantity: int | None = None
    average_price: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    currency: str = "USD"
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=broker_utc_now)


class BrokerAccount(BaseModel):
    broker_name: str
    account_id: str
    currency: str = "USD"
    cash_balance: float | None = None
    buying_power: float | None = None
    total_equity: float | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=broker_utc_now)


class BrokerAdapter(Protocol):
    adapter_name: str
    account_id: str

    def is_available(self) -> bool: ...

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder: ...

    def replace_order(
        self,
        order: BrokerOrder,
        request: BrokerReplaceRequest,
    ) -> BrokerOrder: ...

    def cancel_order(
        self,
        order: BrokerOrder,
        *,
        client_order_id: str,
    ) -> BrokerOrder: ...

    def fetch_open_orders(self) -> list[BrokerOrder]: ...

    def fetch_fills(self, *, market_date: str | None = None) -> list[BrokerOrder]: ...

    def fetch_positions(self) -> list[BrokerPosition]: ...

    def fetch_account(self) -> BrokerAccount: ...


class NullBrokerAdapter:
    adapter_name = "null"
    account_id = ""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        raise RuntimeError(self.reason)

    def replace_order(
        self,
        order: BrokerOrder,
        request: BrokerReplaceRequest,
    ) -> BrokerOrder:
        raise RuntimeError(self.reason)

    def cancel_order(
        self,
        order: BrokerOrder,
        *,
        client_order_id: str,
    ) -> BrokerOrder:
        raise RuntimeError(self.reason)

    def fetch_open_orders(self) -> list[BrokerOrder]:
        return []

    def fetch_fills(self, *, market_date: str | None = None) -> list[BrokerOrder]:
        return []

    def fetch_positions(self) -> list[BrokerPosition]:
        return []

    def fetch_account(self) -> BrokerAccount:
        raise RuntimeError(self.reason)
