from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    create_paper_trading_run,
    fetch_latest_paper_trading_run,
    insert_premkt_predictions,
    fetch_latest_paper_strategy_runs,
    fetch_paper_orders,
    fetch_paper_positions,
    init_database,
    upsert_paper_trading_run,
    upsert_paper_positions,
)
from penny_stock_radar.models import MarketActivity, PaperPosition, PremktPrediction
from penny_stock_radar.services.momentum_advisor import MomentumAdvice, MomentumAdviceBundle
from penny_stock_radar.services.market_activity import MarketActivityScanResult
from penny_stock_radar.services.paper_coordinator import PaperTradingCoordinator
from penny_stock_radar.services.paper_trading import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    PRIMARY_PAPER_STRATEGY,
    PaperTradingEngine,
)


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
    market_status: str = "open",
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
        market_status=market_status,
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


def _open_position(
    *,
    run_id: str,
    symbol: str,
    now: datetime,
    quantity: int,
    average_entry_price: float,
    last_price: float,
    stop_price: float,
    add_count: int = 0,
) -> PaperPosition:
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
        stop_price=stop_price,
        planned_stop_price=stop_price,
        planned_risk_pct=5.0,
        highest_price=max(last_price, average_entry_price),
        add_count=add_count,
        partial_exit_count=0,
        strategy_bucket="predicted_starter",
        fill_reference_price=average_entry_price,
        fill_slippage_pct=0.15,
        day_regime="normal",
        watchlist_rank_at_entry=1,
        entry_reasons=["predicted_starter"],
        exit_reasons=[],
        opened_at=now,
        updated_at=now,
    )


def _seed_predictions(
    db_path: Path,
    predictions: list[PremktPrediction],
) -> str:
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=len(predictions))
    insert_premkt_predictions(db_path, snapshot.snapshot_id, predictions)
    return snapshot.snapshot_id


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def test_paper_trading_enters_adds_exits_and_exports_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)

    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
        premarket_min_dollar_volume=50.0,
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
    assert len(orders) == 4
    assert any(row.intent == "TRIM" for row in orders)
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
        trade_plan_max_concurrent_open_risk_pct=2.0,
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


def test_paper_trading_run_once_scans_open_positions_even_if_not_in_leaderboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=current_time,
                quantity=500,
                average_entry_price=1.00,
                last_price=1.00,
                stop_price=0.95,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    def fake_scan(*, phase, top_limit, extra_symbols, **kwargs):
        assert phase == "regular"
        assert top_limit == 10
        assert extra_symbols == ("AAA",)
        return MarketActivityScanResult(
            scan_id="scan-1",
            market_phase="regular",
            scanned_symbol_count=1,
            activity=[
                _activity(
                    "AAA",
                    phase="regular",
                    price=0.94,
                    bid_price=0.93,
                    ask_price=0.95,
                    label="NO_CHASE",
                    score=1.0,
                    pct_change=4.0,
                )
            ],
            outcomes=[],
        )

    monkeypatch.setattr(engine.scanner, "scan", fake_scan)

    result = engine.run_once(phase="regular", export_csv=False)

    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.exited_count == 1
    assert any(row.symbol == "AAA" and row.exit_reason == "stop_loss" for row in positions)


def test_paper_trading_blocks_stale_quote_less_halted_entry(tmp_path: Path) -> None:
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
                "AAA",
                phase="premarket",
                price=1.00,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                data_age_seconds=90.0,
                has_live_quote=False,
                market_status="volatilityHalted",
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 0
    assert all(row.status != "OPEN" for row in positions)


def test_paper_trading_blocks_new_entry_after_daily_loss_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_initial_capital=10_000.0,
        trade_plan_daily_loss_lock_pct=2.0,
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _paper_position(
                run_id=run.run_id,
                symbol="LOSS",
                total_pnl=-250.0,
                day_regime="cold",
                now=current_time,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
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

    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 0
    assert not any(row.symbol == "AAA" and row.status == "OPEN" for row in positions)


def test_paper_trading_daily_loss_lock_stays_latched_for_rest_of_day(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 11, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_initial_capital=10_000.0,
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    run.notes = [*run.notes, "state:daily_loss_lock:2026-03-26"]
    upsert_paper_trading_run(db_path, run)
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
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

    locked_run = fetch_latest_paper_trading_run(db_path)
    assert locked_run is not None
    assert result.entered_count == 0
    assert "state:daily_loss_lock:2026-03-26" in locked_run.notes


def test_paper_trading_latches_daily_loss_lock_when_exit_pushes_loss_through_threshold(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 11, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_initial_capital=10_000.0,
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=current_time,
                quantity=1_000,
                average_entry_price=1.00,
                last_price=0.82,
                stop_price=0.95,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.80,
                bid_price=0.78,
                ask_price=0.81,
                label="NO_CHASE",
                score=1.0,
                pct_change=-20.0,
            )
        ],
        export_csv=False,
    )

    locked_run = fetch_latest_paper_trading_run(db_path)
    assert locked_run is not None
    assert result.exited_count == 1
    assert "state:daily_loss_lock:2026-03-26" in locked_run.notes


def test_paper_trading_does_not_sell_halted_open_position(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 11, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=current_time,
                quantity=500,
                average_entry_price=1.00,
                last_price=0.94,
                stop_price=0.95,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.90,
                bid_price=0.88,
                ask_price=0.91,
                label="NO_CHASE",
                score=1.0,
                pct_change=-18.0,
                market_status="LULD Pause",
            )
        ],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    aaa = next(row for row in positions if row.symbol == "AAA")
    assert result.exited_count == 0
    assert aaa.status == "OPEN"


def test_paper_trading_does_not_force_close_halted_position_after_session(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 21, 10, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    run.last_phase = "regular"
    upsert_paper_trading_run(db_path, run)
    halted_position = _open_position(
        run_id=run.run_id,
        symbol="AAA",
        now=current_time,
        quantity=500,
        average_entry_price=1.00,
        last_price=0.94,
        stop_price=0.95,
    )
    halted_position.entry_reasons.append("state:market_status:luld_pause")
    upsert_paper_positions(db_path, [halted_position])
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    aaa = next(row for row in positions if row.symbol == "AAA")
    assert result.exited_count == 0
    assert aaa.status == "OPEN"


def test_paper_trading_blocks_new_entry_after_concurrent_open_risk_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_initial_capital=10_000.0,
        trade_plan_max_concurrent_open_risk_pct=1.0,
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=current_time,
                quantity=1_000,
                average_entry_price=1.00,
                last_price=1.04,
                stop_price=0.93,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.04,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=True,
                watchlist_rank=1,
            ),
            _activity(
                "BBB",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.3,
                predicted=True,
                watchlist_rank=2,
            ),
        ],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    aaa = next(row for row in positions if row.symbol == "AAA")
    assert result.entered_count == 0
    assert aaa.quantity == 1_000
    assert not any(row.symbol == "BBB" and row.status == "OPEN" for row in positions)


def test_paper_trading_blocks_add_after_concurrent_open_risk_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_initial_capital=10_000.0,
        trade_plan_max_concurrent_open_risk_pct=1.0,
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", settings.paper_initial_capital)
    upsert_paper_positions(
        db_path,
        [
            _open_position(
                run_id=run.run_id,
                symbol="AAA",
                now=current_time,
                quantity=1_000,
                average_entry_price=1.00,
                last_price=1.04,
                stop_price=0.93,
            )
        ],
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.04,
                label="CONDITIONAL_ENTRY",
                score=4.8,
                pct_change=31.0,
            )
        ],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    aaa = next(row for row in positions if row.symbol == "AAA")
    assert result.added_count == 0
    assert aaa.quantity == 1_000


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
        trade_plan_max_concurrent_open_risk_pct=2.0,
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
        trade_plan_max_concurrent_open_risk_pct=2.0,
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
        trade_plan_max_concurrent_open_risk_pct=2.0,
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


@pytest.mark.parametrize(
    ("symbol", "predicted", "predictor_score", "analysis_score", "expected_tag"),
    [
        ("PSTRONG", True, 90.0, 2.10, "predicted_starter"),
        ("PWEAK", True, 35.0, 2.10, None),
        ("LSTRONG", False, 90.0, 3.00, "live_exception"),
        ("LWEAK", False, 90.0, 2.10, None),
        ("PMIDHI", True, 60.0, 2.30, "predicted_starter"),
        ("PMIDLO", True, 50.0, 2.30, None),
    ],
)
def test_paper_trading_predictor_weight_corner_and_midpoint_cases(
    tmp_path: Path,
    symbol: str,
    predicted: bool,
    predictor_score: float,
    analysis_score: float,
    expected_tag: str | None,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_predictor_weight_k1=1.0,
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)
    engine._predictor_score_by_symbol = {symbol: predictor_score}

    row = _activity(
        symbol,
        phase="premarket",
        price=1.25,
        label="WAIT_PULLBACK" if predicted else "OPENING_RANGE_CANDIDATE",
        score=analysis_score,
        pct_rank=1,
        volume_rank=1,
        predicted=predicted,
        reasons=["watchlist_predicted", "leader_reclaim", "reclaim_entry_ready"]
        if predicted
        else ["pct_leader", "live_dollar_volume_strong"],
        behavioral_score=70.0,
        trap_score=10.0,
        watchlist_rank=1 if predicted else None,
    )

    assert (
        engine._adaptive_entry_tag(
            row=row,
            advice=None,
            market_phase="premarket",
            open_symbols=set(),
            traded_today=set(),
        )
        == expected_tag
    )


def test_paper_trading_predictor_weight_allows_lower_scoring_candidate_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _seed_predictions(
        db_path,
        [
            PremktPrediction(
                symbol="PRED",
                score=90.0,
                max_hold_days=3,
                entry_rationale="strong catalyst",
                themes=["biotech"],
                filing_summary="8-K",
            )
        ],
    )
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        trade_plan_max_concurrent_open_risk_pct=2.0,
        paper_predictor_weight_k1=1.0,
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    result = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "PRED",
                phase="premarket",
                price=1.50,
                label="WAIT_PULLBACK",
                score=2.10,
                pct_rank=8,
                volume_rank=8,
                predicted=True,
                watchlist_rank=3,
                behavioral_score=55.0,
                trap_score=15.0,
            ),
            _activity(
                "LIVE",
                phase="premarket",
                price=1.60,
                label="OPENING_RANGE_CANDIDATE",
                score=2.10,
                pct_rank=1,
                volume_rank=1,
                predicted=False,
                reasons=["pct_leader", "live_dollar_volume_strong"],
                behavioral_score=70.0,
                trap_score=12.0,
            ),
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert result.entered_count == 1
    assert {row.symbol for row in positions if row.status == "OPEN"} == {"PRED"}


def test_paper_trading_noncandidate_remains_strict_with_predictor_weight_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_predictor_weight_k1=1.0,
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time)

    first = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "LIVELOW",
                phase="premarket",
                price=1.30,
                label="OPENING_RANGE_CANDIDATE",
                score=2.10,
                pct_rank=1,
                volume_rank=1,
                predicted=False,
                reasons=["pct_leader", "live_dollar_volume_strong"],
                behavioral_score=70.0,
                trap_score=12.0,
            )
        ],
        export_csv=False,
    )

    second = engine.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "LIVEHI",
                phase="premarket",
                price=1.40,
                label="OPENING_RANGE_CANDIDATE",
                score=3.00,
                pct_rank=1,
                volume_rank=1,
                predicted=False,
                reasons=["pct_leader", "live_dollar_volume_strong"],
                behavioral_score=70.0,
                trap_score=12.0,
            )
        ],
        export_csv=False,
    )

    run = fetch_latest_paper_trading_run(db_path)
    assert run is not None
    positions = fetch_paper_positions(db_path, run.run_id)
    assert first.entered_count == 0
    assert second.entered_count == 1
    assert {row.symbol for row in positions if row.status == "OPEN"} == {"LIVEHI"}


def test_paper_trading_predictor_feature_flag_off_matches_legacy_behavior(tmp_path: Path) -> None:
    db_without = tmp_path / "without.sqlite3"
    db_with = tmp_path / "with.sqlite3"
    init_database(db_without)
    init_database(db_with)
    _seed_predictions(
        db_with,
        [
            PremktPrediction(
                symbol="PRED",
                score=95.0,
                max_hold_days=3,
                entry_rationale="strong catalyst",
                themes=["ai"],
                filing_summary="8-K",
            )
        ],
    )
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings_without = AppSettings(
        db_path=db_without,
        paper_trade_dir=tmp_path / "paper_without",
        live_market_provider="disabled",
        paper_predictor_weight_k1=0.0,
        paper_predictor_weight_k2=0.0,
    )
    settings_with = AppSettings(
        db_path=db_with,
        paper_trade_dir=tmp_path / "paper_with",
        live_market_provider="disabled",
        paper_predictor_weight_k1=0.0,
        paper_predictor_weight_k2=0.0,
    )
    activity = [
        _activity(
            "PRED",
            phase="premarket",
            price=1.20,
            label="OPENING_RANGE_CANDIDATE",
            score=4.00,
            pct_rank=1,
            volume_rank=1,
            predicted=True,
            watchlist_rank=1,
        )
    ]

    engine_without = PaperTradingEngine(settings_without, now_fn=lambda: current_time)
    engine_with = PaperTradingEngine(settings_with, now_fn=lambda: current_time)
    result_without = engine_without.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )
    result_with = engine_with.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )

    run_without = fetch_latest_paper_trading_run(db_without)
    run_with = fetch_latest_paper_trading_run(db_with)
    assert run_without is not None and run_with is not None
    orders_without = fetch_paper_orders(db_without, run_without.run_id)
    orders_with = fetch_paper_orders(db_with, run_with.run_id)
    positions_without = fetch_paper_positions(db_without, run_without.run_id)
    positions_with = fetch_paper_positions(db_with, run_with.run_id)

    assert result_without.entered_count == result_with.entered_count == 1
    assert [row.quantity for row in orders_without] == [row.quantity for row in orders_with]
    assert [row.strategy_bucket for row in orders_without] == [row.strategy_bucket for row in orders_with]
    assert [row.quantity for row in positions_without] == [row.quantity for row in positions_with]
    assert [row.strategy_bucket for row in positions_without] == [row.strategy_bucket for row in positions_with]


def test_paper_trading_predictor_weight_boosts_position_size_with_cap(tmp_path: Path) -> None:
    db_base = tmp_path / "base.sqlite3"
    db_boosted = tmp_path / "boosted.sqlite3"
    init_database(db_base)
    init_database(db_boosted)
    prediction = PremktPrediction(
        symbol="PRED",
        score=90.0,
        max_hold_days=3,
        entry_rationale="strong catalyst",
        themes=["energy"],
        filing_summary="8-K",
    )
    _seed_predictions(db_base, [prediction])
    _seed_predictions(db_boosted, [prediction])
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    activity = [
        _activity(
            "PRED",
            phase="premarket",
            price=1.00,
            label="OPENING_RANGE_CANDIDATE",
            score=4.00,
            pct_rank=1,
            volume_rank=1,
            predicted=True,
            watchlist_rank=1,
        )
    ]
    base_settings = AppSettings(
        db_path=db_base,
        paper_trade_dir=tmp_path / "paper_base",
        live_market_provider="disabled",
        paper_predictor_weight_k2=0.0,
    )
    boosted_settings = AppSettings(
        db_path=db_boosted,
        paper_trade_dir=tmp_path / "paper_boosted",
        live_market_provider="disabled",
        paper_predictor_weight_k2=1.0,
    )

    base_engine = PaperTradingEngine(base_settings, now_fn=lambda: current_time)
    boosted_engine = PaperTradingEngine(boosted_settings, now_fn=lambda: current_time)
    base_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )
    boosted_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )

    run_base = fetch_latest_paper_trading_run(db_base)
    run_boosted = fetch_latest_paper_trading_run(db_boosted)
    assert run_base is not None and run_boosted is not None
    order_base = fetch_paper_orders(db_base, run_base.run_id)[0]
    order_boosted = fetch_paper_orders(db_boosted, run_boosted.run_id)[0]

    assert order_boosted.quantity > order_base.quantity
    boosted_fraction = boosted_engine._entry_fraction(row=activity[0], entry_tag="predicted_starter")
    assert boosted_fraction == pytest.approx(0.12)
    assert boosted_fraction <= boosted_settings.paper_entry_size_fraction


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
    assert entry_order.price == pytest.approx(1.02)
    assert entry_order.fill_reference_price == pytest.approx(1.02)
    assert entry_order.fill_slippage_pct == pytest.approx(((1.02 - 0.99) / 1.02) * 1.5 * 100.0)
    assert entry_order.strategy_bucket == "predicted_starter"
    assert entry_order.planned_risk_pct == pytest.approx(5.0)
    assert entry_order.watchlist_rank_at_entry == 1
    assert position.strategy_bucket == "predicted_starter"
    assert position.planned_stop_price is not None
    assert entry_order.planned_stop_price is not None
    assert position.planned_stop_price >= entry_order.planned_stop_price
    assert position.planned_risk_pct == pytest.approx(5.0)
    assert exit_order.price == pytest.approx(0.89 * (1 - 0.005))
    assert exit_order.fill_reference_price == pytest.approx(0.89)
    assert exit_order.fill_slippage_pct == pytest.approx(0.5)
    assert exit_order.strategy_bucket == "predicted_starter"
    assert exit_order.intent == "EXIT"
    assert position.exit_reason == "stop_loss"


def test_paper_trading_realistic_execution_lowers_return_and_reports_session_slippage(
    tmp_path: Path,
) -> None:
    realistic_db = tmp_path / "realistic.sqlite3"
    optimistic_db = tmp_path / "optimistic.sqlite3"
    realistic_export = tmp_path / "realistic_exports"
    optimistic_export = tmp_path / "optimistic_exports"
    init_database(realistic_db)
    init_database(optimistic_db)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    realistic_settings = AppSettings(
        db_path=realistic_db,
        paper_trade_dir=realistic_export,
        live_market_provider="disabled",
    )
    optimistic_settings = AppSettings(
        db_path=optimistic_db,
        paper_trade_dir=optimistic_export,
        live_market_provider="disabled",
        paper_min_trade_fee=0.01,
        paper_notional_fee_rate_pct=0.0001,
    )
    realistic = PaperTradingCoordinator(realistic_settings, now_fn=lambda: current_time)
    optimistic = PaperTradingCoordinator(optimistic_settings, now_fn=lambda: current_time)

    open_row = _activity(
        "AAA",
        phase="premarket",
        price=1.00,
        bid_price=0.98,
        ask_price=1.03,
        label="OPENING_RANGE_CANDIDATE",
        score=4.4,
        watchlist_rank=1,
    )
    for coordinator in (realistic, optimistic):
        coordinator.process_market_activity(
            market_phase="premarket",
            activity=[open_row],
            export_csv=True,
        )

    current_time = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)
    close_row = _activity(
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
    for coordinator in (realistic, optimistic):
        coordinator.process_market_activity(
            market_phase="regular",
            activity=[close_row],
            export_csv=True,
        )

    realistic_run = fetch_latest_paper_trading_run(
        realistic_db,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    optimistic_run = fetch_latest_paper_trading_run(
        optimistic_db,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    assert realistic_run is not None and optimistic_run is not None
    assert realistic_run.realized_pnl < optimistic_run.realized_pnl

    execution_rows = _csv_rows(realistic_export / "paper_execution_quality.csv")
    phase_rows = {
        row["market_phase"]: row
        for row in execution_rows
        if row["strategy_name"] == PRIMARY_PAPER_STRATEGY
        and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
    }
    assert float(phase_rows["premarket"]["avg_slippage_pct"]) > float(
        phase_rows["regular"]["avg_slippage_pct"]
    )


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
        premarket_min_dollar_volume=50.0,
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
    bucket_csv = export_dir / "paper_bucket_comparison.csv"
    diff_csv = export_dir / "paper_bucket_trade_diff.csv"
    cohort_csv = export_dir / "paper_cohort_summary.csv"
    execution_csv = export_dir / "paper_execution_quality.csv"
    trade_log_csv = export_dir / "paper_trade_log.csv"
    backtest_csv = export_dir / "paper_backtest_kpis.csv"
    regime_csv = export_dir / "paper_regime_split.csv"
    predictor_csv = export_dir / "paper_predictor_kpis.csv"
    manifest_json = export_dir / "run_manifest.json"
    gate_json = export_dir / "paper_performance_gate.json"
    strategy_runs = fetch_latest_paper_strategy_runs(db_path, limit=10)
    assert result.comparison_path == comparison_csv.resolve()
    assert comparison_csv.exists()
    assert bucket_csv.exists()
    assert diff_csv.exists()
    assert cohort_csv.exists()
    assert execution_csv.exists()
    assert trade_log_csv.exists()
    assert backtest_csv.exists()
    assert regime_csv.exists()
    assert predictor_csv.exists()
    assert manifest_json.exists()
    assert gate_json.exists()
    assert len(strategy_runs) >= 4
    csv_text = comparison_csv.read_text(encoding="utf-8")
    bucket_text = bucket_csv.read_text(encoding="utf-8")
    diff_text = diff_csv.read_text(encoding="utf-8")
    cohort_text = cohort_csv.read_text(encoding="utf-8")
    execution_text = execution_csv.read_text(encoding="utf-8")
    backtest_text = backtest_csv.read_text(encoding="utf-8")
    regime_text = regime_csv.read_text(encoding="utf-8")
    predictor_text = predictor_csv.read_text(encoding="utf-8")
    assert "auto_paper_v1" in csv_text
    assert "baseline_pct_leader_v1" in csv_text
    assert PREDICTOR_WEIGHTED_BUCKET in bucket_text
    assert MOMENTUM_ONLY_BUCKET in bucket_text
    assert "pnl_diff" in diff_text
    assert "portfolio_bucket" in cohort_text
    assert "strategy_bucket" in cohort_text
    assert "predicted_starter" in cohort_text
    assert "avg_buy_slippage_pct" in execution_text
    assert "expectancy_r" in backtest_text
    assert "regime_bucket" in regime_text
    assert "predictor_hit_rate_pct" in predictor_text
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    gate = json.loads(gate_json.read_text(encoding="utf-8"))
    assert manifest["artifacts"]["paper_trade_log"] == "paper_trade_log.csv"
    assert gate["status"] == "pass"


def test_paper_trading_partial_fill_is_logged_and_exported(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
        premarket_min_dollar_volume=50.0,
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)
    thin_row = _activity(
        "THIN",
        phase="premarket",
        price=1.00,
        label="OPENING_RANGE_CANDIDATE",
        score=4.4,
    ).model_copy(
        update={
            "volume": 100.0,
            "dollar_volume": 100.0,
        }
    )

    coordinator.process_market_activity(
        market_phase="premarket",
        activity=[thin_row],
        export_csv=True,
    )

    run = fetch_latest_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    assert run is not None
    orders = fetch_paper_orders(db_path, run.run_id)
    assert any(row.fill_status == "PARTIAL" for row in orders)
    assert any("partial_fill_cap" in row.reasons for row in orders)

    execution_rows = _csv_rows(export_dir / "paper_execution_quality.csv")
    predictor_rows = [
        row
        for row in execution_rows
        if row["strategy_name"] == PRIMARY_PAPER_STRATEGY
        and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
        and row["market_phase"] == "premarket"
    ]
    assert predictor_rows
    assert int(float(predictor_rows[0]["partial_fill_count"])) >= 1


def test_paper_trading_coordinator_keeps_bucket_portfolios_independent(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    _seed_predictions(
        db_path,
        [
            PremktPrediction(
                symbol="BOTH",
                score=90.0,
                max_hold_days=3,
                entry_rationale="strong catalyst",
                themes=["biotech"],
                filing_summary="8-K",
            ),
            PremktPrediction(
                symbol="EDGE",
                score=90.0,
                max_hold_days=3,
                entry_rationale="edge case",
                themes=["biotech"],
                filing_summary="8-K",
            ),
        ],
    )
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
        trade_plan_max_concurrent_open_risk_pct=2.0,
        paper_predictor_weight_k1=1.0,
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)

    coordinator.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "BOTH",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.20,
                pct_rank=1,
                volume_rank=1,
                predicted=True,
                watchlist_rank=1,
                behavioral_score=70.0,
                trap_score=12.0,
            ),
            _activity(
                "EDGE",
                phase="premarket",
                price=1.10,
                label="WAIT_PULLBACK",
                score=2.10,
                pct_rank=8,
                volume_rank=8,
                predicted=True,
                watchlist_rank=3,
                behavioral_score=55.0,
                trap_score=15.0,
            ),
        ],
        export_csv=False,
    )

    predictor_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    momentum_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None

    predictor_positions = fetch_paper_positions(db_path, predictor_run.run_id)
    momentum_positions = fetch_paper_positions(db_path, momentum_run.run_id)
    assert {row.symbol for row in predictor_positions if row.status == "OPEN"} == {"BOTH", "EDGE"}
    assert {row.symbol for row in momentum_positions if row.status == "OPEN"} == {"BOTH"}
    assert predictor_run.cash_balance != momentum_run.cash_balance

    current_time = datetime(2026, 3, 26, 14, 5, tzinfo=timezone.utc)
    coordinator.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "BOTH",
                phase="regular",
                price=1.24,
                label="CONDITIONAL_ENTRY",
                score=4.10,
                pct_rank=1,
                volume_rank=1,
                predicted=True,
                watchlist_rank=1,
                behavioral_score=70.0,
                trap_score=12.0,
                pct_change=28.0,
            ),
            _activity(
                "EDGE",
                phase="regular",
                price=1.00,
                bid_price=0.98,
                ask_price=1.01,
                label="NO_CHASE",
                score=1.20,
                pct_rank=9,
                volume_rank=9,
                predicted=True,
                watchlist_rank=3,
                behavioral_score=40.0,
                trap_score=20.0,
                pct_change=-8.0,
            ),
        ],
        export_csv=False,
    )

    predictor_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    momentum_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None
    predictor_positions = fetch_paper_positions(db_path, predictor_run.run_id)
    momentum_positions = fetch_paper_positions(db_path, momentum_run.run_id)

    edge_predictor = next(row for row in predictor_positions if row.symbol == "EDGE")
    both_predictor = next(row for row in predictor_positions if row.symbol == "BOTH")
    both_momentum = next(row for row in momentum_positions if row.symbol == "BOTH")
    assert edge_predictor.status == "CLOSED"
    assert edge_predictor.exit_reason == "stop_loss"
    assert both_predictor.status == "OPEN"
    assert both_momentum.status == "OPEN"
    assert predictor_run.realized_pnl != momentum_run.realized_pnl

    diff_csv = export_dir / "paper_bucket_trade_diff.csv"
    diff_text = diff_csv.read_text(encoding="utf-8")
    assert "EDGE" in diff_text
    assert "BOTH" in diff_text
    assert "predictor_total_pnl" in diff_text
    assert "momentum_total_pnl" in diff_text
    diff_rows = _csv_rows(diff_csv)
    assert any(abs(float(row["pnl_diff"])) > 0 for row in diff_rows if row["pnl_diff"])
    trade_rows = _csv_rows(export_dir / "paper_trade_log.csv")
    edge_rows = [
        row
        for row in trade_rows
        if row["symbol"] == "EDGE" and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
    ]
    assert edge_rows
    assert edge_rows[0]["predicted"] == "True"
    assert edge_rows[0]["predictor_score"] == "90.0"
    assert edge_rows[0]["predictor_weight"] == "1.0"
    predictor_kpi_rows = _csv_rows(export_dir / "paper_predictor_kpis.csv")
    primary_kpi = next(
        row
        for row in predictor_kpi_rows
        if row["strategy_name"] == PRIMARY_PAPER_STRATEGY
        and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
    )
    assert int(primary_kpi["candidate_count"]) == 2
    assert int(primary_kpi["triggered_candidate_count"]) == 2


def test_momentum_only_bucket_ignores_predictor_settings_even_when_enabled(tmp_path: Path) -> None:
    db_base = tmp_path / "momentum_base.sqlite3"
    db_weighted = tmp_path / "momentum_weighted.sqlite3"
    init_database(db_base)
    init_database(db_weighted)
    prediction = PremktPrediction(
        symbol="PRED",
        score=90.0,
        max_hold_days=3,
        entry_rationale="strong catalyst",
        themes=["energy"],
        filing_summary="8-K",
    )
    _seed_predictions(db_base, [prediction])
    _seed_predictions(db_weighted, [prediction])
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    activity = [
        _activity(
            "PRED",
            phase="premarket",
            price=1.00,
            label="OPENING_RANGE_CANDIDATE",
            score=4.00,
            pct_rank=1,
            volume_rank=1,
            predicted=True,
            watchlist_rank=1,
        )
    ]

    base_settings = AppSettings(
        db_path=db_base,
        paper_trade_dir=tmp_path / "paper_momentum_base",
        live_market_provider="disabled",
        paper_predictor_weight_k1=0.0,
        paper_predictor_weight_k2=0.0,
    )
    weighted_settings = AppSettings(
        db_path=db_weighted,
        paper_trade_dir=tmp_path / "paper_momentum_weighted",
        live_market_provider="disabled",
        paper_predictor_weight_k1=1.0,
        paper_predictor_weight_k2=1.0,
    )

    base_engine = PaperTradingEngine(
        base_settings,
        now_fn=lambda: current_time,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    weighted_engine = PaperTradingEngine(
        weighted_settings,
        now_fn=lambda: current_time,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    result_base = base_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )
    result_weighted = weighted_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )

    run_base = fetch_latest_paper_trading_run(
        db_base,
        PRIMARY_PAPER_STRATEGY,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    run_weighted = fetch_latest_paper_trading_run(
        db_weighted,
        PRIMARY_PAPER_STRATEGY,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    assert run_base is not None and run_weighted is not None
    orders_base = fetch_paper_orders(db_base, run_base.run_id)
    orders_weighted = fetch_paper_orders(db_weighted, run_weighted.run_id)

    assert result_base.entered_count == result_weighted.entered_count == 1
    assert [row.quantity for row in orders_base] == [row.quantity for row in orders_weighted]
    assert run_base.cash_balance == pytest.approx(run_weighted.cash_balance)
    assert run_base.realized_pnl == pytest.approx(run_weighted.realized_pnl)
    assert run_base.unrealized_pnl == pytest.approx(run_weighted.unrealized_pnl)


def test_predictor_reasons_are_recorded_only_for_predictor_weighted_bucket(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    prediction = PremktPrediction(
        symbol="PRED",
        score=90.0,
        max_hold_days=3,
        entry_rationale="strong catalyst",
        themes=["energy"],
        filing_summary="8-K",
    )
    _seed_predictions(db_path, [prediction])
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    activity = [
        _activity(
            "PRED",
            phase="premarket",
            price=1.00,
            label="OPENING_RANGE_CANDIDATE",
            score=4.00,
            pct_rank=1,
            volume_rank=1,
            predicted=True,
            watchlist_rank=1,
        )
    ]
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_predictor_weight_k1=1.0,
        paper_predictor_weight_k2=1.0,
    )

    predictor_engine = PaperTradingEngine(
        settings,
        now_fn=lambda: current_time,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    momentum_engine = PaperTradingEngine(
        settings,
        now_fn=lambda: current_time,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    predictor_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )
    momentum_engine.process_market_activity(
        market_phase="premarket",
        activity=activity,
        export_csv=False,
    )

    predictor_run = fetch_latest_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    momentum_run = fetch_latest_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=MOMENTUM_ONLY_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None
    predictor_order = fetch_paper_orders(db_path, predictor_run.run_id)[0]
    momentum_order = fetch_paper_orders(db_path, momentum_run.run_id)[0]

    assert any(reason.startswith("predictor_score:") for reason in predictor_order.reasons)
    assert any(reason.startswith("predictor_weight:") for reason in predictor_order.reasons)
    assert not any(reason.startswith("predictor_score:") for reason in momentum_order.reasons)
    assert not any(reason.startswith("predictor_weight:") for reason in momentum_order.reasons)


def test_paper_performance_gate_fails_on_candidate_denominator_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    export_dir.mkdir(parents=True)
    (export_dir / "paper_trade_log.csv").write_text(
        "run_id,strategy_name,bucket,symbol,status,predicted,predictor_score,predictor_weight\n"
        "run-1,auto_paper_v1,predictor_weighted,AAA,CLOSED,True,90.0,1.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_predictor_kpis.csv").write_text(
        "strategy_name,bucket,candidate_count,triggered_candidate_count\n"
        "auto_paper_v1,predictor_weighted,0,1\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_trade_diff.csv").write_text(
        "symbol,pnl_diff\nAAA,1.0\n",
        encoding="utf-8",
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc))
    payload = coordinator.reporting.evaluate_performance_review_gate()

    assert payload["status"] == "fail"
    assert "candidate_count is 0" in payload["failures"][0]


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

    assert "portfolio_bucket" in text
    assert "day_regime" in text
    assert "hot" in text
    assert "cold" in text


def test_paper_trading_regime_split_maps_to_trend_and_chop(tmp_path: Path) -> None:
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
    coordinator.export_trade_log()
    regime_csv = coordinator.export_regime_split()
    text = regime_csv.read_text(encoding="utf-8")

    assert "trend" in text
    assert "chop" in text
