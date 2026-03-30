from __future__ import annotations

from pathlib import Path
import webbrowser

from .services.report_builder import ReportBuilder


DEFAULT_SNAPSHOT_OUTPUT = Path("sample_outputs/radar_dashboard.html")


def build_snapshot_dashboard(
    database_path: Path,
    output_path: Path = DEFAULT_SNAPSHOT_OUTPUT,
    limit: int = 20,
) -> Path:
    builder = ReportBuilder()
    builder.export_html(database_path, output_path, limit=limit)
    return output_path.resolve()


def launch_snapshot_dashboard(
    database_path: Path,
    output_path: Path = DEFAULT_SNAPSHOT_OUTPUT,
    limit: int = 20,
    open_browser: bool = True,
) -> tuple[Path, bool]:
    html_path = build_snapshot_dashboard(database_path, output_path=output_path, limit=limit)
    opened = False
    if open_browser:
        try:
            opened = bool(webbrowser.open(html_path.as_uri()))
        except Exception:
            opened = False
    return html_path, opened
