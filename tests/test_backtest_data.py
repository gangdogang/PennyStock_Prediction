from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_historical_coverage_reports,
    fetch_historical_halt_events,
    init_database,
    insert_historical_l1_quotes,
    insert_historical_minute_bars,
    insert_universe_candidates,
)
from penny_stock_radar.models import (
    HistoricalCoverageReport,
    HistoricalL1Quote,
    HistoricalMinuteBar,
    UniverseCandidate,
)
from penny_stock_radar.services.backtest_data import BacktestDataManager

EASTERN = ZoneInfo("America/New_York")


def test_backtest_data_manager_resolves_point_in_time_universe_and_diff(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    historical = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=2,
        market_date="2026-04-10",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    current = create_snapshot_run(db_path, source="current", symbol_count=2)
    insert_universe_candidates(
        db_path,
        historical.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="BBB",
                company_name="BBB Corp",
                exchange="Q",
                price=1.2,
                passed_filters=True,
                filter_reasons=[],
            ),
        ],
    )
    insert_universe_candidates(
        db_path,
        current.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.0,
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="CCC",
                company_name="CCC Corp",
                exchange="Q",
                price=1.4,
                passed_filters=True,
                filter_reasons=[],
            ),
        ],
    )

    manager = BacktestDataManager(AppSettings(db_path=db_path))
    universe_rows = manager.fetch_point_in_time_universe("2026-04-10")
    report = manager.build_universe_difference_report("2026-04-10")

    assert [row["symbol"] for row in universe_rows] == ["AAA", "BBB"]
    assert report.point_in_time_scan_id == historical.snapshot_id
    assert report.current_scan_id == current.snapshot_id
    assert report.added_symbols == ["CCC"]
    assert report.removed_symbols == ["BBB"]
    assert report.common_symbols == 1


def test_backtest_data_manager_builds_l1_coverage_report(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 4, 0, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.0,
                source="fixture",
            ),
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 4, 1, tzinfo=EASTERN),
                bid_price=1.00,
                ask_price=1.02,
                last_price=1.01,
                source="fixture",
            ),
        ],
    )

    settings = AppSettings(
        db_path=db_path,
        backtest_coverage_report_dir=tmp_path / "automation" / "state" / "backtest_coverage",
        backtest_coverage_gate_path=tmp_path / "automation" / "state" / "backtest_coverage_gate_status.json",
        backtest_coverage_gate_pct=60.0,
    )
    manager = BacktestDataManager(settings)
    report = manager.build_l1_coverage_report(
        "2026-04-10",
        symbols=["AAA", "BBB"],
        session="premarket",
        source="fixture",
    )

    persisted = fetch_historical_coverage_reports(
        db_path,
        market_date="2026-04-10",
        dataset_kind="l1_quote_premarket",
    )
    assert report.expected_symbol_count == 2
    assert report.covered_symbol_count == 1
    assert report.symbol_coverage_pct == 50.0
    assert report.expected_interval_count == 660
    assert report.covered_interval_count == 2
    assert report.interval_coverage_pct == pytest.approx((2 / 660) * 100.0)
    assert "session=premarket" in report.notes
    assert persisted

    report_path = manager.coverage_report_output_path(report)
    assert report_path == (
        settings.backtest_coverage_report_dir / "2026-04-10_l1_quote_premarket.json"
    )
    exported_report_path = manager.export_coverage_report_json(report)
    assert exported_report_path == report_path.resolve()

    report_payload = json.loads(exported_report_path.read_text(encoding="utf-8"))
    assert report_payload["market_date"] == "2026-04-10"
    assert report_payload["dataset_kind"] == "l1_quote_premarket"
    assert report_payload["source"] == "fixture"
    assert report_payload["expected_symbol_count"] == 2
    assert report_payload["covered_symbol_count"] == 1
    assert report_payload["covered_interval_count"] == 2
    assert report_payload["created_at"]

    gate_status = manager.build_coverage_gate_status(
        report,
        report_path=exported_report_path,
    )
    assert gate_status.session == "premarket"
    assert gate_status.gate_name == "step0_l1_coverage_60"
    assert gate_status.threshold_pct == pytest.approx(60.0)
    assert (
        gate_status.decision_basis
        == "symbol_coverage_pct>=60.0 and interval_coverage_pct>=60.0 and no_l1_timestamp_quality_failures"
    )
    assert gate_status.symbol_gate_passed is False
    assert gate_status.interval_gate_passed is False
    assert gate_status.gate_passed is False
    assert gate_status.status == "failed"
    assert gate_status.report_path == str(exported_report_path)
    assert gate_status.report_created_at == report.created_at

    gate_path = manager.export_coverage_gate_status(gate_status)
    assert gate_path == settings.backtest_coverage_gate_path.resolve()
    gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate_payload["status"] == "failed"
    assert gate_payload["gate_passed"] is False
    assert gate_payload["report_path"] == str(exported_report_path)
    assert gate_payload["updated_at"]


def test_backtest_data_manager_normalizes_tz_variant_coverage_buckets(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 8, 0, 30, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.0,
                source="fixture",
            ),
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 12, 0, 45, tzinfo=timezone.utc),
                bid_price=0.98,
                ask_price=1.02,
                last_price=1.0,
                source="fixture",
            ),
        ],
    )

    manager = BacktestDataManager(AppSettings(db_path=db_path))
    report = manager.build_l1_coverage_report(
        "2026-04-10",
        symbols=["AAA"],
        session="premarket",
        source="fixture",
    )

    assert report.covered_symbol_count == 1
    assert report.covered_interval_count == 1
    assert report.interval_coverage_pct == pytest.approx((1 / 330) * 100.0)


def test_backtest_data_manager_marks_l1_timestamp_quality_failures(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 9, 9, 0, tzinfo=EASTERN),
                bid_price=0.99,
                ask_price=1.01,
                last_price=1.0,
                source="kis_l1_snapshot",
                created_at=datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc),
            ),
        ],
    )

    manager = BacktestDataManager(AppSettings(db_path=db_path))
    report = manager.build_l1_coverage_report(
        "2026-04-10",
        symbols=["AAA"],
        session="premarket",
        source="kis_l1_snapshot",
    )
    gate_status = manager.build_coverage_gate_status(report)

    assert "snapshot_date_mismatch_count=1" in report.notes
    assert "timestamp_drift_gt_120m_count=1" in report.notes
    assert gate_status.status == "failed"
    assert gate_status.last_error == "snapshot_date_mismatch_count=1; timestamp_drift_gt_120m_count=1"


def test_backtest_data_manager_applies_gate_threshold_override(tmp_path: Path) -> None:
    manager = BacktestDataManager(AppSettings(db_path=tmp_path / "radar.sqlite3"))
    report = HistoricalCoverageReport(
        market_date="2026-04-10",
        dataset_kind="l1_quote_premarket",
        source="fixture",
        expected_symbol_count=2,
        covered_symbol_count=2,
        symbol_coverage_pct=100.0,
        expected_interval_count=660,
        covered_interval_count=395,
        interval_coverage_pct=59.9,
        notes=["session=premarket"],
    )

    default_gate = manager.build_coverage_gate_status(report, gate_threshold_pct=60.0)
    relaxed_gate = manager.build_coverage_gate_status(report, gate_threshold_pct=55.0)

    assert default_gate.symbol_gate_passed is True
    assert default_gate.interval_gate_passed is False
    assert default_gate.gate_passed is False
    assert default_gate.status == "failed"
    assert relaxed_gate.gate_name == "step0_l1_coverage_55"
    assert relaxed_gate.gate_passed is True
    assert relaxed_gate.status == "passed"


def test_backtest_data_manager_rejects_non_l1_gate_reports(tmp_path: Path) -> None:
    manager = BacktestDataManager(AppSettings(db_path=tmp_path / "radar.sqlite3"))
    report = HistoricalCoverageReport(
        market_date="2026-04-10",
        dataset_kind="minute_bar_premarket",
        source="fixture",
        expected_symbol_count=2,
        covered_symbol_count=2,
        symbol_coverage_pct=100.0,
        expected_interval_count=660,
        covered_interval_count=660,
        interval_coverage_pct=100.0,
    )

    with pytest.raises(ValueError, match="expects an L1 coverage report dataset_kind"):
        manager.build_coverage_gate_status(report)


def test_backtest_data_manager_infers_halt_events_from_minute_bars(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="HALT",
                market_date="2026-04-10",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 10, 4, 0, tzinfo=EASTERN),
                open_price=1.0,
                high_price=1.1,
                low_price=0.99,
                close_price=1.05,
                volume=10_000,
                source="fixture",
            ),
            HistoricalMinuteBar(
                symbol="HALT",
                market_date="2026-04-10",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 10, 4, 1, tzinfo=EASTERN),
                open_price=1.05,
                high_price=1.05,
                low_price=1.04,
                close_price=1.04,
                volume=0,
                source="fixture",
            ),
            HistoricalMinuteBar(
                symbol="HALT",
                market_date="2026-04-10",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 10, 4, 2, tzinfo=EASTERN),
                open_price=1.04,
                high_price=1.04,
                low_price=1.03,
                close_price=1.03,
                volume=0,
                source="fixture",
            ),
            HistoricalMinuteBar(
                symbol="HALT",
                market_date="2026-04-10",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 10, 4, 10, tzinfo=EASTERN),
                open_price=1.20,
                high_price=1.22,
                low_price=1.18,
                close_price=1.21,
                volume=25_000,
                source="fixture",
            ),
        ],
    )

    manager = BacktestDataManager(AppSettings(db_path=db_path))
    inferred = manager.infer_and_store_halt_events("2026-04-10", symbols=["HALT"])
    persisted = fetch_historical_halt_events(db_path, market_date="2026-04-10", symbol="HALT")

    assert len(inferred) >= 2
    reasons = {event.reason for event in inferred}
    assert {"minute_gap_detected", "zero_volume_stretch"} <= reasons
    assert persisted
