from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import create_snapshot_run, init_database, insert_premkt_predictions, insert_watchlist
from penny_stock_radar.models import PremktPrediction, WatchlistEntry


def test_build_watchlist_command_shows_latest_non_reportable_watchlist(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)

    class FakeBuilder:
        def __init__(self, incoming_settings) -> None:
            assert incoming_settings.database_path == db_path

        def build(self, limit=None, lookback_hours=None):
            del limit, lookback_hours
            snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
            entries = [
                WatchlistEntry(
                    symbol="AAA",
                    total_score=4.0,
                    catalyst_score=1.0,
                    technical_score=1.0,
                    sympathy_score=0.5,
                    market_context_score=0.5,
                    low_float_bonus=1.0,
                    reasons=["test_reason"],
                )
            ]
            insert_watchlist(db_path, snapshot.snapshot_id, entries)
            return entries, [], {}

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)
    monkeypatch.setattr("penny_stock_radar.cli.WatchlistBuilder", FakeBuilder)

    runner = CliRunner()
    result = runner.invoke(app, ["build-watchlist", "--limit", "1", "--lookback-hours", "48"])

    assert result.exit_code == 0
    assert "Built watchlist with 1 entries from 0 matched filings." in result.stdout
    assert "AAA" in result.stdout


def test_run_premkt_predictor_command_shows_latest_predictions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)

    class FakePredictor:
        def __init__(self, incoming_settings) -> None:
            assert incoming_settings.database_path == db_path

        def run(self, limit=None, lookback_hours=None, output_path=None):
            del limit, lookback_hours, output_path
            snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
            insert_premkt_predictions(
                db_path,
                snapshot.snapshot_id,
                [
                    PremktPrediction(
                        symbol="AAA",
                        score=82.5,
                        max_hold_days=3,
                        entry_rationale="catalyst-backed low-float; reasons=8-K, low_float",
                        themes=["biotech"],
                        filing_summary="8-K | merger agreement",
                    )
                ],
            )
            return [
                PremktPrediction(
                    symbol="AAA",
                    score=82.5,
                    max_hold_days=3,
                    entry_rationale="catalyst-backed low-float; reasons=8-K, low_float",
                    themes=["biotech"],
                    filing_summary="8-K | merger agreement",
                )
            ]

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)
    monkeypatch.setattr("penny_stock_radar.cli.PremktPredictor", FakePredictor)

    runner = CliRunner()
    result = runner.invoke(app, ["run-premkt-predictor", "--limit", "1", "--lookback-hours", "48"])

    assert result.exit_code == 0
    assert "Stored 1 premarket predictions." in result.stdout
    assert "AAA" in result.stdout


def test_show_live_market_command_reports_missing_kis_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(
        db_path=db_path,
        live_market_provider="kis",
        kis_app_key=None,
        kis_app_secret=None,
    )

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(app, ["show-live-market", "--symbol", "AAA"])

    assert result.exit_code == 1
    assert "KIS API credentials are required" in result.stdout
