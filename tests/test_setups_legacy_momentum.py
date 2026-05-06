from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.models import MarketActivity, PaperPosition, PaperTradingRun
from penny_stock_radar.services.engine_rules import entry as entry_rules
from penny_stock_radar.services.engine_rules import exit as exit_rules
from penny_stock_radar.services.engine_rules import profit as profit_rules
from penny_stock_radar.services.engine_rules.entry import EntryRuleDeps
from penny_stock_radar.services.engine_rules.exit import ExitRuleDeps
from penny_stock_radar.services.engine_rules.profit import ProfitRuleDeps
from penny_stock_radar.services.fill_model import FillModel
from penny_stock_radar.services.setups import ACTION_ADD, ACTION_ENTER, ACTION_SKIP, SetupEvalContext
from penny_stock_radar.services.setups.legacy_momentum import LegacyMomentumSetup

NOW = datetime(2026, 3, 26, 10, 5, tzinfo=timezone.utc)
REGULAR_NOW = datetime(2026, 3, 26, 13, 40, tzinfo=timezone.utc)


def _activity(
    symbol: str = "TEST",
    *,
    bucket: str = "predictor_weighted",
    phase: str = "premarket",
    price: float = 1.00,
    label: str = "OPENING_RANGE_CANDIDATE",
    score: float = 4.4,
    predicted: bool = True,
    watchlist_rank: int | None = 1,
    pct_rank: int = 1,
    volume_rank: int = 1,
) -> MarketActivity:
    blind = bucket == "watchlist_blind_momentum"
    return MarketActivity(
        symbol=symbol,
        market_phase=phase,
        source="test",
        last_price=price,
        bid_price=price,
        ask_price=price,
        previous_close=0.80,
        pct_change=25.0,
        volume=500_000,
        dollar_volume=price * 500_000,
        trade_size=1_000,
        spread_pct=0.02,
        market_status="open",
        market_data_at=NOW,
        data_age_seconds=0.0,
        has_live_trade=True,
        has_live_quote=True,
        pct_rank=pct_rank,
        volume_rank=volume_rank,
        watchlist_rank=None if blind else watchlist_rank,
        watchlist_score=None if blind else 4.5,
        predicted=False if blind else predicted,
        leader_persistence_score=70.0,
        pullback_absorption_score=70.0,
        trap_score=10.0,
        behavioral_score=70.0,
        analysis_label=label,
        analysis_score=score,
        reasons=["live_dollar_volume_strong", "pct_leader"],
    )


def _open_position(
    *,
    run_id: str = "run-1",
    price: float = 1.00,
    last_price: float | None = None,
) -> PaperPosition:
    mark = last_price if last_price is not None else price * 1.04
    return PaperPosition(
        position_id="pos-test",
        run_id=run_id,
        symbol="TEST",
        status="OPEN",
        entry_phase="premarket",
        entry_label="OPENING_RANGE_CANDIDATE",
        quantity=100,
        average_entry_price=price,
        last_price=mark,
        cost_basis=100 * price,
        market_value=100 * mark,
        unrealized_pnl=(mark - price) * 100,
        total_pnl=(mark - price) * 100,
        stop_price=price * 0.95,
        planned_stop_price=price * 0.95,
        planned_risk_pct=5.0,
        highest_price=max(mark, price),
        strategy_bucket="predicted_starter",
        fill_reference_price=price,
        fill_slippage_pct=0.15,
        day_regime="normal",
        watchlist_rank_at_entry=1,
        entry_reasons=["predicted_starter"],
        opened_at=NOW,
        updated_at=NOW,
    )


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "radar.sqlite3",
        live_market_provider="disabled",
        premarket_min_dollar_volume=50.0,
        paper_min_trade_fee=0.01,
        paper_notional_fee_rate_pct=0.01,
        paper_fill_slippage_pct=0.0001,
        **overrides,
    )


def _ids() -> Iterator[str]:
    count = 0
    while True:
        count += 1
        yield f"id-{count}"


def _entry_deps(settings: AppSettings, *, bucket: str) -> EntryRuleDeps:
    ids = _ids()
    fill_model = FillModel(settings)
    return EntryRuleDeps(
        settings=settings,
        fill_model=fill_model,
        strategy_mode="adaptive",
        bucket=bucket,
        predictor_score_by_symbol={"TEST": 90.0},
        now_fn=lambda: NOW,
        market_date=lambda value: value.date().isoformat(),
        max_open_positions=lambda: 5,
        position_size=lambda **kwargs: 100,
        daily_loss_locked=lambda **kwargs: False,
        has_tradeable_live_state=lambda row, market_phase: bool(
            row.has_live_quote and row.data_age_seconds is not None and row.data_age_seconds <= 15 and row.market_status == "open"
        ),
        store_position_market_status=lambda position, market_status: None,
        planned_risk_pct=exit_rules.planned_risk_pct,
        stop_price=lambda position: exit_rules.stop_price(settings, "adaptive", position),
        decision_basis_price=lambda position: position.fill_reference_price or position.average_entry_price,
        advice_reasons=lambda advice: [],
        order_id_factory=lambda: next(ids),
    )


def _exit_deps(settings: AppSettings) -> ExitRuleDeps:
    ids = _ids()
    fill_model = FillModel(settings)
    return ExitRuleDeps(
        settings=settings,
        fill_model=fill_model,
        strategy_mode="adaptive",
        position_market_status=lambda position: "",
        decision_basis_price=lambda position: position.fill_reference_price or position.average_entry_price,
        order_id_factory=lambda: next(ids),
    )


def _profit_deps(settings: AppSettings) -> ProfitRuleDeps:
    ids = _ids()
    fill_model = FillModel(settings)
    return ProfitRuleDeps(
        settings=settings,
        fill_model=fill_model,
        strategy_mode="adaptive",
        decision_basis_price=lambda position: position.fill_reference_price or position.average_entry_price,
        stop_price=lambda position: exit_rules.stop_price(settings, "adaptive", position),
        order_id_factory=lambda: next(ids),
    )


def _ctx(
    *,
    deps: EntryRuleDeps,
    row: MarketActivity,
    bucket: str,
    position: PaperPosition | None = None,
    open_symbols: frozenset[str] = frozenset(),
    traded_today: frozenset[str] = frozenset(),
    bar_at: datetime = NOW,
) -> SetupEvalContext:
    return SetupEvalContext(
        market_activity=row,
        bar_at=bar_at,
        bucket=bucket,
        deps=deps,
        open_position=position,
        setup_judgement=None,
        market_phase=row.market_phase,
        open_symbols=open_symbols,
        traded_today=traded_today,
    )


@pytest.mark.parametrize(
    "bucket",
    [
        "predictor_weighted",
        "momentum_only",
        "watchlist_momentum",
        "watchlist_blind_momentum",
    ],
)
def test_legacy_momentum_detect_matches_existing_entry_dispatch(
    tmp_path: Path,
    bucket: str,
) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket=bucket)
    row = _activity(bucket=bucket)
    ctx = _ctx(deps=deps, row=row, bucket=bucket)

    expected = entry_rules.eligible_for_entry(
        deps,
        row=row,
        advice=None,
        market_phase=row.market_phase,
        open_symbols=set(),
        traded_today=set(),
    )

    assert LegacyMomentumSetup().detect(ctx) is expected
    assert expected is True


def test_legacy_momentum_detect_matches_existing_blocked_symbol_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket="predictor_weighted")
    row = _activity(bucket="predictor_weighted")
    ctx = _ctx(
        deps=deps,
        row=row,
        bucket="predictor_weighted",
        open_symbols=frozenset({"TEST"}),
    )

    assert LegacyMomentumSetup().detect(ctx) is False
    assert entry_rules.eligible_for_entry(
        deps,
        row=row,
        advice=None,
        market_phase=row.market_phase,
        open_symbols={"TEST"},
        traded_today=set(),
    ) is False


def test_legacy_momentum_evaluate_skip_matches_blocked_entry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket="predictor_weighted")
    row = _activity(bucket="predictor_weighted")
    ctx = _ctx(
        deps=deps,
        row=row,
        bucket="predictor_weighted",
        traded_today=frozenset({"TEST"}),
    )

    decision = LegacyMomentumSetup().evaluate(ctx)

    assert decision.action == ACTION_SKIP
    assert decision.size_fraction == 0.0


@pytest.mark.parametrize(
    "bucket",
    [
        "predictor_weighted",
        "momentum_only",
        "watchlist_momentum",
        "watchlist_blind_momentum",
    ],
)
def test_legacy_momentum_evaluate_entry_matches_existing_fraction_and_tag(
    tmp_path: Path,
    bucket: str,
) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket=bucket)
    row = _activity(bucket=bucket)
    ctx = _ctx(deps=deps, row=row, bucket=bucket)
    expected_tag = entry_rules.adaptive_entry_tag(
        deps,
        row=row,
        advice=None,
        market_phase=row.market_phase,
        open_symbols=set(),
        traded_today=set(),
    )

    decision = LegacyMomentumSetup().evaluate(ctx)

    assert decision.action == ACTION_ENTER
    assert decision.size_fraction == pytest.approx(
        entry_rules.entry_fraction(deps, row=row, entry_tag=expected_tag)
    )
    assert decision.stop_price == pytest.approx(row.ask_price * 0.95)
    assert decision.metadata["entry_tag"] == (expected_tag or "")


def test_legacy_momentum_evaluate_add_matches_existing_can_add(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket="predictor_weighted")
    position = _open_position()
    row = _activity(phase="regular", label="CONDITIONAL_ENTRY", score=4.8, price=1.04)
    ctx = _ctx(
        deps=deps,
        row=row,
        bucket="predictor_weighted",
        position=position,
        bar_at=REGULAR_NOW,
    )

    assert entry_rules.can_add(
        deps,
        position,
        row,
        None,
        market_phase=row.market_phase,
        now=REGULAR_NOW,
    ) is True
    decision = LegacyMomentumSetup().evaluate(ctx)

    assert decision.action == ACTION_ADD
    assert decision.size_fraction == pytest.approx(settings.paper_add_size_fraction)
    assert decision.stop_price == pytest.approx(exit_rules.stop_price(settings, "adaptive", position))


def test_legacy_momentum_entry_wrapper_delegates_to_existing_apply_entry_rules(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    deps = _entry_deps(settings, bucket="predictor_weighted")
    run_a = PaperTradingRun(
        run_id="run-a",
        strategy_name="auto_paper_v1",
        initial_capital=10_000.0,
        cash_balance=10_000.0,
        equity=10_000.0,
        equity_peak=10_000.0,
    )
    run_b = run_a.model_copy(deep=True, update={"run_id": "run-b"})
    positions_a: list[PaperPosition] = []
    positions_b: list[PaperPosition] = []
    row = _activity(bucket="predictor_weighted")

    direct = entry_rules.apply_entry_rules(
        deps,
        run=run_a,
        positions=positions_a,
        activity=[row],
        advice_by_symbol={},
        market_phase="premarket",
        market_date="2026-03-26",
        day_regime="normal",
        now=NOW,
    )
    wrapped = LegacyMomentumSetup().apply_entry_rules(
        deps,
        run=run_b,
        positions=positions_b,
        activity=[row],
        advice_by_symbol={},
        market_phase="premarket",
        market_date="2026-03-26",
        day_regime="normal",
        now=NOW,
    )

    assert [order.intent for order in wrapped] == [order.intent for order in direct] == ["ENTRY"]
    assert [position.symbol for position in positions_b] == [position.symbol for position in positions_a]


def test_legacy_momentum_exit_and_profit_wrappers_delegate_to_existing_rules(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    exit_deps = _exit_deps(settings)
    profit_deps = _profit_deps(settings)
    run_direct = PaperTradingRun(
        run_id="run-direct",
        strategy_name="auto_paper_v1",
        initial_capital=10_000.0,
        cash_balance=9_900.0,
        equity=10_020.0,
        equity_peak=10_020.0,
    )
    run_wrapped = run_direct.model_copy(deep=True, update={"run_id": "run-wrapped"})
    position_direct = _open_position(run_id=run_direct.run_id, last_price=1.13)
    position_wrapped = _open_position(run_id=run_wrapped.run_id, last_price=1.13)
    row = _activity(phase="regular", price=1.13, label="CONDITIONAL_ENTRY", score=5.0)
    activity_by_symbol = {"TEST": row}

    direct_trim = profit_rules.apply_profit_management(
        profit_deps,
        run=run_direct,
        positions=[position_direct],
        activity_by_symbol=activity_by_symbol,
        market_phase="regular",
        day_regime="normal",
        now=NOW,
    )
    wrapped_trim = LegacyMomentumSetup().apply_profit_management(
        profit_deps,
        run=run_wrapped,
        positions=[position_wrapped],
        activity_by_symbol=activity_by_symbol,
        market_phase="regular",
        day_regime="normal",
        now=NOW,
    )

    assert [order.intent for order in wrapped_trim] == [order.intent for order in direct_trim] == ["TRIM"]

    stop_row = _activity(phase="regular", price=0.90, label="NO_CHASE", score=1.0)
    position_direct.last_price = 0.90
    position_wrapped.last_price = 0.90
    direct_exit = exit_rules.apply_exit_rules(
        exit_deps,
        run=run_direct,
        positions=[position_direct],
        activity_by_symbol={"TEST": stop_row},
        market_phase="regular",
        day_regime="normal",
        now=NOW,
    )
    wrapped_exit = LegacyMomentumSetup().apply_exit_rules(
        exit_deps,
        run=run_wrapped,
        positions=[position_wrapped],
        activity_by_symbol={"TEST": stop_row},
        market_phase="regular",
        day_regime="normal",
        now=NOW,
    )

    assert [order.intent for order in wrapped_exit] == [order.intent for order in direct_exit] == ["EXIT"]
