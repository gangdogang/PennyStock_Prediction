from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    fetch_latest_paper_trading_run,
    fetch_latest_paper_strategy_runs,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
    upsert_paper_positions,
)
from penny_stock_radar.models import MarketActivity, PaperPosition
from penny_stock_radar.services.momentum_advisor import MomentumAdvice, MomentumAdviceBundle
from penny_stock_radar.services.paper_trading import PaperTradingCoordinator, PaperTradingEngine


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
    pct_change: float = 20.0,
    reasons: list[str] | None = None,
    leader_persistence_score: float = 0.0,
    pullback_absorption_score: float = 0.0,
    trap_score: float = 0.0,
    behavioral_score: float = 0.0,
    watchlist_rank: int | None = None,
    bid_price: float | None = None,
    ask_price: float | None = None,
    data_age_seconds: float | None = 0.0,
    has_live_trade: bool = True,
    has_live_quote: bool = True,
) -> MarketActivity:
    return MarketActivity(
        symbol=symbol,
        market_phase=phase,
        source="test",
        last_price=price,
        bid_price=bid_price if bid_price is not None else max(price - 0.01, 0.0001),
        ask_price=ask_price if ask_price is not None else price + 0.01,
        previous_close=0.8,
        pct_change=pct_change,
        volume=500_000,
        dollar_volume=price * 500_000,
        trade_size=1_000,
        spread_pct=0.02,
        market_status="open",
        market_data_at=datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc),
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


class FakeAdvisor:
    def __init__(self, stances: dict[str, tuple[str, float, str]]) -> None:
        self.stances = stances

    def review(self, *, market_phase, candidates, open_positions):
        items = {
            symbol: MomentumAdvice(
                symbol=symbol,
                stance=stance,
                conviction=conviction,
                note=note,
            )
            for symbol, (stance, conviction, note) in self.stances.items()
        }
        return MomentumAdviceBundle(summary=f"{market_phase} consensus", items=items)


def _paper_position(
    *,
    run_id: str,
    symbol: str,
    total_pnl: float,
    day_regime: str,
    now: datetime,
) -> PaperPosition:
    return PaperPosition(
        position_id=f"pos-{symbol.lower()}-{day_regime}",
        run_id=run_id,
        symbol=symbol,
        status="CLOSED",
        entry_phase="premarket",
        entry_label="OPENING_RANGE_CANDIDATE",
        exit_reason="session_closed",
        quantity=100,
        average_entry_price=1.0,
        last_price=1.0,
        cost_basis=100.0,
        market_value=0.0,
        realized_pnl=total_pnl,
        unrealized_pnl=0.0,
        total_pnl=total_pnl,
        stop_price=0.95,
        planned_stop_price=0.95,
        planned_risk_pct=5.0,
        highest_price=1.1,
        add_count=0,
        partial_exit_count=0,
        strategy_bucket="predicted_starter",
        fill_reference_price=1.0,
        fill_slippage_pct=0.15,
        day_regime=day_regime,
        watchlist_rank_at_entry=1,
        entry_reasons=["predicted_starter"],
        exit_reasons=["session_closed"],
        opened_at=now,
        updated_at=now,
        closed_at=now,
    )


def test_paper_trading_enters_adds_exits_and_exports_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)

    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    first = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )
    assert first.entered_count == 1
    assert first.open_position_count == 1

    current_time = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)
    second = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.05,
                label="CONDITIONAL_ENTRY",
                score=4.8,
                pct_change=31.0,
            )
        ],
        export_csv=False,
    )
    assert second.added_count == 1

    current_time = datetime(2026, 3, 26, 13, 50, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.12,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                pct_change=40.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 21, 10, tzinfo=timezone.utc)
    final = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=True,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)

    assert final.exited_count == 1
    assert run.closed_trade_count == 1
    assert run.realized_pnl > 0
    assert run.win_rate == 100.0
    assert run.profit_factor >= 1.0
    assert run.reward_risk_ratio >= 1.0
    assert len(orders) == 3
    assert any(row.status == "CLOSED" for row in positions)

    orders_csv = export_dir / "paper_orders.csv"
    summary_csv = export_dir / "paper_run_summary.csv"
    curve_csv = export_dir / "paper_equity_curve.csv"
    assert orders_csv.exists()
    assert summary_csv.exists()
    assert curve_csv.exists()
    assert "AAA" in orders_csv.read_text(encoding="utf-8")


def test_paper_trading_closes_positions_after_session(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 21, 10, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert run.closed_trade_count == 1
    assert all(row.status == "CLOSED" for row in positions)
    assert any(row.exit_reason == "session_closed" for row in positions)


def test_paper_trading_all_scope_can_enter_news_driven_leader_with_gemini_buy(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        market_scope="all",
    )
    engine = PaperTradingEngine(
        settings,
        advisor=FakeAdvisor({"MEGA": ("buy", 0.82, "거래대금이 충분하고 아직 확장 여지 있음")}),
        now_fn=lambda: current_time,
    )

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "MEGA",
                phase="premarket",
                price=18.40,
                label="NEWS_CHECK_FIRST",
                score=3.4,
                pct_rank=1,
                volume_rank=2,
                predicted=False,
                pct_change=53.3,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.status == "OPEN" and row.symbol == "MEGA" for row in positions)
    assert any("gemini_buy" in row.reasons for row in orders)


def test_paper_trading_can_enter_top5_reclaim_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "LATE",
                phase="premarket",
                price=2.04,
                label="WAIT_PULLBACK",
                score=2.7,
                pct_rank=2,
                volume_rank=3,
                predicted=False,
                pct_change=18.0,
                reasons=["top5_live_leader", "leader_reclaim", "reclaim_entry_ready"],
                leader_persistence_score=61.0,
                pullback_absorption_score=69.0,
                trap_score=24.0,
                behavioral_score=72.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.symbol == "LATE" and row.status == "OPEN" for row in positions)
    assert any("live_exception_entry" in row.reasons for row in orders)


def test_paper_trading_predicted_priority_can_enter_outside_top5_and_tags_reasons(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "PRED",
                phase="premarket",
                price=1.75,
                label="WAIT_PULLBACK",
                score=2.8,
                pct_rank=8,
                volume_rank=9,
                predicted=True,
                watchlist_rank=4,
                reasons=["watchlist_predicted", "wait_for_reclaim"],
                trap_score=20.0,
                behavioral_score=52.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.symbol == "PRED" and row.status == "OPEN" for row in positions)
    assert any("predicted_priority_entry" in row.entry_reasons for row in positions if row.symbol == "PRED")
    assert any("predicted_priority_entry" in row.reasons for row in orders)


def test_paper_trading_live_exception_enters_when_slot_is_available(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "FAST",
                phase="premarket",
                price=1.42,
                label="OPENING_RANGE_CANDIDATE",
                score=4.0,
                pct_rank=1,
                volume_rank=2,
                predicted=False,
                reasons=["pct_leader", "live_dollar_volume_strong"],
                leader_persistence_score=65.0,
                pullback_absorption_score=64.0,
                trap_score=18.0,
                behavioral_score=69.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.entered_count == 1
    assert any(row.symbol == "FAST" and row.status == "OPEN" for row in positions)
    assert any("live_exception_entry" in row.reasons for row in orders)


def test_paper_trading_live_exception_waits_until_predicted_slots_are_exhausted(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity("AAA", phase="premarket", price=1.00, label="WAIT_PULLBACK", score=2.9, pct_rank=8, volume_rank=9, predicted=True, watchlist_rank=1, behavioral_score=50.0, trap_score=15.0),
            _activity("BBB", phase="premarket", price=1.10, label="WAIT_PULLBACK", score=2.85, pct_rank=7, volume_rank=8, predicted=True, watchlist_rank=2, behavioral_score=52.0, trap_score=16.0),
            _activity("CCC", phase="premarket", price=1.20, label="OPENING_RANGE_CANDIDATE", score=3.2, pct_rank=6, volume_rank=7, predicted=True, watchlist_rank=3, behavioral_score=54.0, trap_score=17.0),
            _activity("DDD", phase="premarket", price=1.30, label="OPENING_RANGE_CANDIDATE", score=3.1, pct_rank=5, volume_rank=6, predicted=True, watchlist_rank=4, behavioral_score=56.0, trap_score=18.0),
            _activity("EEE", phase="premarket", price=1.40, label="OPENING_RANGE_CANDIDATE", score=3.0, pct_rank=4, volume_rank=5, predicted=True, watchlist_rank=5, behavioral_score=58.0, trap_score=19.0),
            _activity("FAST", phase="premarket", price=1.30, label="OPENING_RANGE_CANDIDATE", score=4.4, pct_rank=1, volume_rank=1, predicted=False, reasons=["pct_leader", "live_dollar_volume_strong"], leader_persistence_score=68.0, pullback_absorption_score=66.0, trap_score=18.0, behavioral_score=72.0),
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 5
    assert {row.symbol for row in positions if row.status == "OPEN"} == {"AAA", "BBB", "CCC", "DDD", "EEE"}


def test_paper_trading_adaptive_holds_through_no_chase_until_session_end(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    mid = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.03,
                label="NO_CHASE",
                score=2.0,
                pct_change=24.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert mid.exited_count == 0
    assert any(row.symbol == "AAA" and row.status == "OPEN" for row in positions)

    current_time = datetime(2026, 3, 26, 21, 10, tzinfo=timezone.utc)
    final = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    assert final.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "session_closed" for row in positions)


def test_paper_trading_adaptive_uses_five_percent_hard_stop_for_premarket_entries(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 10, 20, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=0.94,
                label="WAIT_PULLBACK",
                score=2.0,
                pct_change=4.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "stop_loss" for row in positions)


def test_paper_trading_adaptive_uses_five_percent_hard_stop_for_regular_carry_positions(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 14, 20, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.94,
                label="NO_CHASE",
                score=2.0,
                pct_change=4.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "stop_loss" for row in positions)


def test_paper_trading_fill_model_uses_quote_reference_and_gap_through_stop(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                bid_price=0.99,
                ask_price=1.02,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                watchlist_rank=1,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.90,
                bid_price=0.89,
                ask_price=0.91,
                label="NO_CHASE",
                score=1.0,
                pct_rank=9,
                volume_rank=9,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    position = next(row for row in positions if row.symbol == "AAA")
    entry_order, exit_order = orders

    assert result.exited_count == 1
    assert entry_order.price == pytest.approx(1.02 * 1.0015)
    assert entry_order.fill_reference_price == pytest.approx(1.02)
    assert entry_order.fill_slippage_pct == pytest.approx(0.15)
    assert entry_order.strategy_bucket == "predicted_starter"
    assert entry_order.planned_risk_pct == pytest.approx(5.0)
    assert entry_order.watchlist_rank_at_entry == 1
    assert position.strategy_bucket == "predicted_starter"
    assert position.planned_stop_price == pytest.approx(entry_order.planned_stop_price)
    assert position.planned_risk_pct == pytest.approx(5.0)
    assert exit_order.price == pytest.approx(0.89 * 0.995)
    assert exit_order.fill_reference_price == pytest.approx(0.89)
    assert exit_order.fill_slippage_pct == pytest.approx(0.5)
    assert exit_order.strategy_bucket == "predicted_starter"
    assert exit_order.intent == "EXIT"
    assert position.exit_reason == "stop_loss"


def test_paper_trading_adaptive_does_not_open_new_regular_positions(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.10,
                label="CONDITIONAL_ENTRY",
                score=4.8,
                predicted=True,
                pct_rank=1,
                volume_rank=1,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 0
    assert positions == []


def test_paper_trading_adaptive_trims_winner_and_keeps_runner_open(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.13,
                label="CONDITIONAL_ENTRY",
                score=5.0,
                pct_change=30.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    assert result.exited_count == 0
    assert any(row.intent == "TRIM" and row.symbol == "AAA" for row in orders)
    assert any(row.symbol == "AAA" and row.status == "OPEN" and row.partial_exit_count == 1 for row in positions)
    assert any(row.symbol == "AAA" and row.stop_price is not None and row.stop_price >= row.average_entry_price for row in positions)


def test_paper_trading_adaptive_time_stop_closes_stalled_position(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 10, 40, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.005,
                label="WAIT_PULLBACK",
                score=2.0,
                pct_change=1.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "time_stop" for row in positions)


def test_baseline_pct_strategy_keeps_momentum_cooldown_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    engine = PaperTradingEngine(
        settings,
        strategy_name="baseline_pct_test",
        strategy_mode="baseline_pct",
        now_fn=lambda: current_time,
    )

    engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=False,
                pct_rank=1,
                volume_rank=5,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.06,
                label="NO_CHASE",
                score=2.0,
                predicted=False,
                pct_rank=4,
                volume_rank=6,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path, "baseline_pct_test")
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "momentum_cooldown" for row in positions)


def test_paper_trading_coordinator_exports_strategy_comparison(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)

    result = coordinator.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                pct_rank=2,
                volume_rank=3,
                leader_persistence_score=67.0,
                pullback_absorption_score=63.0,
                trap_score=18.0,
                behavioral_score=70.0,
            )
        ],
        export_csv=True,
    )

    comparison_csv = export_dir / "paper_strategy_comparison.csv"
    cohort_csv = export_dir / "paper_cohort_summary.csv"
    strategy_runs = fetch_latest_paper_strategy_runs(db_path, limit=10)
    assert result.comparison_path == comparison_csv.resolve()
    assert comparison_csv.exists()
    assert cohort_csv.exists()
    assert len(strategy_runs) >= 3
    csv_text = comparison_csv.read_text(encoding="utf-8")
    cohort_text = cohort_csv.read_text(encoding="utf-8")
    assert "auto_paper_v1" in csv_text
    assert "baseline_pct_leader_v1" in csv_text
    assert "strategy_bucket" in cohort_text
    assert "predicted_starter" in cohort_text


def test_paper_trading_cohort_summary_keeps_day_regime_separate(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    now = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", 10_000.0)
    upsert_paper_positions(
        db_path,
        [
            _paper_position(run_id=run.run_id, symbol="AAA", total_pnl=50.0, day_regime="hot", now=now),
            _paper_position(run_id=run.run_id, symbol="BBB", total_pnl=-20.0, day_regime="cold", now=now),
        ],
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: now)
    cohort_csv = coordinator.export_cohort_summary(run.run_id)
    text = cohort_csv.read_text(encoding="utf-8")

    assert "day_regime" in text
    assert "hot" in text
    assert "cold" in text
