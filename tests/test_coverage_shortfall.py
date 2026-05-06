from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from typer.testing import CliRunner

import pytest

from penny_stock_radar.cli import app
from penny_stock_radar.db import get_connection, init_database
from penny_stock_radar.services.coverage_shortfall import estimate_shortfall


def test_coverage_shortfall_empty_when_targets_are_met(tmp_path: Path) -> None:
    db_path = tmp_path / "coverage.sqlite3"
    init_database(db_path)
    with get_connection(db_path) as connection:
        _insert_minute_bar(connection, "AAA", "2025-01-01", "2026-01-01T00:00:00+00:00")
        _insert_minute_bar(connection, "AAA", "2025-07-10", "2026-01-02T00:00:00+00:00")
        _insert_l1_quote(connection, "AAA", "2025-01-01", "vendor_nbbo_fixture")
        _insert_l1_quote(connection, "AAA", "2025-07-10", "vendor_nbbo_fixture")
        _insert_corporate_action(connection, "AAA", "2025-01-01", "2026-01-01T00:00:00+00:00")
        _insert_corporate_action(connection, "AAA", "2026-01-10", "2026-01-02T00:00:00+00:00")

    report = estimate_shortfall(
        db_path,
        {
            "target_minute_bars_months": 6,
            "target_cost_eligible_overlap_pct": 80,
            "target_corporate_action_months": 12,
            "vendor_quote_source": "databento_nbbo",
            "vendor_quote_cost_per_month_usd": 99,
        },
    )

    assert report.blockers == []
    assert report.total_calendar_days_archive_path == 0
    assert report.total_cost_usd_vendor_path == (0, 0)
    assert "operational_planning_only_not_decision_grade" in report.recommendation


def test_coverage_shortfall_estimates_minute_archive_days(tmp_path: Path) -> None:
    db_path = tmp_path / "coverage.sqlite3"
    init_database(db_path)
    with get_connection(db_path) as connection:
        _insert_minute_bar(connection, "AAA", "2026-01-01", "2026-02-01T00:00:00+00:00")
        _insert_minute_bar(connection, "AAA", "2026-04-01", "2026-03-03T00:00:00+00:00")

    report = estimate_shortfall(
        db_path,
        {
            "target_minute_bars_months": 6,
            "target_cost_eligible_overlap_pct": 0,
            "target_corporate_action_months": 0,
        },
    )

    blocker = _blocker(report, "minute_bars_months_shortfall")
    assert blocker.current_value == pytest.approx(90 / 30.4375, abs=0.0001)
    assert blocker.deficit == pytest.approx(6 - blocker.current_value, abs=0.0001)
    assert blocker.estimated_calendar_days_to_unblock_via_archive == 31


def test_coverage_shortfall_estimates_vendor_cost_when_cost_overlap_zero(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coverage.sqlite3"
    init_database(db_path)
    with get_connection(db_path) as connection:
        _insert_minute_dates(connection, symbol_count=5, date_count=10)

    report = estimate_shortfall(
        db_path,
        {
            "target_minute_bars_months": 6,
            "target_cost_eligible_overlap_pct": 80,
            "target_corporate_action_months": 0,
            "vendor_quote_source": "databento_nbbo",
            "vendor_quote_cost_per_month_usd": 99,
        },
    )

    blocker = _blocker(report, "cost_eligible_overlap_pct_shortfall")
    assert blocker.current_value == 0.0
    assert blocker.deficit == 80.0
    assert blocker.estimated_data_cost_usd_to_unblock == (2376, 2970)
    assert blocker.fastest_path == "vendor_purchase"
    assert report.total_cost_usd_vendor_path == (2376, 2970)


def test_coverage_shortfall_vendor_cost_scales_linearly_with_universe_size(
    tmp_path: Path,
) -> None:
    small_db = tmp_path / "small.sqlite3"
    large_db = tmp_path / "large.sqlite3"
    _build_no_cost_db(small_db, symbol_count=5)
    _build_no_cost_db(large_db, symbol_count=10)

    target = {
        "target_minute_bars_months": 6,
        "target_cost_eligible_overlap_pct": 80,
        "target_corporate_action_months": 0,
        "vendor_quote_source": "databento_nbbo",
        "vendor_quote_cost_per_month_usd": 99,
    }
    small = _blocker(estimate_shortfall(small_db, target), "cost_eligible_overlap_pct_shortfall")
    large = _blocker(estimate_shortfall(large_db, target), "cost_eligible_overlap_pct_shortfall")

    assert large.estimated_data_cost_usd_to_unblock == (
        small.estimated_data_cost_usd_to_unblock[0] * 2,
        small.estimated_data_cost_usd_to_unblock[1] * 2,
    )


def test_report_coverage_shortfall_cli_writes_operational_planning_json(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coverage.sqlite3"
    out_path = tmp_path / "automation" / "state" / "shortfall" / "fixture.json"
    _build_no_cost_db(db_path, symbol_count=2)

    result = CliRunner().invoke(
        app,
        [
            "report-coverage-shortfall",
            "--db-path",
            str(db_path),
            "--target-minute-bars-months",
            "6",
            "--target-cost-eligible-overlap-pct",
            "80",
            "--target-corporate-action-months",
            "0",
            "--vendor-quote-source",
            "databento_nbbo",
            "--vendor-quote-cost-per-month-usd",
            "99",
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["planning_stamp"] == "operational_planning_only_not_decision_grade"
    assert payload["target"]["vendor_quote_cost_per_month_usd"] == 99.0
    assert "decision_grade" not in payload


def _build_no_cost_db(db_path: Path, *, symbol_count: int) -> None:
    init_database(db_path)
    with get_connection(db_path) as connection:
        _insert_minute_dates(connection, symbol_count=symbol_count, date_count=10)


def _insert_minute_dates(
    connection,
    *,
    symbol_count: int,
    date_count: int,
) -> None:
    start = date(2026, 1, 1)
    for day_index in range(date_count):
        market_date = (start + timedelta(days=day_index)).isoformat()
        for symbol_index in range(symbol_count):
            symbol = f"S{symbol_index:03d}"
            _insert_minute_bar(
                connection,
                symbol,
                market_date,
                f"2026-02-{day_index + 1:02d}T00:00:00+00:00",
            )


def _insert_minute_bar(
    connection,
    symbol: str,
    market_date: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO historical_minute_bars (
            symbol, market_date, market_phase, bar_at, open_price, high_price,
            low_price, close_price, volume, bid_price, ask_price, spread_pct, source, created_at
        )
        VALUES (?, ?, 'regular', ?, 1, 1, 1, 1, 100, NULL, NULL, NULL, 'fixture', ?)
        """,
        (symbol, market_date, f"{market_date}T14:30:00+00:00", created_at),
    )


def _insert_l1_quote(
    connection,
    symbol: str,
    market_date: str,
    source: str,
) -> None:
    connection.execute(
        """
        INSERT INTO historical_l1_quotes (
            symbol, market_date, quote_at, bid_price, ask_price, last_price, source, created_at
        )
        VALUES (?, ?, ?, 1.00, 1.01, NULL, ?, '2026-01-01T00:00:00+00:00')
        """,
        (symbol, market_date, f"{market_date}T14:30:00+00:00", source),
    )


def _insert_corporate_action(
    connection,
    symbol: str,
    effective_date: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO corporate_actions (
            source, source_record_id, action_category, action_subtype, symbol,
            effective_date, raw_payload, created_at
        )
        VALUES ('fixture', ?, 'symbol_change', 'symbol_change', ?, ?, '{}', ?)
        """,
        (f"{symbol}_{effective_date}", symbol, effective_date, created_at),
    )


def _blocker(report, name: str):
    return next(item for item in report.blockers if item.blocker_name == name)
