from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import init_database


def _write_review_export(export_dir: Path, *, predictor_enabled: bool) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "paper_trade_log.csv").write_text(
        "run_id,strategy_name,bucket,symbol,status,predicted,predictor_score,predictor_weight,"
        "prediction_generated_at,prediction_cutoff_at,prediction_source\n"
        "run-1,auto_paper_v1,predictor_weighted,PRED,CLOSED,True,90.0,1.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,premkt_prediction\n"
        "run-2,auto_paper_v1,momentum_only,LIVE,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n"
        "run-3,auto_paper_v1,watchlist_blind_momentum,LIVE,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n",
        encoding="utf-8",
    )
    (export_dir / "paper_predictor_kpis.csv").write_text(
        "strategy_name,bucket,candidate_count,triggered_candidate_count,predicted_trade_count,closed_predicted_trade_count\n"
        "auto_paper_v1,predictor_weighted,1,1,1,1\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_comparison.csv").write_text(
        "strategy_name,bucket\n"
        "auto_paper_v1,predictor_weighted\n"
        "auto_paper_v1,momentum_only\n"
        "auto_paper_v1,watchlist_blind_momentum\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_trade_diff.csv").write_text(
        "symbol,predictor_status,momentum_status,pnl_diff\nPRED,CLOSED,OPEN,1.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_pair_diff.csv").write_text(
        "left_bucket,right_bucket,left_status,right_status,pnl_diff\n"
        "predictor_weighted,momentum_only,CLOSED,OPEN,1.0\n"
        "momentum_only,watchlist_blind_momentum,CLOSED,CLOSED,0.0\n"
        "predictor_weighted,watchlist_blind_momentum,CLOSED,OPEN,1.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_backtest_kpis.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "paper_execution_quality.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "paper_capacity_report.csv").write_text(
        "strategy_name,bucket,symbol,shares_pct_of_bar_volume,notional_pct_of_bar_dollar_volume,"
        "estimated_capacity_at_1pct_volume,estimated_capacity_at_2pct_volume,capacity_limited\n"
        "auto_paper_v1,predictor_weighted,PRED,0.5,0.5,100,200,False\n",
        encoding="utf-8",
    )
    (export_dir / "paper_intraday_edge_decay.csv").write_text(
        "strategy_name,bucket,decay_bucket,trade_count,win_rate,avg_r_multiple,total_net_pnl\n"
        "auto_paper_v1,predictor_weighted,0-5m,1,100,1.0,10\n",
        encoding="utf-8",
    )
    (export_dir / "paper_catalyst_kpis.csv").write_text(
        "strategy_name,bucket,catalyst_type,closed_trade_count,win_rate,expectancy_r,total_net_pnl,avg_hold_minutes\n"
        "auto_paper_v1,predictor_weighted,fda_or_clinical,1,100,1.0,10,4\n",
        encoding="utf-8",
    )
    (export_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "predictor_effect": {
                    "enabled": predictor_enabled,
                    "k1": 1.0 if predictor_enabled else 0.0,
                    "k2": 1.0 if predictor_enabled else 0.0,
                },
                "slippage_model": {"name": "l1_minute_volume_nonlinear_proxy"},
                "capacity_model": {"name": "bar_volume_participation_capacity"},
            }
        ),
        encoding="utf-8",
    )


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


def test_review_paper_performance_passes_for_enabled_predictor_export(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    _write_review_export(export_dir, predictor_enabled=True)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(app, ["review-paper-performance", "--export-dir", str(export_dir)])

    assert result.exit_code == 0
    assert '"status": "pass"' in result.stdout


def test_review_paper_performance_fails_for_disabled_predictor_export(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    _write_review_export(export_dir, predictor_enabled=False)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(app, ["review-paper-performance", "--export-dir", str(export_dir)])

    assert result.exit_code == 1
    assert "predictor_effect disabled" in result.stdout
