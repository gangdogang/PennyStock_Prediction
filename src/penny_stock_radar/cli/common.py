from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from ..services.broker_execution import BrokerExecutionService

console = Console()

TRADE_CALL_MAP = {
    "OPENING_RANGE_CANDIDATE": "시초 후보",
    "CONDITIONAL_ENTRY": "조건부 진입",
    "NEWS_CHECK_FIRST": "재료 확인 전",
    "WAIT_PULLBACK": "눌림 대기",
    "NO_CHASE": "추격 금지",
}


def format_optional_number(value: object, precision: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def format_optional_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint


def trade_call_label(value: str | None) -> str:
    if not value:
        return "-"
    return TRADE_CALL_MAP.get(value, value)


def require_broker_execution_service() -> BrokerExecutionService:
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    service = root_cli.BrokerExecutionService(settings)
    if not getattr(service.adapter, "is_available", lambda: False)():
        reason = getattr(service.adapter, "reason", "Broker adapter is not available.")
        console.print(str(reason))
        raise typer.Exit(code=1)
    return service


def broker_order_table(rows: list[object], *, title: str) -> Table:
    table = Table(title=title)
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Qty")
    table.add_column("Filled")
    table.add_column("Limit")
    table.add_column("Status")
    table.add_column("Broker ID")
    table.add_column("Client ID")
    table.add_column("Updated")
    for row in rows:
        table.add_row(
            str(getattr(row, "symbol", "-")),
            str(getattr(row, "side", "-")),
            str(getattr(row, "quantity", "-")),
            str(getattr(row, "filled_quantity", "-")),
            format_optional_number(getattr(row, "limit_price", None), 4),
            str(getattr(row, "status", "-")),
            str(getattr(row, "broker_order_id", None) or "-"),
            str(getattr(row, "client_order_id", "-")),
            getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else "-",
        )
    return table


def broker_position_table(rows: list[object]) -> Table:
    table = Table(title="Broker Positions")
    table.add_column("Symbol")
    table.add_column("Qty")
    table.add_column("Avail")
    table.add_column("Avg")
    table.add_column("Last")
    table.add_column("Value")
    table.add_column("Currency")
    for row in rows:
        table.add_row(
            str(getattr(row, "symbol", "-")),
            str(getattr(row, "quantity", "-")),
            str(getattr(row, "available_quantity", None) or "-"),
            format_optional_number(getattr(row, "average_price", None), 4),
            format_optional_number(getattr(row, "market_price", None), 4),
            format_optional_number(getattr(row, "market_value", None), 2),
            str(getattr(row, "currency", "USD")),
        )
    return table


def broker_account_table(row: object) -> Table:
    table = Table(title="Broker Account")
    table.add_column("Broker")
    table.add_column("Account")
    table.add_column("Cash")
    table.add_column("Buying Power")
    table.add_column("Equity")
    table.add_column("Currency")
    table.add_column("Updated")
    table.add_row(
        str(getattr(row, "broker_name", "-")),
        str(getattr(row, "account_id", "-")),
        format_optional_number(getattr(row, "cash_balance", None), 2),
        format_optional_number(getattr(row, "buying_power", None), 2),
        format_optional_number(getattr(row, "total_equity", None), 2),
        str(getattr(row, "currency", "USD")),
        getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else "-",
    )
    return table


def broker_paper_comparison_table(rows: list[object]) -> Table:
    table = Table(title="Broker vs Paper")
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Exec Qty")
    table.add_column("Paper Qty")
    table.add_column("Qty Δ")
    table.add_column("Exec Px")
    table.add_column("Paper Px")
    table.add_column("Px Δ")
    table.add_column("Exec Status")
    table.add_column("Paper ID")
    for row in rows:
        table.add_row(
            str(getattr(row, "symbol", "-")),
            str(getattr(row, "side", "-")),
            str(getattr(row, "execution_quantity", "-")),
            str(getattr(row, "paper_quantity", None) or "-"),
            str(getattr(row, "quantity_delta", None) or "-"),
            format_optional_number(getattr(row, "execution_limit_price", None), 4),
            format_optional_number(getattr(row, "paper_price", None), 4),
            format_optional_number(getattr(row, "price_delta", None), 4),
            str(getattr(row, "execution_status", "-")),
            str(getattr(row, "paper_order_id", None) or "-"),
        )
    return table
