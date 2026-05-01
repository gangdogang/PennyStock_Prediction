from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from penny_stock_radar.models import MarketActivity
from penny_stock_radar.services.replay_bar_cache import (
    PreparedBarSnapshot,
    PreparedMinuteBar,
    PreparedSymbolSeries,
)
from penny_stock_radar.services.setup_state import AISetupJudgeV1, SetupContextBuilder

EASTERN = ZoneInfo("America/New_York")


def test_setup_context_builder_detects_vwap_reclaim_starter() -> None:
    snapshot = _snapshot(
        [
            _bar("2026-04-10T07:58:00-04:00", 1.00, 1.02, 0.99, 1.00, 1_000),
            _bar("2026-04-10T07:59:00-04:00", 1.00, 1.01, 0.97, 0.98, 1_000),
            _bar("2026-04-10T08:00:00-04:00", 0.99, 1.12, 0.98, 1.10, 50_000),
        ]
    )
    row = _activity(
        last_price=1.10,
        high=1.12,
        low=0.98,
        volume=52_000,
        dollar_volume=57_000,
        pct_rank=1,
        volume_rank=2,
        score=4.2,
        label="OPENING_RANGE_CANDIDATE",
        predicted=True,
        reasons=["pct_leader", "live_dollar_volume_strong", "spread_ok", "watchlist_predicted"],
    )
    builder = SetupContextBuilder(
        run_id="run_setup",
        min_dollar_volume=10_000,
        max_spread_pct=0.05,
    )

    context = builder.build(
        market_date="2026-04-10",
        bucket="predictor_weighted",
        row=row,
        snapshot=snapshot,
        observed_at=_dt("2026-04-10T08:00:00-04:00"),
        cutoff_at=_dt("2026-04-10T08:00:00-04:00"),
    )
    judgement = AISetupJudgeV1().judge(context)

    assert context.vwap_reclaim is True
    assert context.hod_breakout is True
    assert context.volume_expansion is True
    assert context.true_leader is True
    assert context.distance_to_vwap_pct == pytest.approx(0.386100, abs=1e-6)
    assert judgement.setup_state == "STARTER_VALID"
    assert judgement.action_bias == "starter_ok"
    assert judgement.quality >= 90
    assert judgement.risk < 20
    assert "vwap_reclaim" in judgement.reasons


def test_setup_judge_flags_failed_breakout_as_exit_for_open_position() -> None:
    snapshot = _snapshot(
        [
            _bar("2026-04-10T08:00:00-04:00", 1.00, 1.20, 0.98, 1.18, 40_000),
            _bar("2026-04-10T08:01:00-04:00", 1.18, 1.24, 1.02, 1.08, 70_000),
        ]
    )
    row = _activity(
        last_price=1.08,
        high=1.24,
        low=1.02,
        volume=110_000,
        dollar_volume=120_000,
        pct_rank=7,
        volume_rank=3,
        score=2.0,
        label="WAIT_PULLBACK",
        predicted=True,
        reasons=["wait_for_reclaim"],
    )
    builder = SetupContextBuilder(
        run_id="run_setup",
        min_dollar_volume=10_000,
        max_spread_pct=0.05,
    )

    context = builder.build(
        market_date="2026-04-10",
        bucket="predictor_weighted",
        row=row,
        snapshot=snapshot,
        observed_at=_dt("2026-04-10T08:01:00-04:00"),
        cutoff_at=_dt("2026-04-10T08:00:00-04:00"),
        position_opened_at=_dt("2026-04-10T08:00:00-04:00"),
        position_entry_price=1.18,
    )
    judgement = AISetupJudgeV1().judge(context)

    assert context.failed_breakout is True
    assert context.position_state == "open"
    assert context.unrealized_pct == pytest.approx(-8.474576, abs=1e-6)
    assert judgement.setup_state == "EXIT_FAIL"
    assert judgement.action_bias == "exit"
    assert judgement.risk >= 50


def test_setup_context_keeps_missing_l1_as_lower_confidence() -> None:
    snapshot = _snapshot(
        [
            _bar("2026-04-10T08:00:00-04:00", 1.00, 1.05, 0.99, 1.04, 10_000, bid=None, ask=None),
            _bar("2026-04-10T08:01:00-04:00", 1.04, 1.10, 1.03, 1.09, 30_000, bid=None, ask=None),
        ]
    )
    row = _activity(
        last_price=1.09,
        high=1.10,
        low=1.03,
        volume=40_000,
        dollar_volume=43_000,
        pct_rank=1,
        volume_rank=1,
        score=3.8,
        label="OPENING_RANGE_CANDIDATE",
        predicted=True,
        spread_pct=None,
        reasons=["pct_momentum", "watchlist_predicted"],
    )
    builder = SetupContextBuilder(
        run_id="run_setup",
        min_dollar_volume=10_000,
        max_spread_pct=0.05,
    )

    context = builder.build(
        market_date="2026-04-10",
        bucket="predictor_weighted",
        row=row,
        snapshot=snapshot,
        observed_at=_dt("2026-04-10T08:01:00-04:00"),
        cutoff_at=_dt("2026-04-10T08:00:00-04:00"),
    )
    judgement = AISetupJudgeV1().judge(context)

    assert context.has_l1_proxy is False
    assert context.spread_ok is True
    assert judgement.confidence < 0.8
    assert judgement.setup_state in {"STARTER_VALID", "ORB_BREAKOUT", "WATCH_LEADER"}


def _snapshot(bars: list[PreparedMinuteBar]) -> PreparedBarSnapshot:
    series = PreparedSymbolSeries.from_bars("AAA", bars)
    return PreparedBarSnapshot(series=series, index=len(series.bars) - 1)


def _bar(
    timestamp: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    *,
    bid: float | None = 1.0,
    ask: float | None = 1.01,
) -> PreparedMinuteBar:
    bar_at = _dt(timestamp)
    spread_pct = None
    if bid is not None and ask is not None:
        spread_pct = (ask - bid) / ((ask + bid) / 2.0)
    return PreparedMinuteBar(
        symbol="AAA",
        market_date=bar_at.date().isoformat(),
        market_phase="premarket",
        bar_at=bar_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        bid_price=bid,
        ask_price=ask,
        bid_price_missing=bid is None,
        ask_price_missing=ask is None,
        spread_pct=spread_pct,
    )


def _activity(
    *,
    last_price: float,
    high: float,
    low: float,
    volume: float,
    dollar_volume: float,
    pct_rank: int,
    volume_rank: int,
    score: float,
    label: str,
    predicted: bool,
    reasons: list[str],
    spread_pct: float | None = 0.01,
) -> MarketActivity:
    return MarketActivity(
        symbol="AAA",
        market_phase="premarket",
        source="fixture",
        last_price=last_price,
        bid_price=last_price - 0.01 if spread_pct is not None else None,
        ask_price=last_price + 0.01 if spread_pct is not None else None,
        bar_high_price=high,
        bar_low_price=low,
        previous_close=1.0,
        pct_change=(last_price - 1.0) * 100.0,
        volume=volume,
        dollar_volume=dollar_volume,
        spread_pct=spread_pct,
        has_live_trade=True,
        has_live_quote=spread_pct is not None,
        pct_rank=pct_rank,
        volume_rank=volume_rank,
        watchlist_rank=1 if predicted else None,
        watchlist_score=4.0 if predicted else None,
        predicted=predicted,
        analysis_label=label,
        analysis_score=score,
        reasons=reasons,
        created_at=_dt("2026-04-10T08:00:00-04:00"),
    )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(EASTERN)
