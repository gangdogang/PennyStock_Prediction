from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.db import (
    create_snapshot_run,
    init_database,
    insert_historical_minute_bars,
    insert_universe_candidates,
)
from penny_stock_radar.models import HistoricalMinuteBar, UniverseCandidate
from penny_stock_radar.services.benchmark_suite import (
    BenchmarkSuiteOptions,
    BenchmarkSuiteRunner,
    CashNoTrade,
    EntryEvent,
    OppositeSide,
    RandomTimeWithinDay,
    SameUniverseRandomEntry,
    TopGainerNaive,
    VolumeLeaderNaive,
)

EASTERN = ZoneInfo("America/New_York")


def test_benchmark_generators_are_deterministic_by_seed() -> None:
    market_date = date(2026, 4, 10)
    universe = ["AAA", "BBB", "CCC"]
    strategy_entries = (
        EntryEvent(
            benchmark="strategy",
            symbol="AAA",
            market_date=market_date,
            entry_at=datetime(2026, 4, 10, 9, 41, tzinfo=EASTERN),
            direction="long",
        ),
    )
    leaders = {
        (market_date, "AAA"): {"pct_change": 3.0, "volume": 100.0},
        (market_date, "BBB"): {"pct_change": 9.0, "volume": 500.0},
        (market_date, "CCC"): {"pct_change": 1.0, "volume": 900.0},
    }
    benchmarks = [
        SameUniverseRandomEntry(entries_per_date=3),
        RandomTimeWithinDay(strategy_entries=strategy_entries),
        TopGainerNaive(market_leaders=leaders, top_n=2),
        VolumeLeaderNaive(market_leaders=leaders, top_n=2),
        OppositeSide(strategy_entries=strategy_entries),
        CashNoTrade(),
    ]

    for benchmark in benchmarks:
        first = benchmark.generate_entries(universe, [market_date], seed=17)
        second = benchmark.generate_entries(universe, [market_date], seed=17)
        assert first == second

    assert [entry.symbol for entry in benchmarks[2].generate_entries(universe, [market_date], 17)] == [
        "BBB",
        "AAA",
    ]
    assert [entry.symbol for entry in benchmarks[3].generate_entries(universe, [market_date], 17)] == [
        "CCC",
        "BBB",
    ]


def test_opposite_side_preserves_strategy_symbol_and_time_but_flips_direction() -> None:
    strategy_entry = EntryEvent(
        benchmark="strategy",
        symbol="AAA",
        market_date=date(2026, 4, 10),
        entry_at=datetime(2026, 4, 10, 9, 37, tzinfo=EASTERN),
        direction="long",
    )

    entries = OppositeSide(strategy_entries=(strategy_entry,)).generate_entries(
        ["AAA"],
        [strategy_entry.market_date],
        seed=1,
    )

    assert len(entries) == 1
    assert entries[0].symbol == strategy_entry.symbol
    assert entries[0].entry_at == strategy_entry.entry_at
    assert entries[0].direction == "short"
    assert entries[0].strategy_symbol == strategy_entry.symbol
    assert entries[0].strategy_entry_at == strategy_entry.entry_at


def test_cash_no_trade_kpi_is_zero_when_suite_is_ready(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_benchmark_db(db_path, source="full_nbbo")

    result = BenchmarkSuiteRunner().run(
        BenchmarkSuiteOptions(
            db_path=db_path,
            export_root=tmp_path / "research_runs",
            run_id="ready",
            market_dates=(date(2026, 4, 10),),
            strategy_entries=(
                EntryEvent(
                    benchmark="strategy",
                    symbol="AAA",
                    market_date=date(2026, 4, 10),
                    entry_at=datetime(2026, 4, 10, 9, 31, tzinfo=EASTERN),
                ),
            ),
            strategy_kpis={"trade_count": 2, "net_pnl": 5.0},
            seed=7,
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "ready"
    cash = report["benchmark_kpis"]["cash_no_trade"]
    assert cash["trade_count"] == 0
    assert cash["net_pnl"] == 0.0
    assert report["incremental_vs_each"]["cash_no_trade"]["net_pnl"] == 5.0


def test_benchmark_suite_refuses_generation_when_cost_policy_is_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_benchmark_db(db_path, source="alpaca_iex_historical_quotes")

    result = BenchmarkSuiteRunner().run(
        BenchmarkSuiteOptions(
            db_path=db_path,
            export_root=tmp_path / "research_runs",
            run_id="blocked",
            market_dates=(date(2026, 4, 10),),
            seed=7,
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["reason"] == "cost_distribution_eligible_source_missing"
    assert report["decision_grade"]["decision_grade"] is False
    assert report["benchmark_entry_counts"] == {}
    assert "AAA" not in result.entries_path.read_text(encoding="utf-8")
    assert {
        payload["status"]
        for payload in report["benchmark_kpis"].values()
    } == {"blocked"}


def test_run_benchmark_suite_cli_writes_blocked_report(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    _seed_benchmark_db(db_path, source="alpaca_iex_historical_quotes")

    result = CliRunner().invoke(
        app,
        [
            "run-benchmark-suite",
            "--db-path",
            str(db_path),
            "--export-root",
            str(tmp_path / "research_runs"),
            "--run-id",
            "cli_blocked",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
        ],
    )

    assert result.exit_code == 0
    report_path = tmp_path / "research_runs" / "cli_blocked" / "benchmark_suite_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["reason"] == "cost_distribution_eligible_source_missing"


def _seed_benchmark_db(db_path: Path, *, source: str) -> None:
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="fixture",
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
    prices = {
        "AAA": [1.00, 1.10, 1.20],
        "BBB": [1.00, 1.02, 1.03],
    }
    for symbol, closes in prices.items():
        for minute, close_price in enumerate(closes):
            bars.append(
                HistoricalMinuteBar(
                    symbol=symbol,
                    market_date="2026-04-10",
                    market_phase="regular",
                    timestamp=datetime(2026, 4, 10, 9, 30 + minute, tzinfo=EASTERN),
                    open_price=close_price,
                    high_price=close_price * 1.01,
                    low_price=close_price * 0.99,
                    close_price=close_price,
                    volume=1000 + (500 if symbol == "BBB" else 0) + minute,
                    spread_pct=0.01,
                    source=source,
                )
            )
    insert_historical_minute_bars(db_path, bars)
