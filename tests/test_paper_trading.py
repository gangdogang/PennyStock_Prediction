from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
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
from penny_stock_radar.services.activity_sanitizer import sanitize_activity_for_bucket
from penny_stock_radar.services.market_activity import MarketActivityScanner, MarketActivityScanResult
from penny_stock_radar.services.paper_coordinator import PaperTradingCoordinator
from penny_stock_radar.services.paper_trading import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    PRIMARY_PAPER_STRATEGY,
    PaperTradingEngine,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
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
    resume_elapsed_seconds: float | None = None,
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
        resume_elapsed_seconds=resume_elapsed_seconds,
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


def test_watchlist_blind_activity_sanitizer_removes_predictor_watchlist_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    scanner = MarketActivityScanner(settings)
    row = _activity(
        "META",
        phase="premarket",
        price=1.00,
        label="OPENING_RANGE_CANDIDATE",
        score=4.50,
        pct_change=20.0,
        predicted=True,
        watchlist_rank=1,
        reasons=[
            "watchlist_predicted",
            "predicted_watchlist",
            "watchlist_score_strong",
            "predictor_score:90.0",
            "live_dollar_volume_strong",
        ],
    )

    sanitized = sanitize_activity_for_bucket(
        scanner,
        WATCHLIST_BLIND_MOMENTUM_BUCKET,
        [row],
    )[0]

    assert row.predicted is True
    assert sanitized.predicted is False
    assert sanitized.watchlist_rank is None
    assert sanitized.watchlist_score is None
    assert sanitized.analysis_label == "NEWS_CHECK_FIRST"
    assert sanitized.analysis_score == pytest.approx(3.5)
    assert "live_dollar_volume_strong" in sanitized.reasons
    assert not any("watchlist" in reason.lower() for reason in sanitized.reasons)
    assert not any("predict" in reason.lower() for reason in sanitized.reasons)


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
    market_date: str = "2026-03-26",
) -> str:
    dated = [p.model_copy(update={"market_date": market_date}) for p in predictions]
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=len(dated), market_date=market_date)
    insert_premkt_predictions(db_path, snapshot.snapshot_id, dated)
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
    assert aaa.last_price == pytest.approx(0.94)


def test_paper_trading_freezes_halt_then_applies_reopen_penalty(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 11, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "paper_exports",
        live_market_provider="disabled",
        paper_halt_resume_spread_slippage_multiplier=2.0,
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

    halted_result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.82,
                bid_price=0.80,
                ask_price=0.84,
                label="NO_CHASE",
                score=1.0,
                pct_change=-18.0,
                market_status="LULD Pause",
            )
        ],
        export_csv=False,
    )
    halted_position = fetch_paper_positions(db_path, run.run_id)[0]
    assert halted_result.exited_count == 0
    assert halted_position.status == "OPEN"
    assert halted_position.last_price == pytest.approx(0.94)

    current_time = datetime(2026, 3, 26, 11, 12, tzinfo=timezone.utc)
    resumed_result = engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.83,
                bid_price=0.82,
                ask_price=0.86,
                label="NO_CHASE",
                score=1.0,
                pct_change=-17.0,
                market_status="resumed",
                resume_elapsed_seconds=0.0,
                reasons=["resume"],
            )
        ],
        export_csv=False,
    )

    positions = fetch_paper_positions(db_path, run.run_id)
    orders = fetch_paper_orders(db_path, run.run_id)
    exit_order = next(row for row in orders if row.action == "SELL")
    assert resumed_result.exited_count == 1
    assert positions[0].status == "CLOSED"
    assert exit_order.price == pytest.approx(0.82 - ((0.86 - 0.82) * 2.0))
    assert exit_order.fill_slippage_pct == pytest.approx(((0.82 - exit_order.price) / 0.82) * 100.0)
    assert exit_order.reasons[0] == "stop_loss"


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
    spread_slippage_pct = ((1.02 - 0.99) / 1.02) * 1.5 * 100.0
    assert entry_order.price == pytest.approx(1.02 * (1.0 + spread_slippage_pct / 100.0))
    assert entry_order.fill_reference_price == pytest.approx(1.02)
    assert entry_order.fill_slippage_pct == pytest.approx(spread_slippage_pct)
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
        trade_plan_max_concurrent_open_risk_pct=2.0,
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
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
        premarket_min_dollar_volume=50.0,
        trade_plan_max_concurrent_open_risk_pct=5.0,
        paper_predictor_weight_k1=1.0,
        paper_predictor_weight_k2=1.0,
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)

    result = coordinator.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "PRED",
                phase="premarket",
                price=1.00,
                label="WAIT_PULLBACK",
                score=2.10,
                pct_rank=8,
                volume_rank=8,
                predicted=True,
                watchlist_rank=1,
                reasons=["watchlist_predicted", "leader_reclaim", "reclaim_entry_ready"],
                leader_persistence_score=67.0,
                pullback_absorption_score=63.0,
                trap_score=18.0,
                behavioral_score=70.0,
            ),
            _activity(
                "LIVE",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.00,
                pct_rank=1,
                volume_rank=1,
                predicted=False,
                watchlist_rank=None,
                reasons=["pct_leader", "live_dollar_volume_strong"],
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
    pair_diff_csv = export_dir / "paper_bucket_pair_diff.csv"
    cohort_csv = export_dir / "paper_cohort_summary.csv"
    execution_csv = export_dir / "paper_execution_quality.csv"
    capacity_csv = export_dir / "paper_capacity_report.csv"
    trade_log_csv = export_dir / "paper_trade_log.csv"
    backtest_csv = export_dir / "paper_backtest_kpis.csv"
    regime_csv = export_dir / "paper_regime_split.csv"
    predictor_csv = export_dir / "paper_predictor_kpis.csv"
    intraday_decay_csv = export_dir / "paper_intraday_edge_decay.csv"
    catalyst_csv = export_dir / "paper_catalyst_kpis.csv"
    manifest_json = export_dir / "run_manifest.json"
    gate_json = export_dir / "paper_performance_gate.json"
    strategy_runs = fetch_latest_paper_strategy_runs(db_path, limit=10)
    assert result.comparison_path == comparison_csv.resolve()
    assert comparison_csv.exists()
    assert bucket_csv.exists()
    assert diff_csv.exists()
    assert pair_diff_csv.exists()
    assert cohort_csv.exists()
    assert execution_csv.exists()
    assert capacity_csv.exists()
    assert trade_log_csv.exists()
    assert backtest_csv.exists()
    assert regime_csv.exists()
    assert predictor_csv.exists()
    assert intraday_decay_csv.exists()
    assert catalyst_csv.exists()
    assert manifest_json.exists()
    assert gate_json.exists()
    assert len(strategy_runs) >= 5
    csv_text = comparison_csv.read_text(encoding="utf-8")
    bucket_text = bucket_csv.read_text(encoding="utf-8")
    diff_text = diff_csv.read_text(encoding="utf-8")
    pair_diff_rows = _csv_rows(pair_diff_csv)
    cohort_text = cohort_csv.read_text(encoding="utf-8")
    execution_text = execution_csv.read_text(encoding="utf-8")
    capacity_text = capacity_csv.read_text(encoding="utf-8")
    backtest_text = backtest_csv.read_text(encoding="utf-8")
    regime_text = regime_csv.read_text(encoding="utf-8")
    predictor_text = predictor_csv.read_text(encoding="utf-8")
    assert "auto_paper_v1" in csv_text
    assert "baseline_pct_leader_v1" in csv_text
    assert PREDICTOR_WEIGHTED_BUCKET in bucket_text
    assert MOMENTUM_ONLY_BUCKET in bucket_text
    assert WATCHLIST_BLIND_MOMENTUM_BUCKET in bucket_text
    assert "pnl_diff" in diff_text
    assert pair_diff_rows
    assert {
        (row["left_bucket"], row["right_bucket"])
        for row in pair_diff_rows
    } == {
        (PREDICTOR_WEIGHTED_BUCKET, MOMENTUM_ONLY_BUCKET),
        (MOMENTUM_ONLY_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
        (PREDICTOR_WEIGHTED_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
    }
    assert "portfolio_bucket" in cohort_text
    assert "strategy_bucket" in cohort_text
    assert "predicted_starter" in cohort_text
    assert "avg_buy_slippage_pct" in execution_text
    assert "shares_pct_of_bar_volume" in capacity_text
    assert "expectancy_r" in backtest_text
    assert "sortino_ratio" in backtest_text
    assert "max_consecutive_losses" in backtest_text
    assert "regime_bucket" in regime_text
    assert "predictor_hit_rate_pct" in predictor_text
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    gate = json.loads(gate_json.read_text(encoding="utf-8"))
    assert manifest["artifacts"]["paper_trade_log"] == "paper_trade_log.csv"
    assert manifest["predictor_effect"]["enabled"] is True
    assert manifest["predictor_effect"]["k1"] == pytest.approx(1.0)
    assert manifest["predictor_effect"]["k2"] == pytest.approx(1.0)
    assert manifest["slippage_model"]["name"] == "l1_minute_volume_nonlinear_proxy"
    assert manifest["capacity_model"]["conservative_participation_scenarios_pct"] == [1.0, 2.0]
    assert manifest["artifacts"]["paper_capacity_report"] == "paper_capacity_report.csv"
    assert manifest["artifacts"]["paper_intraday_edge_decay"] == "paper_intraday_edge_decay.csv"
    assert manifest["artifacts"]["paper_catalyst_kpis"] == "paper_catalyst_kpis.csv"
    assert manifest["bucket_policies"][PREDICTOR_WEIGHTED_BUCKET]["uses_predictor_weight"] is True
    assert manifest["bucket_policies"][MOMENTUM_ONLY_BUCKET]["uses_predictor_weight"] is False
    assert (
        manifest["bucket_policies"][WATCHLIST_BLIND_MOMENTUM_BUCKET]["uses_watchlist_metadata"]
        is False
    )
    trade_header = trade_log_csv.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "prediction_generated_at" in trade_header
    assert "prediction_cutoff_at" in trade_header
    assert "prediction_source" in trade_header
    assert "catalyst_type" in trade_header
    assert "capacity_limited" in trade_header
    assert gate["status"] == "pass"
    assert gate["edge_judgment_allowed"] is False
    assert any("Step 0 L1 coverage" in item for item in gate["edge_judgment_blockers"])


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
    capacity_rows = _csv_rows(export_dir / "paper_capacity_report.csv")
    thin_capacity = next(
        row
        for row in capacity_rows
        if row["strategy_name"] == PRIMARY_PAPER_STRATEGY
        and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
        and row["symbol"] == "THIN"
    )
    assert float(thin_capacity["shares_pct_of_bar_volume"]) > 0
    assert thin_capacity["capacity_limited"] == "True"


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
                pct_change=5.0,
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
    blind_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=WATCHLIST_BLIND_MOMENTUM_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None and blind_run is not None

    predictor_positions = fetch_paper_positions(db_path, predictor_run.run_id)
    momentum_positions = fetch_paper_positions(db_path, momentum_run.run_id)
    blind_positions = fetch_paper_positions(db_path, blind_run.run_id)
    assert {row.symbol for row in predictor_positions if row.status == "OPEN"} == {"BOTH", "EDGE"}
    assert {row.symbol for row in momentum_positions if row.status == "OPEN"} == {"BOTH"}
    assert {row.symbol for row in blind_positions if row.status == "OPEN"} == {"BOTH"}
    assert all(row.watchlist_rank_at_entry is None for row in blind_positions)
    assert all(row.strategy_bucket == "watchlist_blind_starter" for row in blind_positions)
    assert predictor_run.cash_balance != momentum_run.cash_balance
    assert blind_run.cash_balance != momentum_run.cash_balance

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
    blind_run = fetch_latest_paper_trading_run(
        db_path,
        "auto_paper_v1",
        bucket=WATCHLIST_BLIND_MOMENTUM_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None and blind_run is not None
    predictor_positions = fetch_paper_positions(db_path, predictor_run.run_id)
    momentum_positions = fetch_paper_positions(db_path, momentum_run.run_id)
    blind_positions = fetch_paper_positions(db_path, blind_run.run_id)

    edge_predictor = next(row for row in predictor_positions if row.symbol == "EDGE")
    both_predictor = next(row for row in predictor_positions if row.symbol == "BOTH")
    both_momentum = next(row for row in momentum_positions if row.symbol == "BOTH")
    both_blind = next(row for row in blind_positions if row.symbol == "BOTH")
    assert edge_predictor.status == "CLOSED"
    assert edge_predictor.exit_reason == "stop_loss"
    assert both_predictor.status == "OPEN"
    assert both_momentum.status == "OPEN"
    assert both_blind.status == "OPEN"
    assert predictor_run.realized_pnl != momentum_run.realized_pnl

    diff_csv = export_dir / "paper_bucket_trade_diff.csv"
    diff_text = diff_csv.read_text(encoding="utf-8")
    assert "EDGE" in diff_text
    assert "BOTH" in diff_text
    assert "predictor_total_pnl" in diff_text
    assert "momentum_total_pnl" in diff_text
    diff_rows = _csv_rows(diff_csv)
    assert any(abs(float(row["pnl_diff"])) > 0 for row in diff_rows if row["pnl_diff"])
    pair_diff_rows = _csv_rows(export_dir / "paper_bucket_pair_diff.csv")
    assert {
        (row["left_bucket"], row["right_bucket"])
        for row in pair_diff_rows
    } == {
        (PREDICTOR_WEIGHTED_BUCKET, MOMENTUM_ONLY_BUCKET),
        (MOMENTUM_ONLY_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
        (PREDICTOR_WEIGHTED_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
    }
    trade_rows = _csv_rows(export_dir / "paper_trade_log.csv")
    edge_rows = [
        row
        for row in trade_rows
        if row["symbol"] == "EDGE" and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
    ]
    blind_rows = [
        row
        for row in trade_rows
        if row["bucket"] == WATCHLIST_BLIND_MOMENTUM_BUCKET
    ]
    assert edge_rows
    assert edge_rows[0]["predicted"] == "True"
    assert edge_rows[0]["predictor_score"] == "90.0"
    assert edge_rows[0]["predictor_weight"] == "1.0"
    assert blind_rows
    assert {row["predicted"] for row in blind_rows} == {"False"}
    assert {row["predictor_score"] for row in blind_rows} == {"0.0"}
    assert {row["predictor_weight"] for row in blind_rows} == {"0.0"}
    predictor_kpi_rows = _csv_rows(export_dir / "paper_predictor_kpis.csv")
    primary_kpi = next(
        row
        for row in predictor_kpi_rows
        if row["strategy_name"] == PRIMARY_PAPER_STRATEGY
        and row["bucket"] == PREDICTOR_WEIGHTED_BUCKET
    )
    assert int(primary_kpi["candidate_count"]) == 2
    assert int(primary_kpi["triggered_candidate_count"]) == 2
    assert int(primary_kpi["predicted_trade_count"]) == 2
    assert int(primary_kpi["closed_predicted_trade_count"]) == 1


def test_three_adaptive_buckets_diverge_on_synthetic_metadata_ablation(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    _seed_predictions(
        db_path,
        [
            PremktPrediction(
                symbol="PRED",
                score=90.0,
                max_hold_days=3,
                entry_rationale="predictor-only setup",
                themes=["biotech"],
                filing_summary="8-K",
            ),
            PremktPrediction(
                symbol="WATCH",
                score=90.0,
                max_hold_days=3,
                entry_rationale="watchlist setup",
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
        premarket_min_dollar_volume=50.0,
        trade_plan_max_concurrent_open_risk_pct=5.0,
        paper_predictor_weight_k1=1.0,
    )
    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: current_time)

    coordinator.process_market_activity(
        market_phase="premarket",
        activity=[
            _activity(
                "PRED",
                phase="premarket",
                price=1.00,
                label="WAIT_PULLBACK",
                score=2.10,
                pct_rank=8,
                volume_rank=8,
                pct_change=5.0,
                predicted=True,
                watchlist_rank=1,
            ),
            _activity(
                "WATCH",
                phase="premarket",
                price=1.10,
                label="OPENING_RANGE_CANDIDATE",
                score=3.00,
                pct_rank=4,
                volume_rank=4,
                pct_change=5.0,
                predicted=True,
                watchlist_rank=2,
            ),
            _activity(
                "LIVE",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.00,
                pct_rank=1,
                volume_rank=1,
                pct_change=25.0,
                predicted=False,
                watchlist_rank=None,
            ),
        ],
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
    blind_run = fetch_latest_paper_trading_run(
        db_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=WATCHLIST_BLIND_MOMENTUM_BUCKET,
    )
    assert predictor_run is not None and momentum_run is not None and blind_run is not None

    predictor_symbols = {
        row.symbol
        for row in fetch_paper_positions(db_path, predictor_run.run_id)
        if row.status == "OPEN"
    }
    momentum_symbols = {
        row.symbol
        for row in fetch_paper_positions(db_path, momentum_run.run_id)
        if row.status == "OPEN"
    }
    blind_positions = fetch_paper_positions(db_path, blind_run.run_id)
    blind_symbols = {row.symbol for row in blind_positions if row.status == "OPEN"}

    assert predictor_symbols == {"PRED", "WATCH", "LIVE"}
    assert momentum_symbols == {"WATCH", "LIVE"}
    assert blind_symbols == {"LIVE"}
    assert all(row.watchlist_rank_at_entry is None for row in blind_positions)
    assert all("watchlist_blind_momentum_entry" in row.entry_reasons for row in blind_positions)


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

    coordinator = PaperTradingCoordinator(
        settings,
        now_fn=lambda: datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc),
    )
    payload = coordinator.reporting.evaluate_performance_review_gate()

    assert payload["status"] == "fail"
    assert any("candidate_count is 0" in failure for failure in payload["failures"])


def test_paper_performance_gate_fails_when_predictor_effect_disabled(tmp_path: Path) -> None:
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
        "run_id,strategy_name,bucket,symbol,status,predicted,predictor_score,predictor_weight,"
        "prediction_generated_at,prediction_cutoff_at,prediction_source\n"
        "run-1,auto_paper_v1,predictor_weighted,AAA,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n"
        "run-2,auto_paper_v1,momentum_only,AAA,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n"
        "run-3,auto_paper_v1,watchlist_blind_momentum,BBB,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n",
        encoding="utf-8",
    )
    (export_dir / "paper_predictor_kpis.csv").write_text(
        "strategy_name,bucket,candidate_count,triggered_candidate_count,predicted_trade_count,closed_predicted_trade_count\n"
        "auto_paper_v1,predictor_weighted,0,0,0,0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_comparison.csv").write_text(
        "strategy_name,bucket\n"
        "auto_paper_v1,predictor_weighted\n"
        "auto_paper_v1,momentum_only\n"
        "auto_paper_v1,watchlist_blind_momentum\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_trade_diff.csv").write_text(
        "symbol,predictor_status,momentum_status,pnl_diff\nAAA,CLOSED,CLOSED,0.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_pair_diff.csv").write_text(
        "left_bucket,right_bucket,left_status,right_status,pnl_diff\n"
        "predictor_weighted,momentum_only,CLOSED,CLOSED,0.0\n"
        "momentum_only,watchlist_blind_momentum,CLOSED,OPEN,1.0\n"
        "predictor_weighted,watchlist_blind_momentum,CLOSED,OPEN,1.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_backtest_kpis.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "paper_execution_quality.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "predictor_effect": {
                    "enabled": False,
                    "k1": 0.0,
                    "k2": 0.0,
                }
            }
        ),
        encoding="utf-8",
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc))
    payload = coordinator.reporting.evaluate_performance_review_gate()

    assert payload["status"] == "fail"
    assert any("predictor_effect disabled" in failure for failure in payload["failures"])


def test_paper_performance_gate_fails_on_missing_third_bucket_schema(tmp_path: Path) -> None:
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
        "run-1,auto_paper_v1,predictor_weighted,AAA,CLOSED,True,90.0,1.0\n"
        "run-2,auto_paper_v1,momentum_only,AAA,CLOSED,False,0.0,0.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_predictor_kpis.csv").write_text(
        "strategy_name,bucket,candidate_count,triggered_candidate_count\n"
        "auto_paper_v1,predictor_weighted,1,1\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_trade_diff.csv").write_text(
        "symbol,predictor_status,momentum_status,pnl_diff\nAAA,CLOSED,CLOSED,0.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_comparison.csv").write_text(
        "strategy_name,bucket\n"
        "auto_paper_v1,predictor_weighted\n"
        "auto_paper_v1,momentum_only\n",
        encoding="utf-8",
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc))
    payload = coordinator.reporting.evaluate_performance_review_gate()

    assert payload["status"] == "fail"
    failures = "\n".join(payload["failures"])
    assert "paper_trade_log.csv is missing lineage columns" in failures
    assert "adaptive bucket comparison is missing buckets: watchlist_blind_momentum" in failures
    assert "watchlist_blind_momentum has zero trades" in failures
    assert "paper_bucket_pair_diff.csv is missing or empty" in failures
    assert "run_manifest.json is missing" in failures
    assert WATCHLIST_BLIND_MOMENTUM_BUCKET in payload["required_buckets"]


def test_paper_performance_gate_fails_on_identical_buckets_with_predictor_evidence(tmp_path: Path) -> None:
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
        "run_id,strategy_name,bucket,symbol,status,predicted,predictor_score,predictor_weight,"
        "prediction_generated_at,prediction_cutoff_at,prediction_source\n"
        "run-1,auto_paper_v1,predictor_weighted,AAA,CLOSED,True,90.0,1.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,premkt_prediction\n"
        "run-2,auto_paper_v1,momentum_only,AAA,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n"
        "run-3,auto_paper_v1,watchlist_blind_momentum,AAA,CLOSED,False,0.0,0.0,"
        "2026-03-26T10:00:00+00:00,2026-03-26T10:00:00+00:00,disabled\n",
        encoding="utf-8",
    )
    (export_dir / "paper_predictor_kpis.csv").write_text(
        "strategy_name,bucket,candidate_count,triggered_candidate_count,predicted_trade_count,closed_predicted_trade_count\n"
        "auto_paper_v1,predictor_weighted,1,1,1,1\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_comparison.csv").write_text(
        "strategy_name,bucket\n"
        "auto_paper_v1,predictor_weighted\n"
        "auto_paper_v1,momentum_only\n"
        "auto_paper_v1,watchlist_blind_momentum\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_trade_diff.csv").write_text(
        "symbol,predictor_status,momentum_status,pnl_diff\nAAA,CLOSED,CLOSED,0.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_bucket_pair_diff.csv").write_text(
        "left_bucket,right_bucket,left_status,right_status,pnl_diff\n"
        "predictor_weighted,momentum_only,CLOSED,CLOSED,0.0\n"
        "momentum_only,watchlist_blind_momentum,CLOSED,OPEN,1.0\n"
        "predictor_weighted,watchlist_blind_momentum,CLOSED,OPEN,1.0\n",
        encoding="utf-8",
    )
    (export_dir / "paper_backtest_kpis.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "paper_execution_quality.csv").write_text("strategy_name,bucket\n", encoding="utf-8")
    (export_dir / "run_manifest.json").write_text("{}", encoding="utf-8")

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc))
    payload = coordinator.reporting.evaluate_performance_review_gate()

    assert payload["status"] == "fail"
    assert any("predictor_effect enabled" in failure for failure in payload["failures"])


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


def test_paper_intraday_edge_decay_buckets_closed_trades(tmp_path: Path) -> None:
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
            _paper_position(run_id=run.run_id, symbol="FAST", total_pnl=10.0, day_regime="hot", now=now).model_copy(
                update={"closed_at": now + timedelta(minutes=4), "updated_at": now + timedelta(minutes=4)}
            ),
            _paper_position(run_id=run.run_id, symbol="MID", total_pnl=-5.0, day_regime="hot", now=now).model_copy(
                update={"closed_at": now + timedelta(minutes=45), "updated_at": now + timedelta(minutes=45)}
            ),
            _paper_position(run_id=run.run_id, symbol="LONG", total_pnl=15.0, day_regime="hot", now=now).model_copy(
                update={"closed_at": now + timedelta(minutes=130), "updated_at": now + timedelta(minutes=130)}
            ),
        ],
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: now)
    coordinator.export_trade_log()
    decay_csv = coordinator.export_intraday_edge_decay()
    rows = _csv_rows(decay_csv)
    by_bucket = {row["decay_bucket"]: row for row in rows}

    assert {"0-5m", "30-60m", "2h+"} <= set(by_bucket)
    assert int(by_bucket["0-5m"]["trade_count"]) == 1
    assert float(by_bucket["30-60m"]["total_net_pnl"]) == pytest.approx(-5.0)
    assert float(by_bucket["2h+"]["win_rate"]) == pytest.approx(100.0)


def test_paper_catalyst_kpis_split_by_catalyst_type(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_dir = tmp_path / "paper_exports"
    init_database(db_path)
    now = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    _seed_predictions(
        db_path,
        [
            PremktPrediction(
                symbol="OFFER",
                score=85.0,
                max_hold_days=1,
                entry_rationale="public offering and dilution risk",
                themes=[],
                filing_summary="S-1 registered direct offering",
            ),
            PremktPrediction(
                symbol="FDA",
                score=88.0,
                max_hold_days=1,
                entry_rationale="FDA clinical trial update",
                themes=["biotech"],
                filing_summary="clinical phase 2 topline data",
            ),
        ],
    )
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=export_dir,
        live_market_provider="disabled",
    )
    run = create_paper_trading_run(db_path, "auto_paper_v1", 10_000.0)
    upsert_paper_positions(
        db_path,
        [
            _paper_position(run_id=run.run_id, symbol="OFFER", total_pnl=-20.0, day_regime="hot", now=now),
            _paper_position(run_id=run.run_id, symbol="FDA", total_pnl=40.0, day_regime="hot", now=now),
        ],
    )

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: now)
    coordinator.export_trade_log()
    catalyst_csv = coordinator.export_catalyst_kpis()
    rows = _csv_rows(catalyst_csv)
    by_type = {row["catalyst_type"]: row for row in rows}

    assert "offering_or_dilution" in by_type
    assert "fda_or_clinical" in by_type
    assert float(by_type["offering_or_dilution"]["total_net_pnl"]) == pytest.approx(-20.0)
    assert float(by_type["fda_or_clinical"]["win_rate"]) == pytest.approx(100.0)


def test_paper_backtest_kpis_include_tail_risk_metrics(tmp_path: Path) -> None:
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
    run.max_drawdown_pct = 4.0
    run.total_return_pct = 2.0
    upsert_paper_trading_run(db_path, run)
    pnls = [30.0, -10.0, -20.0, 15.0]
    positions = []
    for index, pnl in enumerate(pnls):
        opened_at = now + timedelta(minutes=index * 10)
        closed_at = opened_at + timedelta(minutes=5 + index * 5)
        positions.append(
            _paper_position(
                run_id=run.run_id,
                symbol=f"R{index}",
                total_pnl=pnl,
                day_regime="hot",
                now=opened_at,
            ).model_copy(update={"closed_at": closed_at, "updated_at": closed_at})
        )
    upsert_paper_positions(db_path, positions)

    coordinator = PaperTradingCoordinator(settings, now_fn=lambda: now)
    coordinator.export_trade_log()
    kpis_csv = coordinator.export_backtest_kpis()
    rows = _csv_rows(kpis_csv)
    row = next(item for item in rows if item["strategy_name"] == "auto_paper_v1")

    assert int(row["max_consecutive_losses"]) == 2
    assert float(row["worst_trade_r"]) == pytest.approx(-4.0)
    assert float(row["p5_trade_r"]) < 0
    assert float(row["cvar_5pct_trade_r"]) <= float(row["p5_trade_r"])
    assert float(row["median_hold_minutes"]) == pytest.approx(12.5)
    assert float(row["p90_hold_minutes"]) > float(row["median_hold_minutes"])
    assert float(row["calmar_ratio"]) == pytest.approx(0.5)
