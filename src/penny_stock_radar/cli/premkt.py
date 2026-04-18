from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from ..db import (
    fetch_latest_candidates,
    fetch_latest_premarket_signals,
    fetch_latest_replay_report,
    fetch_latest_session_decisions,
    fetch_latest_watchlist,
)
from ..services.universe_builder import UniverseBuilder
from .common import console

app = typer.Typer()


@app.command("init-db")
def init_db() -> None:
    """Initialize the SQLite database."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    builder = root_cli.WatchlistBuilder(settings)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    rows = fetch_latest_watchlist(
        settings.database_path,
        limit=limit,
        prefer_reportable=False,
    )
    if not rows:
        console.print("No watchlist found. Run `psradar build-watchlist` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Watchlist")
    table.add_column("Symbol")
    table.add_column("Total")
    table.add_column("Catalyst")
    table.add_column("Technical")
    table.add_column("Sympathy")
    table.add_column("Context")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['total_score']:.2f}",
            f"{row['catalyst_score']:.2f}",
            f"{row['technical_score']:.2f}",
            f"{row['sympathy_score']:.2f}",
            (
                f"{row['market_context_score']:.2f}"
                if row["market_context_score"] is not None
                else "0.00"
            ),
            row["reasons"],
        )
    console.print(table)


@app.command("show-premarket")
def show_premarket(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest premarket analysis results."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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

