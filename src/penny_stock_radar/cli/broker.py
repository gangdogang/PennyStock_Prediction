from __future__ import annotations

import typer
from rich.table import Table

from .common import (
    broker_account_table,
    broker_order_table,
    broker_paper_comparison_table,
    broker_position_table,
    require_broker_execution_service,
    console,
)

app = typer.Typer()


@app.command("trade-plan")
def trade_plan(
    phase: str = typer.Option(
        "auto",
        help="Which market phase to evaluate for the execution plan.",
    ),
) -> None:
    """Build a live execution plan for semi-auto intraday trading."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    service = root_cli.TradePlanService(settings)
    result = service.generate(phase=phase, export=True)

    summary = Table(title="Live Trade Plan")
    summary.add_column("Phase")
    summary.add_column("Regime")
    summary.add_column("Daily Lock")
    summary.add_column("Day PnL")
    summary.add_column("Open Risk")
    summary.add_row(
        result.market_phase,
        result.regime,
        "yes" if result.daily_loss_locked else "no",
        f"{result.current_day_pnl:.2f}",
        f"{result.current_open_risk:.2f}",
    )

    queue = Table(title="Top Plan Candidates")
    queue.add_column("Symbol")
    queue.add_column("Bucket")
    queue.add_column("Actionability")
    queue.add_column("Ref")
    queue.add_column("Stop")
    queue.add_column("Size")
    queue.add_column("Risk$")
    queue.add_column("Blockers")
    for row in result.candidates[:12]:
        queue.add_row(
            row.symbol,
            row.bucket,
            row.actionability,
            f"{row.entry_reference:.4f}" if row.entry_reference is not None else "-",
            f"{row.stop:.4f}" if row.stop is not None else "-",
            str(row.suggested_size),
            f"{row.max_dollar_risk:.2f}" if row.max_dollar_risk is not None else "-",
            ", ".join(row.blockers[:2]) if row.blockers else "-",
        )

    console.print(summary)
    console.print(queue)
    console.print(f"Plan CSV written to [bold]{result.csv_path}[/bold]")
    console.print(f"Checklist written to [bold]{result.checklist_path}[/bold]")


@app.command("broker-submit-candidate")
def broker_submit_candidate(
    symbol: str = typer.Option(..., help="Symbol to submit from the latest trade plan."),
    phase: str = typer.Option(
        "auto",
        help="Which market phase to evaluate for the trade plan.",
    ),
) -> None:
    """Submit one actionable trade-plan candidate through the configured broker adapter."""
    service = require_broker_execution_service()
    try:
        order = service.submit_trade_plan_candidate(symbol=symbol, phase=phase)
    except (RuntimeError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(broker_order_table([order], title="Broker Candidate Submission"))


@app.command("broker-submit-order")
def broker_submit_order(
    symbol: str = typer.Option(..., help="Symbol to submit."),
    side: str = typer.Option(..., help="Order side: buy or sell."),
    quantity: int = typer.Option(..., min=1, help="Order quantity."),
    limit_price: float = typer.Option(..., help="Limit price."),
    exchange_code: str | None = typer.Option(
        None,
        help="Optional exchange code override such as NASD, NYSE, or AMEX.",
    ),
    intent: str = typer.Option("MANUAL", help="Order intent label."),
    strategy_bucket: str = typer.Option("manual", help="Strategy bucket label."),
    market_phase: str = typer.Option("manual", help="Market phase label."),
) -> None:
    """Submit a manual broker order without touching the paper engine."""
    normalized_side = side.strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise typer.BadParameter("Expected side to be buy or sell.")
    service = require_broker_execution_service()
    try:
        order = service.submit_manual_order(
            symbol=symbol,
            side=normalized_side,
            quantity=quantity,
            limit_price=limit_price,
            exchange_code=exchange_code,
            intent=intent,
            strategy_bucket=strategy_bucket,
            market_phase=market_phase,
        )
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(broker_order_table([order], title="Broker Manual Submission"))


@app.command("broker-replace-order")
def broker_replace_order(
    client_order_id: str = typer.Option(..., help="Existing client order id to replace."),
    quantity: int | None = typer.Option(None, min=1, help="Optional replacement quantity."),
    limit_price: float | None = typer.Option(None, help="Optional replacement limit price."),
) -> None:
    """Replace a previously submitted broker order."""
    if quantity is None and limit_price is None:
        raise typer.BadParameter("Pass at least one of --quantity or --limit-price.")
    service = require_broker_execution_service()
    try:
        order = service.replace_order(
            client_order_id=client_order_id,
            quantity=quantity,
            limit_price=limit_price,
        )
    except (RuntimeError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(broker_order_table([order], title="Broker Replace Result"))


@app.command("broker-cancel-order")
def broker_cancel_order(
    client_order_id: str = typer.Option(..., help="Existing client order id to cancel."),
) -> None:
    """Cancel a previously submitted broker order."""
    service = require_broker_execution_service()
    try:
        order = service.cancel_order(client_order_id=client_order_id)
    except (RuntimeError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(broker_order_table([order], title="Broker Cancel Result"))


@app.command("broker-show-orders")
def broker_show_orders(
    limit: int = typer.Option(20, help="Rows to display."),
    status: str | None = typer.Option(
        None,
        help="Optional status filter against locally stored execution orders.",
    ),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Refresh open orders from the broker before showing local rows.",
    ),
) -> None:
    """Refresh and show the latest broker open-order state."""
    service = require_broker_execution_service()
    try:
        if refresh:
            service.refresh_open_orders()
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    rows = service.latest_orders(limit=limit, status=status)
    if not rows:
        console.print("No broker execution orders found.")
        raise typer.Exit(code=1)
    console.print(broker_order_table(rows, title="Broker Execution Orders"))


@app.command("broker-show-fills")
def broker_show_fills(
    market_date: str | None = typer.Option(
        None,
        help="Optional market date in YYYYMMDD for broker fill refresh.",
    ),
    limit: int = typer.Option(20, help="Rows to display."),
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Refresh fills from the broker before showing local rows.",
    ),
) -> None:
    """Refresh and show the latest broker fills."""
    import penny_stock_radar.cli as root_cli

    service = require_broker_execution_service()
    try:
        if refresh:
            service.refresh_fills(market_date=market_date)
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    rows = root_cli.fetch_execution_orders(
        service.settings.database_path,
        broker_name=getattr(service.adapter, "adapter_name", None),
        account_id=getattr(service.adapter, "account_id", None),
        status="FILLED",
        limit=limit,
    )
    if not rows:
        console.print("No broker fills found.")
        raise typer.Exit(code=1)
    console.print(broker_order_table(rows, title="Broker Fills"))


@app.command("broker-show-balance")
def broker_show_balance(
    refresh: bool = typer.Option(
        True,
        "--refresh/--no-refresh",
        help="Refresh positions and account snapshot from the broker before showing local rows.",
    ),
) -> None:
    """Refresh and show broker account and position snapshots."""
    import penny_stock_radar.cli as root_cli

    service = require_broker_execution_service()
    try:
        if refresh:
            service.refresh_positions()
            service.refresh_account()
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    positions = root_cli.fetch_execution_positions(
        service.settings.database_path,
        broker_name=getattr(service.adapter, "adapter_name", None),
        account_id=getattr(service.adapter, "account_id", None),
    )
    account = root_cli.fetch_latest_execution_account(
        service.settings.database_path,
        broker_name=getattr(service.adapter, "adapter_name", None),
        account_id=getattr(service.adapter, "account_id", None),
    )
    if account is None and not positions:
        console.print("No broker balance snapshot found.")
        raise typer.Exit(code=1)
    if account is not None:
        console.print(broker_account_table(account))
    if positions:
        console.print(broker_position_table(positions))


@app.command("broker-compare-paper")
def broker_compare_paper(
    limit: int = typer.Option(20, help="Rows to display."),
) -> None:
    """Compare the latest broker execution rows with the latest paper orders."""
    service = require_broker_execution_service()
    rows = service.compare_with_latest_paper(limit=limit)
    if not rows:
        console.print("No comparable broker and paper rows found.")
        raise typer.Exit(code=1)
    console.print(broker_paper_comparison_table(rows))
