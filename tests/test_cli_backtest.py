from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    get_connection,
    init_database,
    insert_historical_l1_quotes,
    insert_historical_minute_bars,
    insert_universe_candidates,
)
from penny_stock_radar.models import (
    HistoricalL1Quote,
    HistoricalMinuteBar,
    UniverseCandidate,
    WatchlistEntry,
)
from penny_stock_radar.services.kis_historical import HistoricalIngestSummary
from penny_stock_radar.services.falsification_research import FalsificationResearchAuditor
from penny_stock_radar.db import insert_watchlist


EASTERN = ZoneInfo("America/New_York")


def _write_kis_consolidation_verdict(
    base_dir: Path,
    *,
    classification: str = "nbbo_consolidated",
    reason: str = "fixture verdict",
) -> None:
    path = base_dir / "automation" / "state" / "source_validation" / "latest_kis_consolidation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source_name": "kis_l1_snapshot",
                "classification": classification,
                "reason": reason,
                "evidence": {
                    "bid_exchange_distinct_count": 2,
                    "ask_exchange_distinct_count": 2,
                    "bid_ask_exchange_differ_rate": 0.5,
                    "quote_update_frequency_hz": 1.5,
                    "spread_distribution_p50": 0.02,
                    "spread_distribution_p90": 0.02,
                    "spread_distribution_p99": 0.02,
                    "sample_size": 40,
                    "sample_window": [
                        "2026-04-10T09:30:00-04:00",
                        "2026-04-10T09:30:39-04:00",
                    ],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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

        def capture_l1_quotes(self, *, symbols, quote_stamper=None):
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
    assert "iteration=1 symbols=1 universe=1 new_rows=1 distinct_minutes=1" in result.stderr
    assert "iteration=2 symbols=1 universe=1 new_rows=2 distinct_minutes=1" in result.stderr
    assert "duplicate minute bucket rows=1" in result.stderr
    assert "stale timestamp fallback rows=1" in result.stderr
    assert "L1 capture pass 1/2" in result.stdout
    assert "L1 capture pass 2/2" in result.stdout
    assert "KIS L1 archive window stored 3 quotes across 2 passes" in result.stdout
    assert "No L1 quote returned: AAA" in result.stdout
    assert "L1 coverage gate failed" in result.stdout
    assert settings.backtest_coverage_gate_path.exists()


def test_run_falsification_audit_writes_research_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    _write_kis_consolidation_verdict(tmp_path)
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
    bars = []
    for symbol in ("AAA", "BBB"):
        for minute, close_price in enumerate([1.00, 1.03, 1.06, 1.02, 1.08, 1.11, 1.05]):
            bars.append(
                HistoricalMinuteBar(
                    symbol=symbol,
                    market_date="2026-04-10",
                    market_phase="regular",
                    timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                    open_price=close_price,
                    high_price=close_price * 1.02,
                    low_price=close_price * 0.98,
                    close_price=close_price,
                    volume=10000 + minute,
                    spread_pct=0.02,
                    source="fixture",
                )
            )
    insert_historical_minute_bars(db_path, bars)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 9, 30, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.00,
                source="kis_l1_snapshot",
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "fixture",
            "--null-sample-count",
            "3",
        ],
    )

    assert result.exit_code == 0
    export_dir = tmp_path / "research_runs" / "fixture"
    report_path = export_dir / "research_audit_report.json"
    assert (export_dir / "run_manifest.json").exists()
    assert report_path.exists()
    assert (export_dir / "research_audit_summary.md").exists()
    assert (export_dir / "null_baseline_trades.csv").exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["interpretation"].startswith("This report is a falsification gate")
    assert report["null_benchmark"]["status"] == "ready"
    assert report["decision_gate"]["decision"] == "BLOCKED"
    assert "same_universe_random_entry" in report["benchmark_suite"]["missing_or_blocked_benchmarks"]
    assert "fixed_pct" in report["null_benchmark"]["by_geometry"]
    null_csv = (export_dir / "null_baseline_trades.csv").read_text(encoding="utf-8")
    assert "AAA" in null_csv
    assert "BBB" not in null_csv
    assert "Falsification audit" in result.stdout

    second_result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "fixture",
            "--null-sample-count",
            "3",
        ],
    )

    assert second_result.exit_code == 0
    assert (tmp_path / "research_runs" / "fixture_rerun_2" / "run_manifest.json").exists()


def test_run_falsification_audit_builds_matched_random_entry_benchmark(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    _write_kis_consolidation_verdict(tmp_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=2,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
            for symbol in ("AAA", "BBB")
        ],
    )
    bars = []
    for symbol in ("AAA", "BBB"):
        for minute, close_price in enumerate([1.00, 1.03, 1.06, 1.02, 1.08, 1.11, 1.05]):
            bars.append(
                HistoricalMinuteBar(
                    symbol=symbol,
                    market_date="2026-04-10",
                    market_phase="regular",
                    timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                    open_price=close_price,
                    high_price=close_price * 1.03,
                    low_price=close_price * 0.97,
                    close_price=close_price,
                    volume=10000 + minute,
                    spread_pct=0.01,
                    source="fixture",
                )
            )
    insert_historical_minute_bars(db_path, bars)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 9, 31, tzinfo=EASTERN),
                bid_price=1.02,
                ask_price=1.04,
                last_price=1.03,
                source="kis_l1_snapshot",
            )
        ],
    )
    trade_log = tmp_path / "paper_trade_log.csv"
    trade_log.write_text(
        "\n".join(
            [
                "event,bucket,symbol,market_date,entry_at,entry_price",
                "ENTRY,predictor_weighted,AAA,2026-04-10,2026-04-10T09:31:00-04:00,1.03",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "matched",
            "--null-sample-count",
            "3",
            "--strategy-trade-log",
            str(trade_log),
            "--strategy-bucket",
            "predictor_weighted",
        ],
    )

    assert result.exit_code == 0
    export_dir = tmp_path / "research_runs" / "matched"
    report = json.loads((export_dir / "research_audit_report.json").read_text(encoding="utf-8"))
    matched = report["matched_random_entry_benchmark"]
    assert matched["status"] == "ready"
    assert matched["strategy_entry_count"] == 1
    assert matched["completed_path_count"] >= 1
    assert "fixed_pct" in matched["by_geometry"]
    assert "same_universe_random_entry" not in report["benchmark_suite"]["missing_or_blocked_benchmarks"]
    matched_csv = (export_dir / "matched_random_entry_trades.csv").read_text(encoding="utf-8")
    assert "same_universe_random_entry" in matched_csv
    assert "BBB" in matched_csv
    assert "AAA" in matched_csv


def test_run_falsification_audit_blocks_matched_without_strategy_date_cost_overlap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    _write_kis_consolidation_verdict(tmp_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=2,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
            for symbol in ("AAA", "BBB")
        ],
    )
    bars = []
    for symbol in ("AAA", "BBB"):
        for minute, close_price in enumerate([1.00, 1.03, 1.06, 1.02]):
            bars.append(
                HistoricalMinuteBar(
                    symbol=symbol,
                    market_date="2026-04-10",
                    market_phase="regular",
                    timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                    open_price=close_price,
                    high_price=close_price * 1.03,
                    low_price=close_price * 0.97,
                    close_price=close_price,
                    volume=10000 + minute,
                    source="fixture",
                )
            )
    insert_historical_minute_bars(db_path, bars)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-09",
                timestamp=datetime(2026, 4, 9, 9, 30, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.00,
                source="kis_l1_snapshot",
            )
        ],
    )
    trade_log = tmp_path / "paper_trade_log.csv"
    trade_log.write_text(
        "\n".join(
            [
                "event,bucket,symbol,market_date,entry_at,entry_price",
                "ENTRY,predictor_weighted,AAA,2026-04-10,2026-04-10T09:31:00-04:00,1.03",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "matched_cost_no_overlap",
            "--strategy-trade-log",
            str(trade_log),
            "--strategy-bucket",
            "predictor_weighted",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (
            tmp_path
            / "research_runs"
            / "matched_cost_no_overlap"
            / "research_audit_report.json"
        ).read_text(encoding="utf-8")
    )
    matched = report["matched_random_entry_benchmark"]
    assert matched["status"] == "blocked"
    assert matched["reason"] == "cost_distribution_date_overlap_missing"
    assert matched["total_cost_sample_count"] > 0


def test_run_falsification_audit_cost_source_policy_excludes_alpaca_iex(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    _write_kis_consolidation_verdict(tmp_path)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 9, 30, tzinfo=EASTERN),
                bid_price=0.50,
                ask_price=1.50,
                last_price=None,
                source="alpaca_iex_historical_quotes",
            ),
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 9, 31, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.00,
                source="kis_l1_snapshot",
            ),
        ],
    )
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-10",
                market_phase="regular",
                timestamp=datetime(2026, 4, 10, 9, 30, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.0,
                volume=1000,
                spread_pct=0.75,
                source="alpaca_iex_diagnostic",
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "cost_policy",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (tmp_path / "research_runs" / "cost_policy" / "research_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    cost = report["cost_audit"]
    assert cost["l1_spread"]["count"] == 1
    assert round(cost["l1_spread"]["p50_pct"], 3) == 2.0
    assert cost["minute_spread"]["count"] == 0
    assert cost["l1_source_counts"] == {
        "alpaca_iex_historical_quotes": 1,
        "kis_l1_snapshot": 1,
    }
    assert cost["l1_cost_eligible_source_counts"] == {"kis_l1_snapshot": 1}
    assert cost["l1_diagnostic_only_source_counts"] == {
        "alpaca_iex_historical_quotes": 1
    }
    assert cost["minute_spread_diagnostic_only_source_counts"] == {
        "alpaca_iex_diagnostic": 1
    }
    assert "alpaca_iex_historical_quotes" in cost["excluded_l1_sources"]
    assert "alpaca_iex_diagnostic" in cost["excluded_minute_spread_sources"]


def test_run_falsification_audit_blocks_matched_when_strategy_date_has_only_alpaca_cost(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.chdir(tmp_path)
    _write_kis_consolidation_verdict(tmp_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=2,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            )
            for symbol in ("AAA", "BBB")
        ],
    )
    bars = []
    for symbol in ("AAA", "BBB"):
        for minute, close_price in enumerate([1.00, 1.03, 1.06, 1.02]):
            bars.append(
                HistoricalMinuteBar(
                    symbol=symbol,
                    market_date="2026-04-10",
                    market_phase="regular",
                    timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                    open_price=close_price,
                    high_price=close_price * 1.03,
                    low_price=close_price * 0.97,
                    close_price=close_price,
                    volume=10000 + minute,
                    source="fixture",
                )
            )
    insert_historical_minute_bars(db_path, bars)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-09",
                timestamp=datetime(2026, 4, 9, 9, 30, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.00,
                source="kis_l1_snapshot",
            ),
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 9, 30, tzinfo=EASTERN),
                bid_price=0.50,
                ask_price=1.50,
                last_price=None,
                source="alpaca_iex_historical_quotes",
            ),
        ],
    )
    trade_log = tmp_path / "paper_trade_log.csv"
    trade_log.write_text(
        "\n".join(
            [
                "event,bucket,symbol,market_date,entry_at,entry_price",
                "ENTRY,predictor_weighted,AAA,2026-04-10,2026-04-10T09:31:00-04:00,1.03",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "matched_alpaca_only_cost",
            "--strategy-trade-log",
            str(trade_log),
            "--strategy-bucket",
            "predictor_weighted",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (
            tmp_path
            / "research_runs"
            / "matched_alpaca_only_cost"
            / "research_audit_report.json"
        ).read_text(encoding="utf-8")
    )
    matched = report["matched_random_entry_benchmark"]
    assert matched["status"] == "blocked"
    assert matched["reason"] == "cost_distribution_eligible_source_missing"
    assert matched["strategy_cost_audit"]["l1_spread"]["count"] == 0
    assert matched["strategy_cost_audit"]["l1_diagnostic_only_source_counts"] == {
        "alpaca_iex_historical_quotes": 1
    }


def test_run_falsification_audit_blocks_null_without_point_in_time_universe(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-10",
                market_phase="regular",
                timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.0,
                volume=1000,
                source="fixture",
            )
            for minute in range(3)
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "no_pit",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (tmp_path / "research_runs" / "no_pit" / "research_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "blocked"
    assert report["null_benchmark"]["reason"] == "point_in_time_universe_missing"


def test_audit_pit_universe_reconstruction_reports_diagnostic_bar_input(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol=symbol,
                market_date="2026-04-10",
                market_phase="regular",
                timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.0,
                volume=1000,
                source="fixture",
            )
            for symbol in ("AAA", "BBB")
            for minute in range(3)
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "audit-pit-universe-reconstruction",
            "--db-path",
            str(db_path),
            "--output-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "pit_fixture",
            "--min-bars-per-symbol",
            "3",
        ],
    )

    assert result.exit_code == 0
    report_path = tmp_path / "research_runs" / "pit_fixture" / "pit_universe_audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["status"] == "diagnostic_reconstruction_possible"
    assert report["rows"][0]["decision"] == "diagnostic_bar_universe_possible"
    assert report["rows"][0]["diagnostic_bar_universe_count"] == 2
    assert "diagnostic-only" in report["interpretation"]


def test_audit_pit_universe_reconstruction_prefers_exact_pit_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
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
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-10",
                market_phase="regular",
                timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.0,
                volume=1000,
                source="fixture",
            )
            for minute in range(3)
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "audit-pit-universe-reconstruction",
            "--db-path",
            str(db_path),
            "--output-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "pit_exact",
            "--min-bars-per-symbol",
            "3",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (tmp_path / "research_runs" / "pit_exact" / "pit_universe_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["summary"]["status"] == "exact_pit_ready"
    assert report["rows"][0]["decision"] == "exact_point_in_time_ready"
    assert report["rows"][0]["exact_pit_passed_count"] == 1


def test_tag_pit_universe_scan_tags_explicit_scan_and_writes_diff(
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
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE scan_runs SET created_at = ? WHERE scan_id = ?",
            ("2026-04-10T11:00:00+00:00", snapshot.snapshot_id),
        )
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "tag-pit-universe-scan",
            "--scan-id",
            snapshot.snapshot_id,
            "--market-date",
            "2026-04-10",
            "--diff-output",
            str(tmp_path / "pit_diff.json"),
        ],
    )

    assert result.exit_code == 0
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT market_date, snapshot_role, point_in_time_tag FROM scan_runs WHERE scan_id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()
    assert row["market_date"] == "2026-04-10"
    assert row["snapshot_role"] == "point_in_time"
    assert row["point_in_time_tag"] == "retro_scan_created_at"
    diff = json.loads((tmp_path / "pit_diff.json").read_text(encoding="utf-8"))
    assert diff["point_in_time_count"] == 1


def test_tag_pit_universe_scan_rejects_after_cutoff_without_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path)
    snapshot = create_snapshot_run(db_path, source="historical", symbol_count=0)
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE scan_runs SET created_at = ? WHERE scan_id = ?",
            ("2026-04-10T14:00:00+00:00", snapshot.snapshot_id),
        )
    monkeypatch.setattr("penny_stock_radar.cli.get_settings", lambda: settings)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "tag-pit-universe-scan",
            "--scan-id",
            snapshot.snapshot_id,
            "--market-date",
            "2026-04-10",
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to tag scan after cutoff" in result.stdout


def test_run_falsification_audit_rejects_stale_point_in_time_universe(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-09",
        snapshot_role="point_in_time",
        point_in_time_tag="stale_fixture",
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
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-10",
                market_phase="regular",
                timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.9,
                close_price=1.0,
                volume=1000,
                source="fixture",
            )
            for minute in range(3)
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-falsification-audit",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "stale_pit",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(
        (tmp_path / "research_runs" / "stale_pit" / "research_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "blocked"
    assert report["null_benchmark"]["reason"] == "point_in_time_universe_missing"


def test_falsification_path_reports_ambiguous_stop_first_rate() -> None:
    auditor = FalsificationResearchAuditor()
    result = auditor._simulate_path(
        group=[
            {"high_price": 1.0, "low_price": 1.0, "close_price": 1.0},
            {"high_price": 1.06, "low_price": 0.94, "close_price": 1.0},
        ],
        entry_index=0,
        entry_price=1.0,
        stop_pct=0.05,
        cost_pct=0.0,
    )

    assert result["exit_reason"] == "ambiguous_stop_first"
    assert result["stop_before_1r"] is True
    assert result["ambiguous_stop_first"] is True
