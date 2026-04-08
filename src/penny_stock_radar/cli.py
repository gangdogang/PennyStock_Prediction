from __future__ import annotations

import json
from pathlib import Path
import time

import typer
from rich.console import Console
from rich.table import Table

from .ai_supervisor import (
    AISupervisor,
    build_gemini_reviewer,
    default_automation_status_path,
    format_automation_status_text,
    load_automation_status,
)
from .dashboard import launch_dashboard
from .config import get_settings
from .db import (
    fetch_latest_candidates,
    fetch_latest_paper_trading_run,
    fetch_latest_paper_strategy_runs,
    fetch_latest_premarket_signals,
    fetch_latest_replay_report,
    fetch_latest_scan_id,
    fetch_latest_session_decisions,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
    insert_social_signals,
)
from .providers.live_market import build_live_market_provider
from .providers.social import SocialMentionsCSVProvider
from .services.replay_pipeline import ReplayPipeline
from .services.report_builder import ReportBuilder
from .services.market_activity import MarketActivityScanner
from .services.paper_trading import (
    PAPER_STRATEGY_LABELS,
    PRIMARY_PAPER_STRATEGY,
    PaperTradingCoordinator,
)
from .services.trade_plan import TradePlanService
from .services.social_monitor import SocialMonitor
from .services.universe_builder import UniverseBuilder
from .services.watchlist_builder import WatchlistBuilder
from .snapshot_dashboard import launch_snapshot_dashboard

app = typer.Typer(help="Penny stock radar CLI.")
console = Console()

TRADE_CALL_MAP = {
    "OPENING_RANGE_CANDIDATE": "시초 후보",
    "CONDITIONAL_ENTRY": "조건부 진입",
    "NEWS_CHECK_FIRST": "재료 확인 전",
    "WAIT_PULLBACK": "눌림 대기",
    "NO_CHASE": "추격 금지",
}


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
    table.add_column("Context")
    table.add_column("Reasons")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{row['total_score']:.2f}",
            f"{row['catalyst_score']:.2f}",
            f"{row['technical_score']:.2f}",
            f"{row['sympathy_score']:.2f}",
            f"{row['market_context_score']:.2f}" if row["market_context_score"] is not None else "0.00",
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
    settings = get_settings()
    init_database(settings.database_path)
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
            _format_optional_number(row.last_price, 4),
            _format_optional_number(row.pct_change, 2),
            _format_optional_number(row.volume, 0),
            _format_optional_number(row.dollar_volume, 0),
            _format_optional_percent(row.spread_pct),
            _trade_call_label(row.analysis_label),
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
            _format_optional_number(row.volume, 0),
            _format_optional_number(row.dollar_volume, 0),
            _format_optional_number(row.pct_change, 2),
            _format_optional_percent(row.spread_pct),
            _trade_call_label(row.analysis_label),
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
            _trade_call_label(row.analysis_label),
        )

    console.print(
        f"Scanned {result.scanned_symbol_count} symbols from the {settings.market_scope_label} scope for the {result.market_phase} phase."
    )
    console.print(pct_table)
    console.print(volume_table)
    console.print(outcome_table)
    if result.comparison_csv is not None:
        console.print(f"Prediction comparison CSV written to [bold]{result.comparison_csv}[/bold]")


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


@app.command("export-dashboard-html")
def export_dashboard_html(
    output_path: Path = typer.Option(
        Path("sample_outputs/radar_dashboard.html"),
        help="Path to export a standalone snapshot dashboard HTML file.",
    ),
    limit: int = typer.Option(20, help="Rows to include from each latest snapshot."),
) -> None:
    """Export a standalone HTML snapshot dashboard without launching Streamlit."""
    settings = get_settings()
    init_database(settings.database_path)
    builder = ReportBuilder()
    builder.export_html(settings.database_path, output_path, limit=limit)
    console.print(f"Exported standalone dashboard to [bold]{output_path}[/bold]")


@app.command("snapshot-dashboard")
def snapshot_dashboard(
    output_path: Path = typer.Option(
        Path("sample_outputs/radar_dashboard.html"),
        help="Path to export the snapshot dashboard HTML file.",
    ),
    limit: int = typer.Option(20, help="Rows to include from each latest snapshot."),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the exported HTML file after generating it.",
    ),
) -> None:
    """Build and optionally open the standalone snapshot dashboard."""
    settings = get_settings()
    init_database(settings.database_path)
    html_path, opened = launch_snapshot_dashboard(
        settings.database_path,
        output_path=output_path,
        limit=limit,
        open_browser=open_browser,
    )
    console.print(f"Snapshot dashboard exported to [bold]{html_path}[/bold]")
    if open_browser:
        if opened:
            console.print("Opened the snapshot dashboard in your default browser.")
        else:
            console.print(
                "Automatic browser open did not succeed. Open the exported HTML file manually."
            )


@app.command("ai-supervisor")
def ai_supervisor(
    check_interval_seconds: int = typer.Option(
        3600,
        help="How often to re-check the workspace when running continuously.",
    ),
    refresh_if_older_than_minutes: int = typer.Option(
        15,
        help="Run the full pipeline when the latest scan is older than this many minutes.",
    ),
    snapshot_output: Path = typer.Option(
        Path("sample_outputs/radar_dashboard.html"),
        help="Snapshot dashboard HTML path.",
    ),
    review_output: Path = typer.Option(
        Path("automation/inbox/gemini_review.md"),
        help="Markdown path where the Gemini sidecar review is written.",
    ),
    prompt_path: Path = typer.Option(
        Path("automation/prompts/gemini_reviewer.md"),
        help="Prompt template path for the Gemini reviewer.",
    ),
    log_path: Path = typer.Option(
        Path("automation/logs/ai_supervisor.log"),
        help="Log file path for the local AI supervisor.",
    ),
    run_once: bool = typer.Option(
        False,
        "--run-once/--watch",
        help="Run a single supervisor pass and exit instead of staying in a loop.",
    ),
) -> None:
    """Run the local Gemini sidecar supervisor for this workspace."""
    settings = get_settings()
    init_database(settings.database_path)
    supervisor = AISupervisor(
        settings,
        check_interval_seconds=check_interval_seconds,
        refresh_if_older_than_minutes=refresh_if_older_than_minutes,
        snapshot_output=snapshot_output,
        review_output=review_output,
        prompt_path=prompt_path,
        log_path=log_path,
        reviewer=build_gemini_reviewer(settings),
    )

    try:
        while True:
            result = supervisor.run_once()
            if result.ok:
                console.print(result.message)
            else:
                console.print(f"[red]{result.message}[/red]")
                if run_once:
                    raise typer.Exit(code=1)

            if run_once:
                break

            console.print(f"Sleeping for {check_interval_seconds} seconds.")
            time.sleep(check_interval_seconds)
    except KeyboardInterrupt:
        console.print("AI supervisor stopped by user.")


@app.command("automation-status")
def automation_status(
    format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Show the latest public automation status snapshot."""
    normalized = (format or "text").strip().lower()
    if normalized not in {"text", "json"}:
        raise typer.BadParameter("Expected one of: text, json.")

    payload = load_automation_status(default_automation_status_path())
    if normalized == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    typer.echo(format_automation_status_text(payload))


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
    settings = get_settings()
    init_database(settings.database_path)
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
) -> None:
    """Continuously run the automated paper-trading engine."""
    settings = get_settings()
    init_database(settings.database_path)
    engine = PaperTradingCoordinator(settings)

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
            console.print(f"Sleeping for {check_interval_seconds} seconds.")
            time.sleep(check_interval_seconds)
    except KeyboardInterrupt:
        console.print("Paper trader stopped by user.")


@app.command("show-paper-summary")
def show_paper_summary(
    order_limit: int = typer.Option(10, help="Recent paper orders to display."),
) -> None:
    """Show the latest paper-trading performance summary."""
    settings = get_settings()
    init_database(settings.database_path)
    run = fetch_latest_paper_trading_run(settings.database_path, PRIMARY_PAPER_STRATEGY)
    if run is None:
        console.print("No paper-trading run found. Run `psradar run-paper-trading` first.")
        raise typer.Exit(code=1)

    positions = fetch_paper_positions(settings.database_path, run.run_id)
    orders = fetch_paper_orders(settings.database_path, run.run_id)
    strategy_runs = fetch_latest_paper_strategy_runs(settings.database_path, limit=10)

    summary = Table(title="Paper Trading Summary")
    summary.add_column("Run")
    summary.add_column("Status")
    summary.add_column("Cash")
    summary.add_column("Equity")
    summary.add_column("Realized")
    summary.add_column("Unrealized")
    summary.add_column("Return%")
    summary.add_column("Win Rate")
    summary.add_column("Profit Factor")
    summary.add_column("R/R")
    summary.add_row(
        run.run_id[:8],
        f"{PAPER_STRATEGY_LABELS.get(run.strategy_name, run.strategy_name)} · {run.status}",
        f"{run.cash_balance:.2f}",
        f"{run.equity:.2f}",
        f"{run.realized_pnl:.2f}",
        f"{run.unrealized_pnl:.2f}",
        f"{run.total_return_pct:.2f}",
        f"{run.win_rate:.2f}",
        f"{run.profit_factor:.2f}",
        f"{run.reward_risk_ratio:.2f}",
    )

    comparison = Table(title="Paper Strategy Comparison")
    comparison.add_column("Strategy")
    comparison.add_column("Equity")
    comparison.add_column("Return%")
    comparison.add_column("Closed")
    comparison.add_column("Win Rate")
    comparison.add_column("Profit Factor")
    comparison.add_column("Max DD%")
    for strategy_run in strategy_runs:
        comparison.add_row(
            PAPER_STRATEGY_LABELS.get(strategy_run.strategy_name, strategy_run.strategy_name),
            f"{strategy_run.equity:.2f}",
            f"{strategy_run.total_return_pct:.2f}",
            str(strategy_run.closed_trade_count),
            f"{strategy_run.win_rate:.2f}",
            f"{strategy_run.profit_factor:.2f}",
            f"{strategy_run.max_drawdown_pct:.2f}",
        )

    position_table = Table(title="Paper Positions")
    position_table.add_column("Symbol")
    position_table.add_column("Status")
    position_table.add_column("Qty")
    position_table.add_column("Avg")
    position_table.add_column("Last")
    position_table.add_column("PnL")
    position_table.add_column("Stop")
    for row in positions:
        position_table.add_row(
            row.symbol,
            row.status,
            str(row.quantity),
            f"{row.average_entry_price:.4f}",
            _format_optional_number(row.last_price, 4),
            f"{row.total_pnl:.2f}",
            _format_optional_number(row.stop_price, 4),
        )

    order_table = Table(title="Recent Paper Orders")
    order_table.add_column("Time")
    order_table.add_column("Symbol")
    order_table.add_column("Action")
    order_table.add_column("Intent")
    order_table.add_column("Qty")
    order_table.add_column("Price")
    order_table.add_column("PnL")
    for row in orders[-order_limit:]:
        order_table.add_row(
            str(row.created_at),
            row.symbol,
            row.action,
            row.intent,
            str(row.quantity),
            f"{row.price:.4f}",
            _format_optional_number(row.realized_pnl, 2),
        )

    console.print(summary)
    console.print(comparison)
    console.print(position_table)
    console.print(order_table)
    console.print(
        f"CSV logs are in [bold]{settings.paper_trade_dir.resolve()}[/bold]"
    )


@app.command("trade-plan")
def trade_plan(
    phase: str = typer.Option(
        "auto",
        help="Which market phase to evaluate for the execution plan.",
    ),
) -> None:
    """Build a live execution plan for semi-auto intraday trading."""
    settings = get_settings()
    init_database(settings.database_path)
    service = TradePlanService(settings)
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
    return f"{value * 100:.2f}%"


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint


def _trade_call_label(value: str | None) -> str:
    if not value:
        return "-"
    return TRADE_CALL_MAP.get(value, value)
