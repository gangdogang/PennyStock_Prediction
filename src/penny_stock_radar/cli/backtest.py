from __future__ import annotations

from pathlib import Path
import time

import typer
from rich.table import Table

from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_premkt_predictions,
    fetch_latest_watchlist,
)
from ..services.backtest_data import BacktestDataManager
from ..services.kis_historical import KISHistoricalDataService
from ..services.market_activity import MarketActivityScanner
from .common import console, format_optional_number, format_optional_percent, trade_call_label

app = typer.Typer()


def _resolve_l1_capture_symbols(
    settings,
    *,
    symbol: list[str] | None,
    limit: int,
) -> list[str]:
    symbols = [value.upper() for value in (symbol or [])]
    if symbols:
        return symbols[:limit]

    watchlist_rows = fetch_latest_watchlist(
        settings.database_path,
        limit=limit,
        prefer_reportable=False,
    )
    return [str(row["symbol"]).upper() for row in watchlist_rows]


@app.command("run-premkt-predictor")
def run_premkt_predictor(
    limit: int | None = typer.Option(
        None,
        help="Maximum prediction rows to persist and display.",
    ),
    lookback_hours: int | None = typer.Option(
        None,
        help="SEC filing lookback window in hours.",
    ),
    export_json: Path | None = typer.Option(
        None,
        help="Optional JSON path for premarket prediction output.",
    ),
) -> None:
    """Build and persist premarket predictions without placing any orders."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    predictor = root_cli.PremktPredictor(settings)
    predictions = predictor.run(
        limit=limit,
        lookback_hours=lookback_hours,
        output_path=export_json,
    )
    if not predictions:
        console.print("No premarket predictions were produced. Build a universe first.")
        raise typer.Exit(code=1)

    console.print(f"Stored {len(predictions)} premarket predictions.")
    show_premkt_predictions(limit=len(predictions))


@app.command("show-premkt-predictions")
def show_premkt_predictions(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest premarket prediction snapshot."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    rows = fetch_latest_premkt_predictions(
        settings.database_path,
        limit=limit,
        prefer_reportable=False,
    )
    if not rows:
        console.print("No premarket predictions found. Run `psradar run-premkt-predictor` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Premarket Predictions")
    table.add_column("Symbol")
    table.add_column("Score")
    table.add_column("Max Hold")
    table.add_column("Themes")
    table.add_column("Rationale")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{float(row['score']):.2f}",
            str(int(row["max_hold_days"])),
            str(row["themes"]),
            str(row["entry_rationale"]),
        )
    console.print(table)


@app.command("backfill-kis-minute")
def backfill_kis_minute(
    market_date: str = typer.Option(..., help="Target market date in YYYY-MM-DD."),
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to backfill. Repeat the flag for multiple symbols. Defaults to the point-in-time universe for the date.",
    ),
    symbol_limit: int | None = typer.Option(
        None,
        help="Cap how many symbols are backfilled when the universe is resolved from the database.",
    ),
    nmin: int = typer.Option(1, help="Minute interval to request from KIS."),
    max_pages: int = typer.Option(10, help="Maximum KIS pagination depth per symbol."),
    infer_halts: bool = typer.Option(
        True,
        "--infer-halts/--no-infer-halts",
        help="Infer halt events from the stored minute bars after the backfill completes.",
    ),
) -> None:
    """Backfill KIS overseas minute bars into the historical backtest tables."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    service = KISHistoricalDataService(settings)
    manager = BacktestDataManager(settings)
    try:
        summary = service.backfill_minute_bars(
            market_date,
            symbols=symbol,
            symbol_limit=symbol_limit,
            nmin=nmin,
            max_pages=max_pages,
        )
    finally:
        service.close()

    console.print(
        f"KIS minute backfill stored [bold]{summary.inserted_rows}[/bold] bars for "
        f"[bold]{summary.requested_symbols}[/bold] symbols on [bold]{summary.market_date}[/bold]."
    )
    if summary.unresolved_symbols:
        console.print(f"Unresolved exchange symbols: {', '.join(summary.unresolved_symbols)}")
    if summary.skipped_symbols:
        console.print(f"No minute rows returned: {', '.join(summary.skipped_symbols)}")
    if infer_halts:
        inferred = manager.infer_and_store_halt_events(
            market_date,
            symbols=symbol or None,
        )
        console.print(f"Inferred [bold]{len(inferred)}[/bold] halt events from minute bars.")


@app.command("capture-kis-l1")
def capture_kis_l1(
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to snapshot. Repeat the flag for multiple symbols. Defaults to the latest watchlist.",
    ),
    limit: int = typer.Option(
        20,
        help="Maximum symbols to snapshot when symbols are resolved from stored data.",
    ),
) -> None:
    """Capture KIS top-of-book quotes into the historical L1 table for forward archive building."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    symbols = _resolve_l1_capture_symbols(
        settings,
        symbol=symbol,
        limit=limit,
    )
    if not symbols:
        console.print("No symbols available. Build a watchlist first or pass `--symbol` explicitly.")
        raise typer.Exit(code=1)

    service = KISHistoricalDataService(settings)
    try:
        summary = service.capture_l1_quotes(symbols=symbols[:limit])
    finally:
        service.close()

    console.print(
        f"KIS L1 snapshot stored [bold]{summary.inserted_rows}[/bold] quotes for "
        f"[bold]{summary.requested_symbols}[/bold] symbols on [bold]{summary.market_date}[/bold]."
    )
    if summary.unresolved_symbols:
        console.print(f"Unresolved exchange symbols: {', '.join(summary.unresolved_symbols)}")
    if summary.skipped_symbols:
        console.print(f"No L1 quote returned: {', '.join(summary.skipped_symbols)}")


@app.command("capture-kis-l1-window")
def capture_kis_l1_window(
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to snapshot repeatedly. Defaults to the latest watchlist.",
    ),
    limit: int = typer.Option(
        20,
        help="Maximum symbols to snapshot when symbols are resolved from stored data.",
    ),
    iterations: int = typer.Option(
        10,
        min=1,
        help="How many repeated capture passes to run.",
    ),
    interval_seconds: float = typer.Option(
        60.0,
        min=1e-9,
        help="How long to wait between capture passes.",
    ),
) -> None:
    """Capture KIS L1 quotes repeatedly to build interval coverage for the current session."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    symbols = _resolve_l1_capture_symbols(
        settings,
        symbol=symbol,
        limit=limit,
    )
    if not symbols:
        console.print("No symbols available. Build a watchlist first or pass `--symbol` explicitly.")
        raise typer.Exit(code=1)

    service = KISHistoricalDataService(settings)
    total_inserted = 0
    unresolved: set[str] = set()
    skipped: set[str] = set()
    market_date: str | None = None
    try:
        for index in range(iterations):
            summary = service.capture_l1_quotes(symbols=symbols[:limit])
            market_date = summary.market_date
            total_inserted += summary.inserted_rows
            unresolved.update(summary.unresolved_symbols)
            skipped.update(summary.skipped_symbols)
            typer.echo(
                "iteration="
                f"{index + 1} symbols={summary.requested_symbols} "
                f"new_rows={summary.inserted_rows} distinct_minutes={summary.distinct_minute_keys}",
                err=True,
            )
            if summary.snapshot_mismatch_count:
                typer.echo(
                    f"snapshot_date mismatch rows={summary.snapshot_mismatch_count}",
                    err=True,
                )
            if summary.duplicate_minute_bucket_count:
                typer.echo(
                    f"duplicate minute bucket rows={summary.duplicate_minute_bucket_count}",
                    err=True,
                )
            if summary.stale_timestamp_fallback_count:
                typer.echo(
                    f"stale timestamp fallback rows={summary.stale_timestamp_fallback_count}",
                    err=True,
                )
            console.print(
                f"L1 capture pass {index + 1}/{iterations}: "
                f"stored [bold]{summary.inserted_rows}[/bold] quotes for "
                f"[bold]{summary.requested_symbols}[/bold] symbols."
            )
            if index + 1 < iterations:
                time.sleep(interval_seconds)
    finally:
        service.close()

    console.print(
        f"KIS L1 archive window stored [bold]{total_inserted}[/bold] quotes across "
        f"[bold]{iterations}[/bold] passes"
        + (f" on [bold]{market_date}[/bold]." if market_date is not None else ".")
    )
    if unresolved:
        console.print(f"Unresolved exchange symbols: {', '.join(sorted(unresolved))}")
    if skipped:
        console.print(f"No L1 quote returned: {', '.join(sorted(skipped))}")


@app.command("report-backtest-coverage")
def report_backtest_coverage(
    market_date: str = typer.Option(..., help="Target market date in YYYY-MM-DD."),
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to evaluate. Defaults to the point-in-time universe for the date.",
    ),
    limit: int | None = typer.Option(
        None,
        help="Optional cap when coverage symbols are resolved from the database.",
    ),
    session: str = typer.Option(
        "premarket",
        help="Coverage window to evaluate: premarket, regular, or full_day.",
    ),
    infer_halts: bool = typer.Option(
        False,
        "--infer-halts/--no-infer-halts",
        help="Infer halt events from minute bars as part of the report run.",
    ),
    json_output: Path | None = typer.Option(
        None,
        help="Optional JSON path for the full coverage report. Defaults to the configured coverage report directory.",
    ),
    gate_status_path: Path | None = typer.Option(
        None,
        help="Optional JSON path for the latest coverage gate snapshot. Defaults to the configured gate status path.",
    ),
) -> None:
    """Build a historical L1 coverage report for a backtest date."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    manager = BacktestDataManager(settings)
    symbols = [value.upper() for value in (symbol or [])]
    if not symbols:
        point_in_time_rows = manager.fetch_point_in_time_universe(market_date)
        symbols = [str(row["symbol"]).upper() for row in point_in_time_rows]
    if not symbols:
        latest_rows = fetch_latest_passed_universe(
            settings.database_path,
            prefer_reportable=False,
        )
        symbols = [str(row["symbol"]).upper() for row in latest_rows]
    if limit is not None:
        symbols = symbols[:limit]
    if not symbols:
        console.print("No symbols available. Tag a point-in-time universe first or build a universe snapshot.")
        raise typer.Exit(code=1)

    try:
        report = manager.build_l1_coverage_report(
            market_date,
            symbols=symbols,
            session=session,
            source="historical_l1_quotes",
        )
        report_path = manager.export_coverage_report_json(report, output_path=json_output)
        gate_status = manager.build_coverage_gate_status(
            report,
            report_path=report_path,
        )
        gate_path = manager.export_coverage_gate_status(
            gate_status,
            output_path=gate_status_path,
        )
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write coverage outputs: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"L1 coverage {report.market_date} {session}: "
        f"symbols={report.covered_symbol_count}/{report.expected_symbol_count} "
        f"({report.symbol_coverage_pct:.1f}%), "
        f"intervals={report.covered_interval_count}/{report.expected_interval_count} "
        f"({report.interval_coverage_pct:.1f}%)."
    )
    console.print(f"Coverage report JSON: {report_path}")
    console.print(
        "Coverage gate "
        f"{gate_status.status}: threshold={gate_status.threshold_pct:.1f}% "
        f"(symbol={gate_status.symbol_coverage_pct:.1f}%, interval={gate_status.interval_coverage_pct:.1f}%)."
    )
    console.print(f"Coverage gate status JSON: {gate_path}")
    if infer_halts:
        inferred = manager.infer_and_store_halt_events(market_date, symbols=symbols)
        console.print(f"Inferred [bold]{len(inferred)}[/bold] halt events from minute bars.")


@app.command("scan-market-activity")
def scan_market_activity(
    phase: str = typer.Option(
        "auto",
        help="Which phase to scan: auto, premarket, or regular.",
    ),
    scan_limit: int | None = typer.Option(
        None,
        help="Optional cap on how many symbols to scan from the current market scope.",
    ),
    top_limit: int = typer.Option(
        10,
        help="How many symbols count as top leaders for prediction-vs-outcome matching.",
    ),
    comparison_csv: Path | None = typer.Option(
        None,
        help="Optional CSV path for prediction-vs-outcome export. Defaults to sample_outputs/<phase>_prediction_vs_actual.csv.",
    ),
) -> None:
    """Rank the latest in-scope market movers by live % change and volume, then compare them with the watchlist."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    scanner = MarketActivityScanner(settings)
    if comparison_csv is None:
        resolved_phase = scanner.resolve_market_phase(phase)
        if resolved_phase in {"premarket", "regular"}:
            comparison_csv = Path("sample_outputs") / f"{resolved_phase}_prediction_vs_actual.csv"

    try:
        result = scanner.scan(
            phase=phase,
            scan_limit=scan_limit,
            top_limit=top_limit,
            comparison_csv=comparison_csv,
        )
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    pct_table = Table(title=f"Live {result.market_phase.title()} Movers · % Change")
    pct_table.add_column("Rank")
    pct_table.add_column("Symbol")
    pct_table.add_column("Pred")
    pct_table.add_column("Price")
    pct_table.add_column("%Chg")
    pct_table.add_column("Volume")
    pct_table.add_column("DV")
    pct_table.add_column("Spread")
    pct_table.add_column("Read")
    for row in sorted(result.activity, key=lambda item: (item.pct_rank, item.symbol))[:top_limit]:
        pct_table.add_row(
            str(row.pct_rank),
            row.symbol,
            "Y" if row.predicted else "-",
            format_optional_number(row.last_price, 4),
            format_optional_number(row.pct_change, 2),
            format_optional_number(row.volume, 0),
            format_optional_number(row.dollar_volume, 0),
            format_optional_percent(row.spread_pct),
            trade_call_label(row.analysis_label),
        )

    volume_table = Table(title=f"Live {result.market_phase.title()} Movers · Volume")
    volume_table.add_column("Rank")
    volume_table.add_column("Symbol")
    volume_table.add_column("Pred")
    volume_table.add_column("Volume")
    volume_table.add_column("DV")
    volume_table.add_column("%Chg")
    volume_table.add_column("Spread")
    volume_table.add_column("Read")
    for row in sorted(result.activity, key=lambda item: (item.volume_rank, item.symbol))[:top_limit]:
        volume_table.add_row(
            str(row.volume_rank),
            row.symbol,
            "Y" if row.predicted else "-",
            format_optional_number(row.volume, 0),
            format_optional_number(row.dollar_volume, 0),
            format_optional_number(row.pct_change, 2),
            format_optional_percent(row.spread_pct),
            trade_call_label(row.analysis_label),
        )

    outcome_table = Table(title=f"Prediction vs Outcome · {result.market_phase.title()}")
    outcome_table.add_column("Symbol")
    outcome_table.add_column("Pred")
    outcome_table.add_column("WRank")
    outcome_table.add_column("%Rank")
    outcome_table.add_column("VRank")
    outcome_table.add_column("Outcome")
    outcome_table.add_column("Read")
    for row in result.outcomes[: max(top_limit * 2, 12)]:
        outcome_table.add_row(
            row.symbol,
            "Y" if row.predicted else "-",
            str(row.watchlist_rank or "-"),
            str(row.pct_rank or "-"),
            str(row.volume_rank or "-"),
            row.outcome,
            trade_call_label(row.analysis_label),
        )

    console.print(
        f"Scanned {result.scanned_symbol_count} symbols from the {settings.market_scope_label} scope for the {result.market_phase} phase."
    )
    console.print(pct_table)
    console.print(volume_table)
    console.print(outcome_table)
    if result.comparison_csv is not None:
        console.print(f"Prediction comparison CSV written to [bold]{result.comparison_csv}[/bold]")
