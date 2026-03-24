from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .dashboard import launch_dashboard
from .config import get_settings
from .db import (
    fetch_latest_candidates,
    fetch_latest_premarket_signals,
    fetch_latest_replay_report,
    fetch_latest_scan_id,
    fetch_latest_session_decisions,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
    init_database,
    insert_social_signals,
)
from .providers.live_market import build_live_market_provider
from .providers.social import SocialMentionsCSVProvider
from .services.replay_pipeline import ReplayPipeline
from .services.report_builder import ReportBuilder
from .services.social_monitor import SocialMonitor
from .services.universe_builder import UniverseBuilder
from .services.watchlist_builder import WatchlistBuilder

app = typer.Typer(help="Penny stock radar CLI.")
console = Console()


@app.command("init-db")
def init_db() -> None:
    """Initialize the SQLite database."""
    settings = get_settings()
    init_database(settings.database_path)
    console.print(f"Initialized database at [bold]{settings.database_path}[/bold]")


@app.command("build-universe")
def build_universe(
    max_symbols: int | None = typer.Option(
        None,
        help="Cap the number of seed symbols downloaded from Nasdaq Trader.",
    ),
    export_json: Path | None = typer.Option(
        None,
        help="Optional path to export the latest universe candidates as JSON.",
    ),
) -> None:
    """Build and persist a Milestone 1 penny stock universe snapshot."""
    settings = get_settings()
    init_database(settings.database_path)
    builder = UniverseBuilder(settings)
    snapshot, candidates = builder.run(max_symbols=max_symbols)
    if export_json is not None:
        builder.export_json(candidates, export_json)

    passed = sum(1 for candidate in candidates if candidate.passed_filters)
    console.print(
        f"Snapshot [bold]{snapshot.snapshot_id}[/bold] saved with "
        f"{passed}/{len(candidates)} passing candidates."
    )


@app.command("show-latest")
def show_latest(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest universe snapshot rows."""
    settings = get_settings()
    init_database(settings.database_path)
    rows = fetch_latest_candidates(settings.database_path, limit=limit)
    if not rows:
        console.print("No universe snapshot found. Run `psradar build-universe` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Universe Snapshot")
    table.add_column("Symbol")
    table.add_column("Price")
    table.add_column("Market Cap")
    table.add_column("Float")
    table.add_column("Passed")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['price']:.2f}" if row["price"] is not None else "-",
            f"{row['market_cap']:,}" if row["market_cap"] else "-",
            f"{row['float_shares']:,}" if row["float_shares"] else "-",
            "yes" if row["passed_filters"] else "no",
            row["filter_reasons"],
        )
    console.print(table)


@app.command("build-watchlist")
def build_watchlist(
    limit: int | None = typer.Option(
        None,
        help="Maximum watchlist rows to persist and display.",
    ),
    lookback_hours: int | None = typer.Option(
        None,
        help="SEC filing lookback window in hours.",
    ),
) -> None:
    """Build a Milestone 2 watchlist from the latest universe snapshot."""
    settings = get_settings()
    init_database(settings.database_path)
    builder = WatchlistBuilder(settings)
    entries, filings, _ = builder.build(limit=limit, lookback_hours=lookback_hours)
    if not entries:
        console.print(
            "No watchlist entries were produced. Build a universe first or widen the filters."
        )
        raise typer.Exit(code=1)

    console.print(
        f"Built watchlist with {len(entries)} entries from {len(filings)} matched filings."
    )
    show_watchlist(limit=len(entries))


@app.command("show-watchlist")
def show_watchlist(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest watchlist."""
    settings = get_settings()
    init_database(settings.database_path)
    rows = fetch_latest_watchlist(settings.database_path, limit=limit)
    if not rows:
        console.print("No watchlist found. Run `psradar build-watchlist` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Watchlist")
    table.add_column("Symbol")
    table.add_column("Total")
    table.add_column("Catalyst")
    table.add_column("Technical")
    table.add_column("Sympathy")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['total_score']:.2f}",
            f"{row['catalyst_score']:.2f}",
            f"{row['technical_score']:.2f}",
            f"{row['sympathy_score']:.2f}",
            row["reasons"],
        )
    console.print(table)


@app.command("generate-mock-replay")
def generate_mock_replay(
    output_csv: Path | None = typer.Option(
        None,
        help="Optional output CSV path for generated replay ticks.",
    ),
    symbol_limit: int | None = typer.Option(
        None,
        help="Optional cap on how many watchlist symbols to simulate.",
    ),
) -> None:
    """Generate replay CSV data from the latest watchlist using mock scenarios."""
    settings = get_settings()
    init_database(settings.database_path)
    pipeline = ReplayPipeline(settings)
    replay_path = pipeline.generate_mock_replay(
        output_path=output_csv,
        symbol_limit=symbol_limit,
    )
    console.print(f"Replay CSV written to [bold]{replay_path}[/bold]")


@app.command("analyze-replay")
def analyze_replay(
    replay_csv: Path = typer.Option(..., help="Replay CSV created by generate-mock-replay."),
    export_json: Path | None = typer.Option(
        None,
        help="Optional JSON path for the combined replay report.",
    ),
) -> None:
    """Run premarket + regular-session analysis on a replay CSV."""
    settings = get_settings()
    init_database(settings.database_path)
    pipeline = ReplayPipeline(settings)
    payload = pipeline.analyze_replay(replay_csv, export_json=export_json)
    console.print(
        f"Analyzed replay for {len(payload['premarket_signals'])} symbols. "
        f"Report label: [bold]{payload['report']['label']}[/bold]"
    )
    show_premarket(limit=len(payload["premarket_signals"]))
    show_session(limit=len(payload["session_decisions"]))
    show_report()


@app.command("run-replay-pipeline")
def run_replay_pipeline(
    output_csv: Path | None = typer.Option(
        None,
        help="Optional CSV output for generated replay data.",
    ),
    export_json: Path | None = typer.Option(
        None,
        help="Optional JSON output for the replay analysis report.",
    ),
) -> None:
    """Generate mock replay data and analyze it in one step."""
    settings = get_settings()
    init_database(settings.database_path)
    pipeline = ReplayPipeline(settings)
    replay_path = pipeline.generate_mock_replay(output_path=output_csv)
    pipeline.analyze_replay(replay_path, export_json=export_json)
    console.print(f"Completed replay pipeline for [bold]{replay_path}[/bold]")
    show_premarket(limit=settings.watchlist_limit)
    show_session(limit=settings.watchlist_limit)
    show_report()


@app.command("show-premarket")
def show_premarket(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest premarket analysis results."""
    settings = get_settings()
    init_database(settings.database_path)
    rows = fetch_latest_premarket_signals(settings.database_path, limit=limit)
    if not rows:
        console.print("No premarket analysis found. Run `psradar analyze-replay` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Premarket Signals")
    table.add_column("Symbol")
    table.add_column("Quality")
    table.add_column("RVOL")
    table.add_column("Dollar Vol")
    table.add_column("TPS")
    table.add_column("Spread%")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['quality_score']:.2f}",
            f"{row['premarket_rvol']:.2f}",
            f"{row['dollar_volume']:.0f}",
            f"{row['tps']:.4f}",
            f"{row['spread_pct'] * 100:.2f}",
            row["reasons"],
        )
    console.print(table)


@app.command("show-session")
def show_session(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest regular-session decisions."""
    settings = get_settings()
    init_database(settings.database_path)
    rows = fetch_latest_session_decisions(settings.database_path, limit=limit)
    if not rows:
        console.print("No session decisions found. Run `psradar analyze-replay` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Session Decisions")
    table.add_column("Symbol")
    table.add_column("Decision")
    table.add_column("AVWAP")
    table.add_column("Ext Z")
    table.add_column("MFE%")
    table.add_column("MAE%")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            row["decision"],
            f"{row['anchored_vwap']:.2f}",
            f"{row['extension_z']:.2f}",
            f"{row['mfe_pct']:.2f}",
            f"{row['mae_pct']:.2f}",
            row["reasons"],
        )
    console.print(table)


@app.command("show-report")
def show_report() -> None:
    """Show the latest replay evaluation report."""
    settings = get_settings()
    init_database(settings.database_path)
    row = fetch_latest_replay_report(settings.database_path)
    if row is None:
        console.print("No replay report found. Run `psradar analyze-replay` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Replay Report")
    table.add_column("Label")
    table.add_column("Expectancy")
    table.add_column("Profit Factor")
    table.add_column("Precision@K")
    table.add_column("Avg MFE%")
    table.add_column("Avg MAE%")
    table.add_column("Symbols")
    table.add_row(
        row["label"],
        f"{row['expectancy']:.2f}",
        f"{row['profit_factor']:.2f}",
        f"{row['precision_at_k']:.2f}",
        f"{row['average_mfe_pct']:.2f}",
        f"{row['average_mae_pct']:.2f}",
        str(row["symbol_count"]),
    )
    console.print(table)


@app.command("analyze-social")
def analyze_social(
    mentions_csv: Path = typer.Option(..., help="CSV with timestamp,symbol,platform,author,engagement."),
) -> None:
    """Analyze social mention velocity from a CSV fallback source."""
    settings = get_settings()
    init_database(settings.database_path)
    scan_row = fetch_latest_scan_id(settings.database_path)
    if scan_row is None:
        console.print("No scan run found. Build universe/watchlist first.")
        raise typer.Exit(code=1)

    frame = SocialMentionsCSVProvider().load_mentions(mentions_csv)
    signals = SocialMonitor().analyze(frame)
    if not signals:
        console.print("No social signals found in the provided CSV.")
        raise typer.Exit(code=1)
    insert_social_signals(settings.database_path, scan_row["scan_id"], signals)
    console.print(f"Analyzed social mentions for {len(signals)} symbols.")
    show_social(limit=len(signals))


@app.command("show-social")
def show_social(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest social signals."""
    settings = get_settings()
    init_database(settings.database_path)
    rows = fetch_latest_social_signals(settings.database_path, limit=limit)
    if not rows:
        console.print("No social signals found. Run `psradar analyze-social` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Social Signals")
    table.add_column("Symbol")
    table.add_column("Score")
    table.add_column("Mentions")
    table.add_column("Velocity")
    table.add_column("Authors")
    table.add_column("Platforms")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['social_score']:.2f}",
            str(row["mention_count"]),
            f"{row['mention_velocity']:.2f}",
            str(row["unique_authors"]),
            str(row["cross_platform_count"]),
            row["reasons"],
        )
    console.print(table)


@app.command("show-live-market")
def show_live_market(
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to query. Repeat the flag for multiple symbols. Defaults to the latest watchlist.",
    ),
    limit: int = typer.Option(5, help="Maximum symbols to query when using the latest watchlist."),
    export_json: Path | None = typer.Option(
        None,
        help="Optional JSON export path for the fetched live snapshots.",
    ),
) -> None:
    """Fetch bounded latest-trade/quote/snapshot data from the configured live provider."""
    settings = get_settings()
    init_database(settings.database_path)
    provider = build_live_market_provider(settings)
    if not provider.is_available():
        reason = getattr(provider, "reason", "No live market data provider is configured.")
        console.print(reason)
        raise typer.Exit(code=1)

    symbols = [value.upper() for value in symbol]
    if not symbols:
        watchlist_rows = fetch_latest_watchlist(settings.database_path, limit=limit)
        symbols = [row["symbol"] for row in watchlist_rows]
    if not symbols:
        console.print("No symbols available. Build a watchlist first or pass `--symbol` explicitly.")
        raise typer.Exit(code=1)

    snapshots: list[dict[str, object]] = []
    table = Table(title="Live Market Snapshot")
    table.add_column("Symbol")
    table.add_column("Source")
    table.add_column("Trade")
    table.add_column("Bid")
    table.add_column("Ask")
    table.add_column("Spread%")
    table.add_column("Status")
    table.add_column("Updated")

    for ticker in symbols[:limit]:
        snapshot = provider.latest_snapshot(ticker)
        if snapshot is None:
            continue
        bid = snapshot.latest_quote.bid_price if snapshot.latest_quote else None
        ask = snapshot.latest_quote.ask_price if snapshot.latest_quote else None
        spread_pct = _spread_pct(bid, ask)
        updated_at = snapshot.updated_at or (
            snapshot.latest_trade.timestamp if snapshot.latest_trade else None
        )

        row = {
            "symbol": snapshot.symbol,
            "source": snapshot.source,
            "trade_price": snapshot.latest_trade.price if snapshot.latest_trade else None,
            "trade_size": snapshot.latest_trade.size if snapshot.latest_trade else None,
            "bid_price": bid,
            "ask_price": ask,
            "spread_pct": spread_pct,
            "market_status": snapshot.market_status,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }
        snapshots.append(row)
        table.add_row(
            snapshot.symbol,
            snapshot.source,
            _format_optional_number(row["trade_price"], 4),
            _format_optional_number(bid, 4),
            _format_optional_number(ask, 4),
            _format_optional_percent(spread_pct),
            snapshot.market_status or "-",
            row["updated_at"] or "-",
        )

    close = getattr(provider, "close", None)
    if callable(close):
        close()

    if not snapshots:
        console.print("No live market snapshots were returned for the requested symbols.")
        raise typer.Exit(code=1)

    console.print(table)
    if export_json is not None:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
        console.print(f"Live snapshot JSON written to [bold]{export_json}[/bold]")


@app.command("export-summary")
def export_summary(
    json_output: Path = typer.Option(
        Path("sample_outputs/radar_summary.json"),
        help="Path to export a combined JSON summary.",
    ),
    markdown_output: Path = typer.Option(
        Path("sample_outputs/radar_summary.md"),
        help="Path to export a combined Markdown summary.",
    ),
    limit: int = typer.Option(20, help="Rows to include from each latest snapshot."),
) -> None:
    """Export a combined summary of the latest stored outputs."""
    settings = get_settings()
    init_database(settings.database_path)
    builder = ReportBuilder()
    builder.export_json(settings.database_path, json_output, limit=limit)
    builder.export_markdown(settings.database_path, markdown_output, limit=limit)
    console.print(
        f"Exported summary to [bold]{json_output}[/bold] and [bold]{markdown_output}[/bold]"
    )


@app.command("dashboard")
def dashboard(
    host: str = typer.Option("localhost", help="Streamlit host address."),
    port: int = typer.Option(8501, help="Streamlit port."),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--headless",
        help="Open a browser window after launch.",
    ),
) -> None:
    """Launch the local Streamlit dashboard."""
    settings = get_settings()
    init_database(settings.database_path)
    launch_dashboard(host=host, port=port, open_browser=open_browser)


def _format_optional_number(value: object, precision: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{precision}f}"
    except Exception:
        return str(value)


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}"


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint
