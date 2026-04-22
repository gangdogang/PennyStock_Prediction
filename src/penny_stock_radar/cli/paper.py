from __future__ import annotations

import json
from pathlib import Path
import time

import typer

from ..paper_console import build_paper_summary_tables
from ..services.paper_coordinator import PaperTradingCoordinator
from ..services.paper_reporting import archive_paper_performance_export, paper_report_paths, read_csv_rows
from ..services.paper_trading import PREDICTOR_WEIGHTED_BUCKET, PRIMARY_PAPER_STRATEGY
from .common import console

app = typer.Typer()


@app.command("run-paper-trading")
def run_paper_trading(
    phase: str = typer.Option(
        "auto",
        help="Which market phase to evaluate: auto, premarket, regular, afterhours, or closed.",
    ),
    export_csv: bool = typer.Option(
        True,
        "--export-csv/--no-export-csv",
        help="Refresh the paper-trading CSV logs after this pass.",
    ),
) -> None:
    """Run one automated paper-trading pass using the latest live market ranking."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    engine = PaperTradingCoordinator(settings)
    try:
        result = engine.run_once(phase=phase, export_csv=export_csv)
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    console.print(
        f"Paper trading [{result.market_phase}] actions={','.join(result.actions)} "
        f"equity={result.equity:.2f} realized={result.realized_pnl:.2f} "
        f"unrealized={result.unrealized_pnl:.2f} profit_factor={result.profit_factor:.2f}"
    )
    console.print(f"CSV logs written to [bold]{result.export_dir}[/bold]")


@app.command("paper-trader")
def paper_trader(
    check_interval_seconds: int = typer.Option(
        60,
        help="How often to poll live market data when running continuously.",
    ),
    phase: str = typer.Option(
        "auto",
        help="Which market phase to evaluate each loop.",
    ),
    run_once: bool = typer.Option(
        False,
        "--run-once/--watch",
        help="Run a single paper-trading pass and exit instead of looping.",
    ),
    max_runtime_seconds: float | None = typer.Option(
        None,
        help="Stop the continuous trader after this many seconds.",
    ),
) -> None:
    """Continuously run the automated paper-trading engine."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    engine = PaperTradingCoordinator(settings)
    started_at = time.monotonic()

    try:
        while True:
            try:
                result = engine.run_once(phase=phase, export_csv=True)
            except RuntimeError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
                if run_once:
                    raise typer.Exit(code=1)
            else:
                console.print(
                    f"[{result.market_phase}] actions={','.join(result.actions)} "
                    f"open={result.open_position_count} closed={result.closed_trade_count} "
                    f"equity={result.equity:.2f} realized={result.realized_pnl:.2f} "
                    f"unrealized={result.unrealized_pnl:.2f}"
                )
            if run_once:
                break
            if max_runtime_seconds is not None:
                elapsed = time.monotonic() - started_at
                remaining = max_runtime_seconds - elapsed
                if remaining <= 0:
                    console.print("Paper trader reached max runtime.")
                    break
                sleep_seconds = min(float(check_interval_seconds), remaining)
            else:
                sleep_seconds = float(check_interval_seconds)
            console.print(f"Sleeping for {sleep_seconds:g} seconds.")
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        console.print("Paper trader stopped by user.")


@app.command("show-paper-summary")
def show_paper_summary(
    order_limit: int = typer.Option(10, help="Recent paper orders to display."),
) -> None:
    """Show the latest paper-trading performance summary."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    run = root_cli.fetch_latest_paper_trading_run(
        settings.database_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    if run is None:
        console.print("No paper-trading run found. Run `psradar run-paper-trading` first.")
        raise typer.Exit(code=1)

    positions = root_cli.fetch_paper_positions(settings.database_path, run.run_id)
    orders = root_cli.fetch_paper_orders(settings.database_path, run.run_id)
    strategy_runs = root_cli.fetch_latest_paper_strategy_runs(settings.database_path, limit=10)
    report_paths = paper_report_paths(settings.paper_trade_dir)
    tables = build_paper_summary_tables(
        run=run,
        positions=positions,
        orders=orders,
        strategy_runs=strategy_runs,
        backtest_kpis=read_csv_rows(report_paths.backtest_kpis),
        regime_split_rows=read_csv_rows(report_paths.regime_split),
        predictor_kpi_rows=read_csv_rows(report_paths.predictor_kpis),
        execution_quality_rows=read_csv_rows(report_paths.execution_quality),
        order_limit=order_limit,
    )
    for table in tables:
        if table.row_count:
            console.print(table)
    console.print(f"CSV logs are in [bold]{settings.paper_trade_dir.resolve()}[/bold]")


@app.command("review-paper-performance")
def review_paper_performance(
    export_dir: Path | None = typer.Option(
        None,
        help="Paper export directory to inspect. Defaults to PENNY_STOCK_PAPER_TRADE_DIR.",
    ),
) -> None:
    """Evaluate whether paper performance CSVs are wired for predictor review."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    coordinator = PaperTradingCoordinator(
        settings,
        export_dir=export_dir or settings.paper_trade_dir,
    )
    payload = coordinator.reporting.evaluate_performance_review_gate()
    console.print(json.dumps(payload, indent=2, sort_keys=True))
    if payload["status"] != "pass":
        raise typer.Exit(code=1)


@app.command("archive-paper-performance")
def archive_paper_performance(
    export_dir: Path | None = typer.Option(
        None,
        help="Paper export directory to archive. Defaults to PENNY_STOCK_PAPER_TRADE_DIR.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output-path",
        "--output",
        help="Zip file path to create. Defaults to a timestamped archive next to the export directory.",
    ),
    refresh_gate: bool = typer.Option(
        True,
        "--refresh-gate/--no-refresh-gate",
        help="Refresh paper_performance_gate.json before creating the archive.",
    ),
    allow_fail: bool = typer.Option(
        False,
        "--allow-fail/--require-pass",
        help="Create the archive even when the performance gate fails.",
    ),
) -> None:
    """Zip paper performance artifacts for transfer/review."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    target_dir = export_dir or settings.paper_trade_dir
    coordinator = PaperTradingCoordinator(settings, export_dir=target_dir)
    payload: dict[str, object] | None = None
    if refresh_gate:
        gate_path = coordinator.export_performance_review_gate()
        payload = json.loads(gate_path.read_text(encoding="utf-8"))

    archive_path = archive_paper_performance_export(
        target_dir,
        output_path=output_path,
    )
    console.print(f"Paper performance archive written to [bold]{archive_path}[/bold]")
    if payload is not None:
        console.print(json.dumps(payload, indent=2, sort_keys=True))
        if payload["status"] != "pass" and not allow_fail:
            raise typer.Exit(code=1)
