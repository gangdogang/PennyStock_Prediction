from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import create_snapshot_run, init_database, insert_universe_candidates
from penny_stock_radar.models import UniverseCandidate, WatchlistEntry
from penny_stock_radar.services.kis_historical import HistoricalIngestSummary
from penny_stock_radar.db import insert_watchlist


def test_report_backtest_coverage_command_rejects_invalid_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "report-backtest-coverage",
            "--market-date",
            "2026-04-10",
            "--session",
            "overnight",
        ],
    )

    assert result.exit_code == 1
    assert "Unsupported session for coverage report: overnight" in result.stdout


def test_capture_kis_l1_window_repeats_capture_and_reports_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        backtest_coverage_report_dir=tmp_path / "automation" / "state" / "backtest_coverage",
        backtest_coverage_gate_path=tmp_path / "automation" / "state" / "backtest_coverage_gate_status.json",
    )
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.0,
                reasons=["fixture"],
            )
        ],
    )

    summaries = [
        HistoricalIngestSummary(
            market_date="2026-04-21",
            requested_symbols=1,
            inserted_rows=1,
            source="kis_l1_snapshot",
            rows=1,
            distinct_minute_keys=1,
        ),
        HistoricalIngestSummary(
            market_date="2026-04-21",
            requested_symbols=1,
            inserted_rows=2,
            source="kis_l1_snapshot",
            rows=2,
            distinct_minute_keys=1,
            duplicate_minute_bucket_count=1,
            stale_timestamp_fallback_count=1,
            skipped_symbols=["AAA"],
        ),
    ]
    captured_symbols: list[list[str]] = []
    sleep_calls: list[float] = []

    class FakeService:
        def __init__(self, incoming_settings) -> None:
            assert incoming_settings.database_path == db_path
            self._index = 0

        def capture_l1_quotes(self, *, symbols):
            captured_symbols.append(list(symbols))
            summary = summaries[self._index]
            self._index += 1
            return summary

        def close(self) -> None:
            return None

    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)
    monkeypatch.setattr("penny_stock_radar.cli.backtest.KISHistoricalDataService", FakeService)
    monkeypatch.setattr("penny_stock_radar.cli.backtest.time.sleep", sleep_calls.append)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "capture-kis-l1-window",
            "--iterations",
            "2",
            "--interval-seconds",
            "0.01",
        ],
    )

    assert result.exit_code == 0
    assert captured_symbols == [["AAA"], ["AAA"]]
    assert sleep_calls == [0.01]
    assert "iteration=1 symbols=1 new_rows=1 distinct_minutes=1" in result.stderr
    assert "iteration=2 symbols=1 new_rows=2 distinct_minutes=1" in result.stderr
    assert "duplicate minute bucket rows=1" in result.stderr
    assert "stale timestamp fallback rows=1" in result.stderr
    assert "L1 capture pass 1/2" in result.stdout
    assert "L1 capture pass 2/2" in result.stdout
    assert "KIS L1 archive window stored 3 quotes across 2 passes" in result.stdout
    assert "No L1 quote returned: AAA" in result.stdout
    assert "L1 coverage gate failed" in result.stdout
    assert settings.backtest_coverage_gate_path.exists()
