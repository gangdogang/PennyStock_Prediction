from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    create_snapshot_run,
    fetch_latest_paper_trading_run,
    fetch_paper_orders,
    init_database,
    insert_premkt_predictions,
    upsert_paper_positions,
    upsert_paper_trading_run,
)
from penny_stock_radar.models import MarketActivity, PaperPosition, PremktPrediction
from penny_stock_radar.services.multiday_engine import MULTIDAY_PAPER_STRATEGY, MultidayTradingEngine
from penny_stock_radar.services.paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    PRIMARY_PAPER_STRATEGY,
)
from penny_stock_radar.services.paper_trading import PaperTradingEngine


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
    previous_close: float = 0.8,
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
        previous_close=previous_close,
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


def _prediction(symbol: str = "AAA", score: float = 90.0) -> PremktPrediction:
    return PremktPrediction(
        symbol=symbol,
        score=score,
        max_hold_days=3,
        entry_rationale="strong catalyst",
        themes=["energy"],
        filing_summary="8-K",
    )


def _golden_path(name: str) -> Path:
    return Path(__file__).parent / "golden" / name


def _seed_predictions(db_path: Path, predictions: list[PremktPrediction]) -> None:
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=len(predictions))
    insert_premkt_predictions(db_path, snapshot.snapshot_id, predictions)


def _sanitize_orders(
    db_path: Path,
    *,
    strategy_name: str | None = None,
    bucket: str | None = None,
) -> list[dict[str, object]]:
    run = fetch_latest_paper_trading_run(db_path, strategy_name, bucket=bucket)
    assert run is not None
    orders = fetch_paper_orders(db_path, run.run_id)
    sanitized: list[dict[str, object]] = []
    for order in orders:
        sanitized.append(
            {
                "market_phase": order.market_phase,
                "action": order.action,
                "intent": order.intent,
                "symbol": order.symbol,
                "quantity": order.quantity,
                "requested_quantity": order.requested_quantity,
                "remaining_quantity": order.remaining_quantity,
                "fill_status": order.fill_status,
                "price": round(order.price, 10),
                "notional": round(order.notional, 10),
                "transaction_cost": round(order.transaction_cost, 10),
                "strategy_bucket": order.strategy_bucket,
                "planned_stop_price": round(order.planned_stop_price, 10)
                if order.planned_stop_price is not None
                else None,
                "planned_risk_pct": round(order.planned_risk_pct, 10)
                if order.planned_risk_pct is not None
                else None,
                "fill_reference_price": round(order.fill_reference_price, 10)
                if order.fill_reference_price is not None
                else None,
                "fill_slippage_pct": round(order.fill_slippage_pct, 10)
                if order.fill_slippage_pct is not None
                else None,
                "watchlist_rank_at_entry": order.watchlist_rank_at_entry,
                "reasons": order.reasons,
                "realized_pnl": round(order.realized_pnl, 10)
                if order.realized_pnl is not None
                else None,
                "realized_pnl_pct": round(order.realized_pnl_pct, 10)
                if order.realized_pnl_pct is not None
                else None,
            }
        )
    return sanitized


def _run_intraday_scenario(
    tmp_path: Path,
    *,
    bucket: str,
    k1: float,
    k2: float,
) -> list[dict[str, object]]:
    db_path = tmp_path / f"{bucket}_{k1}_{k2}.sqlite3"
    init_database(db_path)
    _seed_predictions(db_path, [_prediction()])
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / f"{bucket}_exports",
        live_market_provider="disabled",
        premarket_min_dollar_volume=50.0,
        paper_predictor_weight_k1=k1,
        paper_predictor_weight_k2=k2,
    )
    engine = PaperTradingEngine(settings, now_fn=lambda: current_time, bucket=bucket)

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

    current_time = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)
    engine.process_market_activity(
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

    current_time = datetime(2026, 3, 26, 13, 50, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=0.98,
                label="NO_CHASE",
                score=1.5,
                pct_rank=9,
                volume_rank=9,
            )
        ],
        export_csv=False,
    )

    return _sanitize_orders(
        db_path,
        strategy_name=PRIMARY_PAPER_STRATEGY,
        bucket=bucket,
    )


def _run_intraday_time_stop_scenario(tmp_path: Path) -> list[dict[str, object]]:
    db_path = tmp_path / "intraday_time_stop.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "intraday_time_stop_exports",
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
    engine.process_market_activity(
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

    return _sanitize_orders(
        db_path,
        strategy_name=PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )


def _multiday_open_position(
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


def _run_multiday_scenario(tmp_path: Path) -> list[dict[str, object]]:
    db_path = tmp_path / "multiday.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 4, 18, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "multiday_exports",
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
                previous_close=1.0,
                pct_change=25.0,
                leader_persistence_score=72.0,
                pullback_absorption_score=70.0,
                behavioral_score=72.0,
                trap_score=18.0,
            )
        ],
        export_csv=False,
    )

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
                previous_close=1.0,
                pct_change=34.0,
                leader_persistence_score=78.0,
                pullback_absorption_score=75.0,
                behavioral_score=76.0,
                trap_score=18.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 18, 21, 5, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.16,
                label="CONDITIONAL_ENTRY",
                score=4.9,
                previous_close=1.08,
                pct_change=42.0,
                leader_persistence_score=80.0,
                pullback_absorption_score=78.0,
                behavioral_score=79.0,
                trap_score=18.0,
            )
        ],
        export_csv=False,
    )

    current_time = datetime(2026, 4, 19, 15, 30, tzinfo=timezone.utc)
    engine.process_market_activity(
        market_phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.11,
                label="NO_CHASE",
                score=1.5,
                predicted=False,
                previous_close=1.16,
                pct_change=8.0,
                leader_persistence_score=50.0,
                pullback_absorption_score=48.0,
                behavioral_score=50.0,
                trap_score=40.0,
            )
        ],
        export_csv=False,
    )

    return _sanitize_orders(
        db_path,
        strategy_name=MULTIDAY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )


def _run_multiday_overnight_rejected_scenario(tmp_path: Path) -> list[dict[str, object]]:
    db_path = tmp_path / "multiday_overnight_rejected.sqlite3"
    init_database(db_path)
    current_time = datetime(2026, 4, 18, 10, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "multiday_overnight_rejected_exports",
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
                previous_close=1.0,
                pct_change=25.0,
                leader_persistence_score=72.0,
                pullback_absorption_score=70.0,
                behavioral_score=72.0,
                trap_score=18.0,
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
    engine.process_market_activity(
        market_phase="afterhours",
        activity=[],
        export_csv=False,
    )

    return _sanitize_orders(
        db_path,
        strategy_name=MULTIDAY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )


def _run_multiday_loser_replacement_scenario(tmp_path: Path) -> list[dict[str, object]]:
    db_path = tmp_path / "multiday_loser_replacement.sqlite3"
    init_database(db_path)
    now = datetime(2026, 4, 19, 14, 5, tzinfo=timezone.utc)
    settings = AppSettings(
        db_path=db_path,
        paper_trade_dir=tmp_path / "multiday_loser_replacement_exports",
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
            _multiday_open_position(
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
    engine.process_market_activity(
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

    return _sanitize_orders(
        db_path,
        strategy_name=MULTIDAY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )


def _assert_matches_golden(name: str, actual: list[dict[str, object]]) -> None:
    expected = json.loads(_golden_path(name).read_text(encoding="utf-8"))
    assert actual == expected


def test_fill_model_intraday_quote_gap_stop_matches_golden(tmp_path: Path) -> None:
    db_path = tmp_path / "fill_model.sqlite3"
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
    engine.process_market_activity(
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

    actual = _sanitize_orders(db_path)
    _assert_matches_golden("fill_model_intraday_quote_gap_stop.json", actual)


def test_intraday_predictor_weighted_k0_matches_golden(tmp_path: Path) -> None:
    actual = _run_intraday_scenario(
        tmp_path,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
        k1=0.0,
        k2=0.0,
    )
    _assert_matches_golden("intraday_predictor_weighted_k0.json", actual)


def test_intraday_momentum_only_matches_golden(tmp_path: Path) -> None:
    actual = _run_intraday_scenario(
        tmp_path,
        bucket=MOMENTUM_ONLY_BUCKET,
        k1=1.0,
        k2=1.0,
    )
    _assert_matches_golden("intraday_momentum_only.json", actual)


def test_intraday_adaptive_time_stop_matches_golden(tmp_path: Path) -> None:
    actual = _run_intraday_time_stop_scenario(tmp_path)
    _assert_matches_golden("intraday_adaptive_time_stop.json", actual)


def test_multiday_predictor_weighted_day2_exit_matches_golden(tmp_path: Path) -> None:
    actual = _run_multiday_scenario(tmp_path)
    _assert_matches_golden("multiday_predictor_weighted_day2_exit.json", actual)


def test_multiday_overnight_hold_rejected_matches_golden(tmp_path: Path) -> None:
    actual = _run_multiday_overnight_rejected_scenario(tmp_path)
    _assert_matches_golden("multiday_overnight_hold_rejected.json", actual)


def test_multiday_loser_replacement_matches_golden(tmp_path: Path) -> None:
    actual = _run_multiday_loser_replacement_scenario(tmp_path)
    _assert_matches_golden("multiday_loser_replacement.json", actual)


def test_intraday_predictor_weighted_snapshot_changes_when_predictor_weights_enabled(
    tmp_path: Path,
) -> None:
    baseline = _run_intraday_scenario(
        tmp_path / "baseline",
        bucket=PREDICTOR_WEIGHTED_BUCKET,
        k1=0.0,
        k2=0.0,
    )
    weighted = _run_intraday_scenario(
        tmp_path / "weighted",
        bucket=PREDICTOR_WEIGHTED_BUCKET,
        k1=1.0,
        k2=1.0,
    )

    assert baseline != weighted
    assert baseline[0]["quantity"] != weighted[0]["quantity"]
