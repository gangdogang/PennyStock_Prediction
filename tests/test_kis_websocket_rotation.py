from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    fetch_historical_coverage_reports,
    init_database,
    insert_historical_l1_quotes,
)
from penny_stock_radar.models import HistoricalL1Quote
from penny_stock_radar.services.backtest_data import BacktestDataManager
from penny_stock_radar.services.falsification_research import (
    FalsificationAuditOptions,
    FalsificationResearchAuditor,
)
from penny_stock_radar.services.kis_websocket_rotation import (
    KisWebSocketRotationManager,
    RotationPolicy,
)
from penny_stock_radar.services.research_data_coverage import (
    ResearchDataCoverageAuditor,
    ResearchDataCoverageOptions,
)


def test_rotation_assigns_100_symbol_universe_and_measures_p90_gap() -> None:
    now = datetime(2026, 5, 6, 13, 0, tzinfo=timezone.utc)
    manager = KisWebSocketRotationManager(now_provider=lambda: now)
    universe = [f"S{i:03d}" for i in range(100)]
    priority = {symbol: float(100 - index) for index, symbol in enumerate(universe)}

    slots = manager.assign_tiers(
        universe,
        priority,
        RotationPolicy(tier1_size=30, tier2_window_seconds=300, tier2_concurrent=10),
    )

    counts = manager.slot_counts(slots)
    assert counts["tier1_continuous_symbol_count"] == 30
    assert counts["tier2_rotation_symbol_count"] == 70
    assert len(manager.active_symbols(slots)) == 40
    assert manager.rotation_gap_seconds_p90(slots, now=now) == 0.0

    now = now + timedelta(seconds=300)
    rotated = manager.next_rotation_step(slots, elapsed_seconds=300)
    active_tier2 = [
        symbol
        for symbol in manager.active_symbols(rotated)
        if rotated[symbol].tier == "tier2_rotation"
    ]

    assert active_tier2 == [f"S{i:03d}" for i in range(40, 50)]
    assert manager.rotation_gap_seconds_p90(rotated, now=now) == 300.0


def test_priority_change_reassigns_tier1_and_tier2() -> None:
    manager = KisWebSocketRotationManager(
        now_provider=lambda: datetime(2026, 5, 6, tzinfo=timezone.utc)
    )
    universe = ["AAA", "BBB", "CCC", "DDD"]

    first = manager.assign_tiers(
        universe,
        {"AAA": 4.0, "BBB": 3.0, "CCC": 2.0, "DDD": 1.0},
        RotationPolicy(tier1_size=2, tier2_concurrent=1),
    )
    second = manager.assign_tiers(
        universe,
        {"CCC": 4.0, "DDD": 3.0, "AAA": 2.0, "BBB": 1.0},
        RotationPolicy(tier1_size=2, tier2_concurrent=1),
    )

    assert {symbol for symbol, slot in first.items() if slot.tier == "tier1_continuous"} == {
        "AAA",
        "BBB",
    }
    assert {symbol for symbol, slot in second.items() if slot.tier == "tier1_continuous"} == {
        "CCC",
        "DDD",
    }


def test_quote_stamp_marks_tier1_continuous_and_tier2_rotation() -> None:
    manager = KisWebSocketRotationManager(
        now_provider=lambda: datetime(2026, 5, 6, tzinfo=timezone.utc)
    )
    manager.assign_tiers(
        ["AAA", "BBB"],
        {"AAA": 2.0, "BBB": 1.0},
        RotationPolicy(tier1_size=1, tier2_concurrent=1),
    )

    assert manager.stamp_quote({"symbol": "AAA"})["subscription_continuous"] is True
    assert manager.stamp_quote({"symbol": "BBB"})["subscription_continuous"] is False


def test_coverage_report_records_rotation_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    manager = BacktestDataManager(AppSettings(db_path=db_path))

    report = manager.build_l1_coverage_report(
        "2026-04-10",
        symbols=["AAA", "BBB", "CCC"],
        session="premarket",
        source="kis_l1_snapshot",
        tier1_continuous_symbol_count=2,
        tier2_rotation_symbol_count=1,
        rotation_gap_seconds_p90=300.0,
    )
    rows = fetch_historical_coverage_reports(db_path, market_date="2026-04-10")

    assert report.tier1_continuous_symbol_count == 2
    assert report.tier2_rotation_symbol_count == 1
    assert report.rotation_gap_seconds_p90 == 300.0
    assert rows[0]["tier1_continuous_symbol_count"] == 2
    assert rows[0]["tier2_rotation_symbol_count"] == 1
    assert rows[0]["rotation_gap_seconds_p90"] == 300.0


def test_cost_eligible_counts_only_subscription_continuous_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    quote_at = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=quote_at,
                bid_price=1.0,
                ask_price=1.1,
                source="full_nbbo",
                subscription_continuous=True,
            ),
            HistoricalL1Quote(
                symbol="BBB",
                market_date="2026-04-10",
                timestamp=quote_at,
                bid_price=1.0,
                ask_price=1.1,
                source="full_nbbo",
                subscription_continuous=False,
            ),
        ],
    )

    cost_audit = FalsificationResearchAuditor()._cost_audit(  # noqa: SLF001
        FalsificationAuditOptions(db_path=db_path, export_root=tmp_path)
    )
    coverage = ResearchDataCoverageAuditor().run(
        ResearchDataCoverageOptions(
            db_path=db_path,
            output_root=tmp_path,
            run_id="coverage",
            start_date="2026-04-10",
            end_date="2026-04-10",
        )
    )

    assert cost_audit["l1_source_counts"] == {"full_nbbo": 2}
    assert cost_audit["l1_cost_eligible_source_counts"] == {"full_nbbo": 1}
    assert cost_audit["l1_spread"]["count"] == 1
    assert coverage.report["by_date"][0]["l1_cost_eligible_quote_count"] == 1
