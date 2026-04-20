from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from ..db import (
    fetch_latest_scan_id,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
    insert_social_signals,
)
from ..providers.live_market import build_live_market_provider
from ..providers.social import SocialMentionsCSVProvider
from ..services.social_monitor import SocialMonitor
from .common import console, format_optional_number, format_optional_percent, spread_pct

app = typer.Typer()


@app.command("analyze-social")
def analyze_social(
    mentions_csv: Path = typer.Option(..., help="CSV with timestamp,symbol,platform,author,engagement."),
) -> None:
    """Analyze social mention velocity from a CSV fallback source."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    try:
        provider = build_live_market_provider(settings)
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
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
        current_spread_pct = spread_pct(bid, ask)
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
            "spread_pct": current_spread_pct,
            "market_status": snapshot.market_status,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }
        snapshots.append(row)
        table.add_row(
            snapshot.symbol,
            snapshot.source,
            format_optional_number(row["trade_price"], 4),
            format_optional_number(bid, 4),
            format_optional_number(ask, 4),
            format_optional_percent(current_spread_pct),
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
