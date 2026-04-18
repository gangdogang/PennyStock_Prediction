from __future__ import annotations

from pathlib import Path

import typer

from ..services.replay_pipeline import ReplayPipeline
from .common import console
from .premkt import show_premarket, show_report, show_session

app = typer.Typer()


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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    pipeline = ReplayPipeline(settings)
    replay_path = pipeline.generate_mock_replay(output_path=output_csv)
    pipeline.analyze_replay(replay_path, export_json=export_json)
    console.print(f"Completed replay pipeline for [bold]{replay_path}[/bold]")
    show_premarket(limit=settings.watchlist_limit)
    show_session(limit=settings.watchlist_limit)
    show_report()
