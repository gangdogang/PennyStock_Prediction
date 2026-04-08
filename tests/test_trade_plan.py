from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_paper_trading_run,
    init_database,
    upsert_paper_positions,
)
from penny_stock_radar.models import MarketActivity, PaperPosition
from penny_stock_radar.services.paper_trading import PRIMARY_PAPER_STRATEGY
from penny_stock_radar.services.trade_plan import TradePlanService


def _activity(
    symbol: str,
    *,
    phase: str,
    price: float,
    label: str,
    score: float,
    predicted: bool = True,
    pct_rank: int = 1,
    volume_rank: int = 1,
    pct_change: float = 25.0,
    watchlist_rank: int | None = None,
    spread_pct: float = 0.02,
    dollar_volume: float | None = None,
    data_age_seconds: float | None = 1.0,
    has_live_trade: bool = True,
    has_live_quote: bool = True,
    behavioral_score: float = 70.0,
    trap_score: float = 15.0,
    leader_persistence_score: float = 66.0,
    pullback_absorption_score: float = 67.0,
    reasons: list[str] | None = None,
) -> MarketActivity:
    return MarketActivity(
        symbol=symbol,
        market_phase=phase,
        source="test",
        last_price=price,
        bid_price=max(price - 0.01, 0.0001),
        ask_price=price + 0.01,
        previous_close=max(price * 0.8, 0.01),
        pct_change=pct_change,
        volume=500_000,
        dollar_volume=dollar_volume if dollar_volume is not None else price * 500_000,
        trade_size=1_000,
        spread_pct=spread_pct,
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


def _position(
    *,
    run_id: str,
    symbol: str,
    now: datetime,
    quantity: int,
    average_entry_price: float,
    last_price: float,
    total_pnl: float,
    status: str = "OPEN",
    entry_phase: str = "premarket",
    strategy_bucket: str = "predicted_starter",
    stop_price: float = 0.95,
    add_count: int = 0,
    partial_exit_count: int = 0,
) -> PaperPosition:
    return PaperPosition(
        position_id=f"pos-{symbol.lower()}",
        run_id=run_id,
        symbol=symbol,
        status=status,
        entry_phase=entry_phase,
        entry_label="OPENING_RANGE_CANDIDATE",
        exit_reason="stop_loss" if status == "CLOSED" else None,
        quantity=quantity,
        average_entry_price=average_entry_price,
        last_price=last_price,
        cost_basis=quantity * average_entry_price,
        market_value=0.0 if status == "CLOSED" else quantity * last_price,
        realized_pnl=total_pnl if status == "CLOSED" else 0.0,
        unrealized_pnl=0.0 if status == "CLOSED" else total_pnl,
        total_pnl=total_pnl,
        stop_price=stop_price,
        planned_stop_price=stop_price,
        planned_risk_pct=5.0,
        highest_price=max(last_price, average_entry_price),
        add_count=add_count,
        partial_exit_count=partial_exit_count,
        strategy_bucket=strategy_bucket,
        fill_reference_price=average_entry_price,
        fill_slippage_pct=0.15,
        day_regime="normal",
        watchlist_rank_at_entry=1,
        entry_reasons=[strategy_bucket],
        exit_reasons=["stop_loss"] if status == "CLOSED" else [],
        opened_at=now,
        updated_at=now,
        closed_at=now if status == "CLOSED" else None,
    )


def test_trade_plan_risk_first_keeps_live_exception_watch_only(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_root = tmp_path / "live_outputs"
    init_database(db_path)

    now = datetime(2026, 3, 26, 10, 15, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=export_root)

    result = service.generate(
        phase="premarket",
        activity=[
            _activity(
                "MEGA",
                phase="premarket",
                price=4.20,
                label="OPENING_RANGE_CANDIDATE",
                score=3.2,
                predicted=False,
                pct_rank=2,
                volume_rank=2,
            )
        ],
        export=True,
    )

    assert result.market_phase == "premarket"
    assert result.csv_path.exists()
    assert result.checklist_path.exists()
    candidate = next(row for row in result.candidates if row.symbol == "MEGA")
    assert candidate.bucket == "live_exception"
    assert candidate.actionability == "watch_only"
    assert "risk_first_live_exception_watch_only" in candidate.reasons
    checklist = result.checklist_path.read_text(encoding="utf-8")
    assert "## 지켜보기" in checklist
    assert "MEGA" in checklist


def test_trade_plan_preserves_priority_order_within_same_bucket(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)

    now = datetime(2026, 3, 26, 10, 15, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=tmp_path / "live_outputs")

    result = service.generate(
        phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.10,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=True,
                pct_rank=2,
                volume_rank=2,
                watchlist_rank=2,
            ),
            _activity(
                "ZZZ",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.3,
                predicted=True,
                pct_rank=3,
                volume_rank=3,
                watchlist_rank=1,
            ),
        ],
        export=False,
    )

    actionable = [row.symbol for row in result.candidates if row.actionability == "actionable"]
    assert actionable[:2] == ["ZZZ", "AAA"]


def test_trade_plan_blocks_stale_quote_and_filing_risks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    monkeypatch.setattr(
        "penny_stock_radar.services.trade_plan.fetch_latest_filings",
        lambda *args, **kwargs: [
            {
                "symbol": "AAA",
                "form": "S-1",
                "summary": "Registration statement with warrant coverage and reverse split language",
                "matched_keywords": '["registration statement", "reverse split"]',
            }
        ],
    )

    now = datetime(2026, 3, 26, 10, 20, tzinfo=timezone.utc)
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=tmp_path / "live_outputs")
    result = service.generate(
        phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.20,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=True,
                spread_pct=0.12,
                dollar_volume=50_000.0,
                data_age_seconds=90.0,
                has_live_quote=False,
            )
        ],
        export=False,
    )

    candidate = next(row for row in result.candidates if row.symbol == "AAA")
    assert candidate.bucket == "predicted_starter"
    assert candidate.actionability == "blocked"
    assert "stale_data" in candidate.blockers
    assert "missing_live_quote" in candidate.blockers
    assert "spread_too_wide" in candidate.blockers
    assert "low_dollar_volume" in candidate.blockers
    assert "halt_suspected" in candidate.blockers
    assert "dilution_risk" in candidate.blockers
    assert "reverse_split_risk" in candidate.blockers
    assert "warrant_risk" in candidate.blockers


def test_trade_plan_enforces_daily_loss_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)

    now = datetime(2026, 3, 26, 10, 25, tzinfo=timezone.utc)
    run = create_paper_trading_run(db_path, PRIMARY_PAPER_STRATEGY, 10_000.0)
    upsert_paper_positions(
        db_path,
        [
            _position(
                run_id=run.run_id,
                symbol="LOSS",
                now=now,
                quantity=1_000,
                average_entry_price=1.00,
                last_price=0.75,
                total_pnl=-250.0,
                status="CLOSED",
            )
        ],
    )

    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=tmp_path / "live_outputs")
    result = service.generate(
        phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.10,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=True,
            )
        ],
        export=False,
    )

    candidate = next(row for row in result.candidates if row.symbol == "AAA")
    assert result.daily_loss_locked is True
    assert result.current_day_pnl == pytest.approx(-250.0)
    assert candidate.actionability == "blocked"
    assert "daily_loss_lock" in candidate.blockers


def test_trade_plan_enforces_concurrent_open_risk_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)

    now = datetime(2026, 3, 26, 10, 30, tzinfo=timezone.utc)
    run = create_paper_trading_run(db_path, PRIMARY_PAPER_STRATEGY, 10_000.0)
    upsert_paper_positions(
        db_path,
        [
            _position(
                run_id=run.run_id,
                symbol="OPEN",
                now=now,
                quantity=3_000,
                average_entry_price=1.00,
                last_price=1.00,
                total_pnl=0.0,
                status="OPEN",
            )
        ],
    )

    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=tmp_path / "live_outputs")
    result = service.generate(
        phase="premarket",
        activity=[
            _activity(
                "AAA",
                phase="premarket",
                price=1.05,
                label="OPENING_RANGE_CANDIDATE",
                score=4.4,
                predicted=True,
            )
        ],
        export=False,
    )

    candidate = next(row for row in result.candidates if row.symbol == "AAA")
    assert result.current_open_risk == pytest.approx(150.0)
    assert candidate.actionability == "blocked"
    assert "concurrent_open_risk_limit" in candidate.blockers


def test_trade_plan_makes_winner_add_actionable_before_cutoff(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    export_root = tmp_path / "live_outputs"
    init_database(db_path)

    now = datetime(2026, 3, 26, 14, 0, tzinfo=timezone.utc)
    run = create_paper_trading_run(db_path, PRIMARY_PAPER_STRATEGY, 10_000.0)
    upsert_paper_positions(
        db_path,
        [
            _position(
                run_id=run.run_id,
                symbol="AAA",
                now=now,
                quantity=100,
                average_entry_price=1.00,
                last_price=1.08,
                total_pnl=8.0,
                status="OPEN",
            )
        ],
    )

    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    service = TradePlanService(settings, now_fn=lambda: now, export_root=export_root)
    result = service.generate(
        phase="regular",
        activity=[
            _activity(
                "AAA",
                phase="regular",
                price=1.08,
                label="CONDITIONAL_ENTRY",
                score=4.6,
                predicted=True,
                pct_rank=1,
                volume_rank=1,
            ),
            _activity(
                "BBB",
                phase="regular",
                price=1.15,
                label="CONDITIONAL_ENTRY",
                score=4.4,
                predicted=True,
                pct_rank=2,
                volume_rank=2,
                watchlist_rank=2,
            ),
            _activity(
                "CCC",
                phase="regular",
                price=1.20,
                label="CONDITIONAL_ENTRY",
                score=4.3,
                predicted=True,
                pct_rank=3,
                volume_rank=3,
                watchlist_rank=3,
            ),
        ],
        export=True,
    )

    candidate = next(row for row in result.candidates if row.symbol == "AAA")
    assert result.regime == "hot"
    assert candidate.bucket == "winner_add"
    assert candidate.actionability == "actionable"
    assert candidate.suggested_size > 0
    checklist = result.checklist_path.read_text(encoding="utf-8")
    assert "## 지금 매매 가능" in checklist
    assert "AAA" in checklist
