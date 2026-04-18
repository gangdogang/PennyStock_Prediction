from __future__ import annotations

from datetime import datetime, timezone

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    create_snapshot_run,
    fetch_latest_paper_strategy_runs,
    init_database,
    insert_premkt_predictions,
    upsert_paper_positions,
)
from penny_stock_radar.models import PaperPosition, PremktPrediction
from penny_stock_radar.services.engine_shared import collect_active_symbols
from penny_stock_radar.services.intraday_engine import (
    INTRADAY_ENGINE_SPEC,
    IntradayTradingEngine,
)
from penny_stock_radar.services.multiday_engine import (
    MULTIDAY_ENGINE_SPEC,
    MultidayTradingEngine,
)
from penny_stock_radar.services.paper_trading import PRIMARY_PAPER_STRATEGY


def _open_position(*, run_id: str, symbol: str, now: datetime) -> PaperPosition:
    return PaperPosition(
        position_id=f"pos-{symbol.lower()}",
        run_id=run_id,
        symbol=symbol,
        status="OPEN",
        entry_phase="premarket",
        entry_label="OPENING_RANGE_CANDIDATE",
        quantity=100,
        average_entry_price=1.0,
        last_price=1.05,
        cost_basis=100.0,
        market_value=105.0,
        realized_pnl=0.0,
        unrealized_pnl=5.0,
        total_pnl=5.0,
        stop_price=0.95,
        planned_stop_price=0.95,
        planned_risk_pct=5.0,
        highest_price=1.05,
        add_count=0,
        partial_exit_count=0,
        strategy_bucket="predicted_starter",
        fill_reference_price=1.0,
        fill_slippage_pct=0.15,
        day_regime="normal",
        watchlist_rank_at_entry=1,
        entry_reasons=["predicted_starter"],
        exit_reasons=[],
        opened_at=now,
        updated_at=now,
    )


def test_intraday_engine_exposes_legacy_active_symbols(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    run = create_paper_trading_run(db_path, PRIMARY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(db_path, [_open_position(run_id=run.run_id, symbol="AAA", now=now)])

    engine = IntradayTradingEngine(settings, now_fn=lambda: now)

    assert engine.spec.engine_id == INTRADAY_ENGINE_SPEC.engine_id
    assert engine.active_position_symbols() == ("AAA",)


def test_collect_active_symbols_merges_public_engine_symbols(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    run_a = create_paper_trading_run(db_path, PRIMARY_PAPER_STRATEGY, settings.paper_initial_capital)
    run_b = create_paper_trading_run(db_path, "baseline_pct_leader_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(run_id=run_a.run_id, symbol="AAA", now=now),
            _open_position(run_id=run_b.run_id, symbol="BBB", now=now),
        ],
    )

    engine_a = IntradayTradingEngine(settings, strategy_name=PRIMARY_PAPER_STRATEGY, now_fn=lambda: now)
    engine_b = IntradayTradingEngine(
        settings,
        strategy_name="baseline_pct_leader_v1",
        now_fn=lambda: now,
        strategy_mode="baseline_pct",
    )

    assert collect_active_symbols([engine_a, engine_b]) == ("AAA", "BBB")


def test_multiday_engine_is_explicit_scaffold(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    engine = MultidayTradingEngine(settings)

    assert engine.spec == MULTIDAY_ENGINE_SPEC
    result = engine.process_market_activity(market_phase="afterhours", activity=[], export_csv=False)

    assert result.strategy_name == engine.strategy_name
    assert result.actions == ["hold"]


def test_multiday_engine_can_load_prediction_reference(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_premkt_predictions(
        db_path,
        snapshot.snapshot_id,
        [
            PremktPrediction(
                symbol="AAA",
                score=81.0,
                max_hold_days=3,
                entry_rationale="catalyst-backed",
                themes=["biotech"],
                filing_summary="8-K | merger agreement",
            )
        ],
    )

    engine = MultidayTradingEngine(settings)

    assert engine.load_prediction_reference() == {"AAA": 81.0}


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, 0.0),
        (40.0, 0.0),
        (60.0, 0.5),
        (80.0, 1.0),
    ],
)
def test_intraday_engine_exposes_predictor_weight_mapping(tmp_path, score, expected) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")

    engine = IntradayTradingEngine(settings)

    assert engine.predictor_weight_for_score(score) == pytest.approx(expected)


def test_fetch_latest_paper_strategy_runs_keeps_same_strategy_buckets_separate(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)

    create_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        10_000.0,
        bucket="predictor_weighted",
    )
    create_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        10_000.0,
        bucket="momentum_only",
    )

    runs = fetch_latest_paper_strategy_runs(db_path, limit=10)
    adaptive_runs = [row for row in runs if row.strategy_name == PRIMARY_PAPER_STRATEGY]

    assert {row.bucket for row in adaptive_runs} == {"predictor_weighted", "momentum_only"}
