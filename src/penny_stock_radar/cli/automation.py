from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from ..ai_supervisor import AISupervisor, build_gemini_reviewer
from ..dashboard import launch_dashboard
from ..services.report_builder import ReportBuilder
from ..snapshot_dashboard import launch_snapshot_dashboard
from .common import console

app = typer.Typer()


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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
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
    import penny_stock_radar.cli as root_cli

    normalized = (format or "text").strip().lower()
    if normalized not in {"text", "json"}:
        raise typer.BadParameter("Expected one of: text, json.")

    payload = root_cli.load_automation_status(root_cli.default_automation_status_path())
    if normalized == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    typer.echo(root_cli.format_automation_status_text(payload))


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
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    launch_dashboard(host=host, port=port, open_browser=open_browser)
