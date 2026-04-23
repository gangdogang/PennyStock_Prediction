from __future__ import annotations

import math

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.models import MarketActivity, PaperPosition
from penny_stock_radar.services.fill_model import FillModel


def _activity(
    *,
    market_cap: int | None,
    volume: float = 10_000.0,
    dollar_volume: float = 3_000_000.0,
    bid_price: float = 1.00,
    ask_price: float = 1.01,
    last_price: float = 1.00,
    market_status: str = "open",
    resume_elapsed_seconds: float | None = None,
    reasons: list[str] | None = None,
) -> MarketActivity:
    return MarketActivity(
        symbol="AAA",
        market_phase="regular",
        source="test",
        market_cap=market_cap,
        last_price=last_price,
        bid_price=bid_price,
        ask_price=ask_price,
        previous_close=1.00,
        pct_change=0.0,
        volume=volume,
        dollar_volume=dollar_volume,
        trade_size=1_000,
        spread_pct=0.02,
        market_status=market_status,
        data_age_seconds=0.0,
        resume_elapsed_seconds=resume_elapsed_seconds,
        has_live_trade=True,
        has_live_quote=True,
        analysis_label="CONDITIONAL_ENTRY",
        analysis_score=4.0,
        reasons=reasons or ["watchlist_predicted"],
    )


def _position() -> PaperPosition:
    return PaperPosition(
        position_id="pos-aaa",
        run_id="run-aaa",
        symbol="AAA",
        entry_phase="regular",
        quantity=100,
        average_entry_price=1.00,
        last_price=1.00,
        cost_basis=100.0,
        market_value=100.0,
        stop_price=0.95,
        planned_stop_price=0.95,
        planned_risk_pct=5.0,
        highest_price=1.05,
        strategy_bucket="starter",
    )


def test_fill_model_small_market_cap_uses_stricter_volume_cap() -> None:
    settings = AppSettings(
        live_market_provider="disabled",
        paper_volume_cap_large_dollar_volume=10_000_000.0,
        paper_volume_cap_mid_dollar_volume=2_000_000.0,
        paper_volume_cap_large_pct=10.0,
        paper_volume_cap_mid_pct=5.0,
        paper_volume_cap_small_pct=2.0,
        paper_volume_cap_small_market_cap_threshold=500_000_000,
        paper_volume_cap_small_market_cap_scale=0.5,
    )
    fill_model = FillModel(settings)
    requested_quantity = 1_000

    large_cap_fill = fill_model.buy(
        _activity(market_cap=600_000_000),
        market_phase="regular",
        requested_quantity=requested_quantity,
    )
    small_cap_fill = fill_model.buy(
        _activity(market_cap=100_000_000),
        market_phase="regular",
        requested_quantity=requested_quantity,
    )

    assert large_cap_fill.quantity == 500
    assert small_cap_fill.quantity == 250
    assert large_cap_fill.remaining_quantity == 500
    assert small_cap_fill.remaining_quantity == 750
    assert large_cap_fill.fill_status == "PARTIAL"
    assert small_cap_fill.fill_status == "PARTIAL"


def test_fill_model_halt_resume_penalty_decays_after_five_minutes() -> None:
    settings = AppSettings(
        live_market_provider="disabled",
        paper_halt_resume_spread_slippage_multiplier=2.0,
        paper_halt_resume_decay_minutes=3.0,
    )
    fill_model = FillModel(settings)
    position = _position()
    early_resume_row = _activity(
        market_cap=600_000_000,
        bid_price=1.00,
        ask_price=1.02,
        market_status="resumed",
        resume_elapsed_seconds=0.0,
        reasons=["resume"],
    )
    late_resume_row = _activity(
        market_cap=600_000_000,
        bid_price=1.00,
        ask_price=1.02,
        market_status="resumed",
        resume_elapsed_seconds=300.0,
        reasons=["resume"],
    )

    early_fill = fill_model.sell(
        position=position,
        row=early_resume_row,
        reason="trail_exit",
        market_phase="regular",
        requested_quantity=position.quantity,
    )
    late_fill = fill_model.sell(
        position=position,
        row=late_resume_row,
        reason="trail_exit",
        market_phase="regular",
        requested_quantity=position.quantity,
    )

    expected_late_penalty = 0.02 * 2.0 * math.exp(-(5.0 / 3.0))

    assert early_fill.fill_price == pytest.approx(0.96)
    assert late_fill.fill_price == pytest.approx(1.00 - expected_late_penalty)
    assert early_fill.fill_price < late_fill.fill_price
    assert early_fill.fill_slippage_pct > late_fill.fill_slippage_pct


def test_fill_model_high_participation_has_nonlinear_slippage_and_capacity_flag() -> None:
    settings = AppSettings(
        live_market_provider="disabled",
        paper_volume_cap_large_dollar_volume=10_000_000.0,
        paper_volume_cap_mid_dollar_volume=2_000_000.0,
        paper_volume_cap_large_pct=10.0,
        paper_volume_cap_mid_pct=5.0,
        paper_volume_cap_small_pct=2.0,
        paper_capacity_limited_pct=2.0,
    )
    fill_model = FillModel(settings)
    row = _activity(
        market_cap=600_000_000,
        volume=10_000.0,
        dollar_volume=10_000_000.0,
        bid_price=0.99,
        ask_price=1.00,
        last_price=0.995,
    )

    low_participation = fill_model.buy(
        row,
        market_phase="regular",
        requested_quantity=100,
    )
    high_participation = fill_model.buy(
        row,
        market_phase="regular",
        requested_quantity=1_000,
    )

    assert low_participation.shares_pct_of_bar_volume == pytest.approx(1.0)
    assert high_participation.shares_pct_of_bar_volume == pytest.approx(10.0)
    assert high_participation.participation_slippage_pct > low_participation.participation_slippage_pct
    assert high_participation.fill_slippage_pct > low_participation.fill_slippage_pct
    assert high_participation.fill_price > low_participation.fill_price
    assert high_participation.estimated_capacity_at_1pct_volume == pytest.approx(
        high_participation.fill_price * 100.0
    )
    assert high_participation.estimated_capacity_at_2pct_volume == pytest.approx(
        high_participation.fill_price * 200.0
    )
    assert high_participation.capacity_limited is True
