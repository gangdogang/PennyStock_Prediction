from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    fetch_latest_paper_trading_run,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
    upsert_paper_positions,
    upsert_paper_trading_run,
)
from penny_stock_radar.models import MarketActivity, PaperPosition
from penny_stock_radar.services.multiday_engine import MULTIDAY_PAPER_STRATEGY, MultidayTradingEngine


def _activity(
    symbol: str,
    *,
    phase: str,
    price: float,
    label: str,
    score: float,
    pct_rank: int = 1,
    volume_rank: int = 1,
    predicted: bool = True,
    pct_change: float = 25.0,
    previous_close: float = 1.0,
    reasons: list[str] | None = None,
    leader_persistence_score: float = 72.0,
    pullback_absorption_score: float = 70.0,
    trap_score: float = 18.0,
    behavioral_score: float = 72.0,
    watchlist_rank: int | None = None,
    bid_price: float | None = None,
    ask_price: float | None = None,
    volume: float = 500_000,
    dollar_volume: float | None = None,
    data_age_seconds: float | None = 0.0,
    has_live_trade: bool = True,
    has_live_quote: bool = True,
    market_status: str = "open",
) -> MarketActivity:
    return MarketActivity(
        symbol=symbol,
        market_phase=phase,
        source="test",
        last_price=price,
        bid_price=bid_price if bid_price is not None else max(price - 0.01, 0.0001),
        ask_price=ask_price if ask_price is not None else price + 0.01,
        previous_close=previous_close,
        pct_change=pct_change,
        volume=volume,
        dollar_volume=dollar_volume if dollar_volume is not None else price * volume,
        trade_size=1_000,
        spread_pct=0.02,
        market_status=market_status,
        market_data_at=datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc),
        data_age_seconds=data_age_seconds,
        has_live_trade=has_live_trade,
        has_live_quote=has_live_quote,
        pct_rank=pct_rank,
        volume_rank=volume_rank,
        watchlist_rank=watchlist_rank if watchlist_rank is not None else (1 if predicted else None),
        watchlist_score=4.5 if predicted else None,
        predicted=predicted,
        leader_persistence_score=leader_persistence_score,
        pullback_absorption_score=pullback_absorption_score,
        trap_score=trap_score,
        behavioral_score=behavioral_score,
        analysis_label=label,
        analysis_score=score,
        reasons=reasons or ["watchlist_predicted", "live_dollar_volume_strong"],
    )


def _open_position(
    *,
    run_id: str,
    symbol: str,
    now: datetime,
    quantity: int,
    average_entry_price: float,
    last_price: float,
    add_count: int = 0,
    highest_price: float | None = None,
    strategy_bucket: str = "starter",
    opened_days_ago: int = 1,
) -> PaperPosition:
    opened_at = now - timedelta(days=opened_days_ago)
    return PaperPosition(
        position_id=f"pos-{symbol.lower()}-open",
        run_id=run_id,
        symbol=symbol,
        status="OPEN",
        entry_phase="premarket",
        entry_label="OPENING_RANGE_CANDIDATE",
        quantity=quantity,
        average_entry_price=average_entry_price,
        last_price=last_price,
        cost_basis=quantity * average_entry_price,
        market_value=quantity * last_price,
        realized_pnl=0.0,
        unrealized_pnl=(last_price - average_entry_price) * quantity,
        total_pnl=(last_price - average_entry_price) * quantity,
        stop_price=average_entry_price * 0.95,
        planned_stop_price=average_entry_price * 0.95,
        planned_risk_pct=5.0,
        highest_price=highest_price if highest_price is not None else max(last_price, average_entry_price),
        add_count=add_count,
        partial_exit_count=0,
        strategy_bucket=strategy_bucket,
        fill_reference_price=average_entry_price,
        fill_slippage_pct=0.15,
        day_regime="normal",
        watchlist_rank_at_entry=1,
        entry_reasons=["multiday_starter"],
        exit_reasons=[],
        opened_at=opened_at,
        updated_at=now,
    )


def test_multiday_starter_entry_and_overnight_hold(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 4, 18, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = MultidayTradingEngine(settings, now_fn=lambda: current_time)

    first = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.8,
            )
        ],
        export_csv=False,
    )
    run = fetch_latest_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)

    assert first.entered_count == 1
    assert len([row for row in positions if row.status == "OPEN"]) == 1
    assert positions[0].strategy_bucket == "starter"
    assert "multiday_starter" in positions[0].entry_reasons

    current_time = datetime(2026, 4, 18, 15, 30, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.08,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=34.0,
                leader_persistence_score=78.0,
                pullback_absorption_score=75.0,
                behavioral_score=76.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 18, 21, 5, tzinfo=timezone.utc)
    second = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)
    open_position = next(row for row in positions if row.status == "OPEN")

    assert second.exited_count == 0
    assert second.open_position_count == 1
    assert any(reason.startswith("overnight_hold:") for reason in open_position.entry_reasons)


def test_multiday_overnight_hold_rejection_closes_position(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 4, 18, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = MultidayTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.8,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 18, 15, 50, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.97,
                label="NO_CHASE",
                score=1.8,
                predicted=False,
                pct_change=-4.0,
                leader_persistence_score=25.0,
                pullback_absorption_score=28.0,
                trap_score=70.0,
                behavioral_score=25.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 18, 21, 5, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )
    run = fetch_latest_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    closed_position = next(row for row in positions if row.symbol == "AAA")

    assert result.exited_count == 1
    assert closed_position.status == "CLOSED"
    assert closed_position.exit_reason == "overnight_hold_rejected"


def test_multiday_winner_add_updates_stage(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    run.cash_balance = 9_000.0
    run.equity = 10_050.0
    run.equity_peak = 10_050.0
    upsert_paper_trading_run(db_path, run)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=1.08,
                opened_days_ago=1,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.08,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=35.0,
                leader_persistence_score=76.0,
                pullback_absorption_score=72.0,
                behavioral_score=75.0,
            )
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)
    open_position = next(row for row in positions if row.status == "OPEN")

    assert result.added_count == 1
    assert open_position.add_count == 1
    assert open_position.strategy_bucket == "add_1"
    assert open_position.quantity > 100


def test_multiday_max_adds_env_override_changes_winner_add_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_add_scenario(db_path: Path, settings: AppSettings) -> tuple[int, int, str]:
        init_database(db_path)
        now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
        run = create_paper_trading_run(
            db_path,
            MULTIDAY_PAPER_STRATEGY,
            settings.paper_initial_capital,
        )
        run.cash_balance = 9_000.0
        run.equity = 10_050.0
        run.equity_peak = 10_050.0
        upsert_paper_trading_run(db_path, run)
        upsert_paper_positions(
            db_path,
            [
                _open_position(
                    run_id=run.run_id,
                    symbol="AAA",
                    now=now,
                    quantity=100,
                    average_entry_price=1.00,
                    last_price=1.08,
                    opened_days_ago=1,
                )
            ],
        )

        engine = MultidayTradingEngine(settings, now_fn=lambda: now)
        result = engine.process_market_activity(
            market_phase="regular",
            activity=[
                _activity(
                    "AAA",
                    phase="regular",
                    price=1.08,
                    label="CONDITIONAL_ENTRY",
                    score=4.9,
                    pct_change=35.0,
                    leader_persistence_score=76.0,
                    pullback_absorption_score=72.0,
                    behavioral_score=75.0,
                )
            ],
            export_csv=False,
        )
        position = next(row for row in fetch_paper_positions(db_path, run.run_id) if row.status == "OPEN")
        return result.added_count, position.add_count, position.strategy_bucket

    baseline_db_path = tmp_path / "baseline.sqlite3"
    baseline_settings = AppSettings(
        db_path=baseline_db_path,
        paper_trade_dir=tmp_path / "baseline_exports",
        live_market_provider="disabled",
    )
    baseline_added_count, baseline_add_count, baseline_bucket = run_add_scenario(
        baseline_db_path,
        baseline_settings,
    )

    monkeypatch.setenv("PENNY_STOCK_MULTIDAY_MAX_ADDS", "0")
    override_db_path = tmp_path / "override.sqlite3"
    override_settings = AppSettings(
        db_path=override_db_path,
        paper_trade_dir=tmp_path / "override_exports",
        live_market_provider="disabled",
    )
    override_added_count, override_add_count, override_bucket = run_add_scenario(
        override_db_path,
        override_settings,
    )

    assert baseline_settings.multiday_max_adds == 2
    assert baseline_added_count == 1
    assert baseline_add_count == 1
    assert baseline_bucket == "add_1"

    assert override_settings.multiday_max_adds == 0
    assert override_added_count == 0
    assert override_add_count == 0
    assert override_bucket == "starter"


def test_multiday_starter_entry_tracks_transaction_cost(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 4, 18, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = MultidayTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.8,
            )
        ],
        export_csv=False,
    )
    run = fetch_latest_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    open_position = next(row for row in positions if row.status == "OPEN")
    entry_order = orders[0]

    assert result.entered_count == 1
    assert run.total_transaction_cost > 0.0
    assert open_position.fees_paid_total > 0.0
    assert entry_order.transaction_cost > 0.0
    assert open_position.average_entry_price > entry_order.price


def test_multiday_winner_add_tracks_transaction_cost_and_partial_fill(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    run.cash_balance = 9_000.0
    run.equity = 10_050.0
    run.equity_peak = 10_050.0
    upsert_paper_trading_run(db_path, run)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=1.08,
                opened_days_ago=1,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.08,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=35.0,
                leader_persistence_score=76.0,
                pullback_absorption_score=72.0,
                behavioral_score=75.0,
                volume=1_000,
            )
        ],
        export_csv=False,
    )
    updated_run = fetch_latest_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY)
    assert updated_run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    open_position = next(row for row in positions if row.status == "OPEN")
    add_order = orders[-1]

    assert result.added_count == 1
    assert add_order.intent == "ADD"
    assert add_order.fill_status == "PARTIAL"
    assert add_order.remaining_quantity > 0
    assert "partial_fill_cap" in add_order.reasons
    assert updated_run.total_transaction_cost > 0.0
    assert open_position.fees_paid_total > 0.0
    assert add_order.transaction_cost > 0.0


def test_multiday_rejects_add_below_average_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=0.98,
                opened_days_ago=1,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.98,
                label="CONDITIONAL_ENTRY",
                score=4.8,
                pct_change=10.0,
                leader_persistence_score=78.0,
                pullback_absorption_score=75.0,
            )
        ],
        export_csv=False,
    )

    assert result.added_count == 0


def test_multiday_loser_replacement_rotates_into_stronger_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_adaptive_max_open_positions=1,
    )
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    run.cash_balance = 9_500.0
    run.equity = 9_950.0
    run.equity_peak = 10_000.0
    upsert_paper_trading_run(db_path, run)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="OLD",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=0.96,
                opened_days_ago=0,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "OLD",
                phase="regular",
                price=0.96,
                label="NO_CHASE",
                score=1.5,
                predicted=False,
                pct_change=-4.0,
                leader_persistence_score=22.0,
                pullback_absorption_score=20.0,
                trap_score=72.0,
                behavioral_score=20.0,
            ),
            _activity(
                "NEW",
                phase="regular",
                price=1.10,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=42.0,
                predicted=True,
                leader_persistence_score=78.0,
                pullback_absorption_score=74.0,
                behavioral_score=77.0,
            ),
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)
    old_position = next(row for row in positions if row.symbol == "OLD")
    new_position = next(row for row in positions if row.symbol == "NEW")

    assert result.entered_count == 1
    assert result.exited_count == 1
    assert old_position.exit_reason == "loser_replacement"
    assert new_position.status == "OPEN"


def test_multiday_skips_replacement_without_clear_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        live_market_provider="disabled",
        paper_adaptive_max_open_positions=1,
    )
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="OLD",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=0.99,
                opened_days_ago=1,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "NEW",
                phase="regular",
                price=1.02,
                label="WAIT_PULLBACK",
                score=4.0,
                pct_change=12.0,
                predicted=False,
                leader_persistence_score=50.0,
                pullback_absorption_score=50.0,
                behavioral_score=50.0,
                trap_score=30.0,
            )
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)
    old_position = next(row for row in positions if row.symbol == "OLD")

    assert result.entered_count == 0
    assert result.exited_count == 0
    assert old_position.status == "OPEN"


def test_multiday_day2_gap_fail_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        live_market_provider="disabled",
        paper_stop_loss_pct=15.0,
    )
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=0.97,
                opened_days_ago=1,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=0.90,
                label="WAIT_PULLBACK",
                score=3.2,
                predicted=False,
                pct_change=-10.0,
                previous_close=1.00,
                leader_persistence_score=35.0,
                pullback_absorption_score=35.0,
                behavioral_score=30.0,
                trap_score=40.0,
            )
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)

    assert result.exited_count == 1
    assert positions[0].exit_reason == "day2_gap_fail"


def test_multiday_day3_exhaustion_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 20, 15, 5, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=1.18,
                highest_price=1.20,
                opened_days_ago=2,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.18,
                label="NO_CHASE",
                score=2.0,
                predicted=False,
                pct_change=8.0,
                previous_close=1.15,
                leader_persistence_score=38.0,
                pullback_absorption_score=42.0,
                behavioral_score=45.0,
                trap_score=60.0,
            )
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)

    assert result.exited_count == 1
    assert positions[0].exit_reason == "day3_exhaustion"


def test_multiday_trail_exit_records_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 20, 15, 5, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    run = create_paper_trading_run(db_path, MULTIDAY_PAPER_STRATEGY, settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=1.18,
                highest_price=1.25,
                add_count=1,
                strategy_bucket="add_1",
                opened_days_ago=2,
            )
        ],
    )

    engine = MultidayTradingEngine(settings, now_fn=lambda: now)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.18,
                label="CONDITIONAL_ENTRY",
                score=4.2,
                predicted=False,
                pct_change=6.0,
                previous_close=1.20,
                leader_persistence_score=70.0,
                pullback_absorption_score=68.0,
                behavioral_score=70.0,
                trap_score=20.0,
            )
        ],
        export_csv=False,
    )
    positions = fetch_paper_positions(db_path, run.run_id)

    assert result.exited_count == 1
    assert positions[0].exit_reason == "trail_exit"
    assert "trail_exit" in positions[0].exit_reasons
