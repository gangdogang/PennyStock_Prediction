from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import init_database


def test_archive_paper_performance_creates_transfer_zip(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    output_path = tmp_path / "paper_review.zip"
    init_database(db_path)
    export_dir.mkdir()
    (export_dir / "paper_trade_log.csv").write_text(
        "run_id,strategy_name,bucket,symbol,status,predicted,predictor_score,predictor_weight\n",
        encoding="utf-8",
    )
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "archive-paper-performance",
            "--export-dir",
            str(export_dir),
            "--output-path",
            str(output_path),
            "--allow-fail",
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    with ZipFile(output_path) as archive:
        names = set(archive.namelist())
    assert "paper_trading/paper_trade_log.csv" in names
    assert "paper_trading/paper_performance_gate.json" in names
