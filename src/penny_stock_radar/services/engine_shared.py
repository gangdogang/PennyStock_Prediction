from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from ..db import (
    fetch_paper_positions,
    insert_paper_orders,
    insert_paper_run_snapshot,
    upsert_paper_positions,
    upsert_paper_trading_run,
)
from ..models import MarketActivity, PaperOrder, PaperPosition, PaperRunSnapshot, PaperTradingRun
from .paper_runtime import PaperTradingStepResult


@dataclass(frozen=True, slots=True)
class TradingEngineSpec:
    engine_id: str
    strategy_name: str
    strategy_mode: str
    holding_period: str
    description: str
    allows_overnight: bool = False


class TradingEngine(Protocol):
    strategy_name: str
    strategy_mode: str
    export_dir: Path

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> object: ...

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> object: ...

    def export_run_csv(self, run_id: str | None = None) -> Path: ...

    def active_position_symbols(self) -> tuple[str, ...]: ...


def engine_active_symbols(engine: object) -> tuple[str, ...]:
    getter = getattr(engine, "active_position_symbols", None)
    if callable(getter):
        return tuple(str(symbol).upper() for symbol in getter() if symbol)
    private_getter = getattr(engine, "_active_position_symbols", None)
    if callable(private_getter):
        return tuple(str(symbol).upper() for symbol in private_getter() if symbol)
    return ()


def collect_active_symbols(engines: Iterable[object]) -> tuple[str, ...]:
    symbols: set[str] = set()
    for engine in engines:
        symbols.update(engine_active_symbols(engine))
    return tuple(sorted(symbols))


@dataclass(slots=True)
class StepContext:
    engine: Any
    market_phase: str
    activity: list[MarketActivity]
    export_csv: bool
    now: datetime
    market_date: str
    run: PaperTradingRun
    positions: list[PaperPosition]
    activity_by_symbol: dict[str, MarketActivity]
    day_regime: str
    advice_by_symbol: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)
    entered_count: int = 0
    added_count: int = 0
    exited_count: int = 0
    orders: list[PaperOrder] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StepHookResult:
    orders: tuple[PaperOrder, ...] = ()
    entered_count: int = 0
    added_count: int = 0
    exited_count: int = 0
    actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyHook:
    callback: Callable[[StepContext], StepHookResult]


@dataclass(frozen=True, slots=True)
class StrategyRules:
    prepare: StrategyHook | None = None
    apply_exit_rules: StrategyHook | None = None
    apply_profit_management: StrategyHook | None = None
    apply_add_rules: StrategyHook | None = None
    apply_entry_rules: StrategyHook | None = None
    apply_session_close: StrategyHook | None = None


def _apply_hook_result(context: StepContext, result: StepHookResult) -> None:
    if result.actions:
        context.actions.extend(result.actions)
    if result.orders:
        context.orders.extend(result.orders)
    context.entered_count += result.entered_count
    context.added_count += result.added_count
    context.exited_count += result.exited_count


def run_step(
    engine: Any,
    *,
    market_phase: str,
    activity: list[MarketActivity],
    strategy_rules: StrategyRules,
    export_csv: bool = True,
) -> PaperTradingStepResult:
    now = engine.now_fn()
    market_date = engine._market_date(now)
    run = engine._get_or_create_run()
    positions = fetch_paper_positions(engine.settings.database_path, run.run_id)
    activity_by_symbol = {
        row.symbol.upper(): row
        for row in activity
        if row.symbol and row.last_price is not None and row.last_price > 0
    }
    day_regime = (
        engine._classify_day_regime(activity)
        if market_phase in {"premarket", "regular"} and hasattr(engine, "_classify_day_regime")
        else "closed"
    )
    context = StepContext(
        engine=engine,
        market_phase=market_phase,
        activity=activity,
        export_csv=export_csv,
        now=now,
        market_date=market_date,
        run=run,
        positions=positions,
        activity_by_symbol=activity_by_symbol,
        day_regime=day_regime,
    )

    if strategy_rules.prepare is not None:
        _apply_hook_result(context, strategy_rules.prepare.callback(context))

    engine._mark_positions(
        context.positions,
        context.activity_by_symbol,
        context.now,
        day_regime=context.day_regime,
    )
    engine._latch_daily_loss_lock(
        run=context.run,
        positions=context.positions,
        market_date=context.market_date,
    )

    if market_phase in {"premarket", "regular"}:
        advice_bundle = engine._review_candidates(
            activity=activity,
            positions=context.positions,
            market_phase=market_phase,
        )
        if advice_bundle is not None:
            context.advice_by_symbol = advice_bundle.items
            if advice_bundle.notes:
                context.context_notes.extend(advice_bundle.notes)
                context.actions.append("gemini_consensus")

        if strategy_rules.apply_exit_rules is not None:
            _apply_hook_result(context, strategy_rules.apply_exit_rules.callback(context))
        if strategy_rules.apply_profit_management is not None:
            _apply_hook_result(context, strategy_rules.apply_profit_management.callback(context))
        if strategy_rules.apply_add_rules is not None:
            _apply_hook_result(context, strategy_rules.apply_add_rules.callback(context))

        engine._latch_daily_loss_lock(
            run=context.run,
            positions=context.positions,
            market_date=context.market_date,
        )
        if strategy_rules.apply_entry_rules is not None:
            _apply_hook_result(context, strategy_rules.apply_entry_rules.callback(context))
    elif strategy_rules.apply_session_close is not None:
        _apply_hook_result(context, strategy_rules.apply_session_close.callback(context))

    engine._refresh_position_marks(context.positions)
    step_notes = engine._step_notes(
        actions=context.actions,
        activity=activity,
        context_notes=context.context_notes,
        advice_by_symbol=context.advice_by_symbol,
    )
    engine._sync_run_metrics(
        run=context.run,
        positions=context.positions,
        market_phase=market_phase,
        market_date=context.market_date,
        now=context.now,
        notes=step_notes,
    )

    upsert_paper_trading_run(engine.settings.database_path, context.run)
    upsert_paper_positions(engine.settings.database_path, context.positions)
    if context.orders:
        insert_paper_orders(engine.settings.database_path, context.orders)

    snapshot = PaperRunSnapshot(
        run_id=context.run.run_id,
        market_phase=market_phase,
        cash_balance=context.run.cash_balance,
        equity=context.run.equity,
        realized_pnl=context.run.realized_pnl,
        unrealized_pnl=context.run.unrealized_pnl,
        total_return_pct=context.run.total_return_pct,
        max_drawdown_pct=context.run.max_drawdown_pct,
        open_position_count=sum(1 for row in context.positions if row.status == "OPEN"),
        closed_trade_count=context.run.closed_trade_count,
        win_rate=context.run.win_rate,
        profit_factor=context.run.profit_factor,
        reward_risk_ratio=context.run.reward_risk_ratio,
        notes=step_notes,
        created_at=context.now,
    )
    insert_paper_run_snapshot(engine.settings.database_path, snapshot)

    if export_csv:
        engine.export_run_csv(context.run.run_id)

    return PaperTradingStepResult(
        run_id=context.run.run_id,
        strategy_name=context.run.strategy_name,
        bucket=context.run.bucket,
        market_phase=market_phase,
        actions=context.actions or ["hold"],
        entered_count=context.entered_count,
        added_count=context.added_count,
        exited_count=context.exited_count,
        open_position_count=sum(1 for row in context.positions if row.status == "OPEN"),
        closed_trade_count=context.run.closed_trade_count,
        cash_balance=context.run.cash_balance,
        equity=context.run.equity,
        realized_pnl=context.run.realized_pnl,
        unrealized_pnl=context.run.unrealized_pnl,
        profit_factor=context.run.profit_factor,
        reward_risk_ratio=context.run.reward_risk_ratio,
        export_dir=engine.export_dir.resolve(),
    )
