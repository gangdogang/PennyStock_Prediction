from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable
import uuid

from ..config import AppSettings
from ..db import (
    fetch_latest_premkt_predictions,
)
from ..models import MarketActivity, PaperOrder, PaperPosition
from .engine_shared import StepContext, StepHookResult, StrategyHook, StrategyRules, TradingEngineSpec, run_step
from .momentum_advisor import MomentumAdvice
from .paper_trading import PaperTradingEngine, PaperTradingStepResult
from .trading_support import classify_day_regime, current_open_risk, market_status_blocks_trading

MULTIDAY_PAPER_STRATEGY = "multiday_momentum_v1"

MULTIDAY_ENGINE_SPEC = TradingEngineSpec(
    engine_id="multiday",
    strategy_name=MULTIDAY_PAPER_STRATEGY,
    strategy_mode="adaptive",
    holding_period="multi_day",
    description="Multiday winner-pyramiding paper trading engine.",
    allows_overnight=True,
)

MULTIDAY_ALLOWED_LABELS = {
    "OPENING_RANGE_CANDIDATE",
    "CONDITIONAL_ENTRY",
    "WAIT_PULLBACK",
    "NEWS_CHECK_FIRST",
}


@dataclass(frozen=True, slots=True)
class MultidayPositionPlan:
    symbol: str
    thesis: str
    starter_risk_pct: float
    max_add_count: int
    overnight_hold_score: float


class MultidayTradingEngine(PaperTradingEngine):
    def __init__(
        self,
        settings: AppSettings,
        *,
        export_dir: Path | None = None,
        advisor: object | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            settings,
            strategy_name=MULTIDAY_PAPER_STRATEGY,
            export_dir=export_dir,
            advisor=advisor,
            now_fn=now_fn,
            strategy_mode="adaptive",
        )
        self.spec = MULTIDAY_ENGINE_SPEC

    def load_prediction_reference(
        self,
        *,
        scan_id: str | None = None,
    ) -> dict[str, float]:
        rows = fetch_latest_premkt_predictions(
            self.settings.database_path,
            scan_id=scan_id,
            prefer_reportable=False,
        )
        return {
            str(row["symbol"]).upper(): float(row["score"])
            for row in rows
        }

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        return run_step(
            self,
            market_phase=market_phase,
            activity=activity,
            strategy_rules=self._strategy_rules(),
            export_csv=export_csv,
        )

    def _strategy_rules(self) -> StrategyRules:
        return StrategyRules(
            apply_exit_rules=StrategyHook(callback=self._run_multiday_exit_rules),
            apply_add_rules=StrategyHook(callback=self._run_multiday_add_rules),
            apply_entry_rules=StrategyHook(callback=self._run_multiday_entry_rules),
            apply_session_close=StrategyHook(callback=self._run_multiday_session_close),
        )

    def _run_multiday_exit_rules(self, context: StepContext) -> StepHookResult:
        orders = self._apply_multiday_exit_rules(
            run=context.run,
            positions=context.positions,
            activity_by_symbol=context.activity_by_symbol,
            market_phase=context.market_phase,
            day_regime=context.day_regime,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            exited_count=sum(1 for order in orders if order.intent == "EXIT"),
            actions=tuple(f"exit:{order.symbol}" for order in orders),
        )

    def _run_multiday_add_rules(self, context: StepContext) -> StepHookResult:
        orders = self._apply_multiday_winner_adds(
            run=context.run,
            positions=context.positions,
            activity=context.activity,
            advice_by_symbol=context.advice_by_symbol,
            market_phase=context.market_phase,
            day_regime=context.day_regime,
            market_date=context.market_date,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            added_count=sum(1 for order in orders if order.intent == "ADD"),
            actions=tuple(f"add:{order.symbol}" for order in orders),
        )

    def _run_multiday_entry_rules(self, context: StepContext) -> StepHookResult:
        orders, replacement_count = self._apply_multiday_entries(
            run=context.run,
            positions=context.positions,
            activity=context.activity,
            advice_by_symbol=context.advice_by_symbol,
            market_phase=context.market_phase,
            market_date=context.market_date,
            day_regime=context.day_regime,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            entered_count=sum(1 for order in orders if order.intent == "ENTRY"),
            exited_count=replacement_count,
            actions=tuple(f"{order.intent.lower()}:{order.symbol}" for order in orders),
        )

    def _run_multiday_session_close(self, context: StepContext) -> StepHookResult:
        orders = self._apply_overnight_hold(
            run=context.run,
            positions=context.positions,
            market_phase=context.market_phase,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            exited_count=sum(1 for order in orders if order.intent == "EXIT"),
            actions=tuple(f"overnight_exit:{order.symbol}" for order in orders),
        )

    def _classify_day_regime(self, activity: list[MarketActivity]) -> str:
        return (
            classify_day_regime(self.settings, activity)
            if activity
            else "closed"
        )

    def _apply_multiday_entries(
        self,
        *,
        run,
        positions: list[PaperPosition],
        activity: list[MarketActivity],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        market_date: str,
        day_regime: str,
        now: datetime,
    ) -> tuple[list[PaperOrder], int]:
        orders: list[PaperOrder] = []
        open_positions = [row for row in positions if row.status == "OPEN"]
        open_symbols = {row.symbol for row in open_positions}
        traded_today = {
            row.symbol
            for row in positions
            if self._market_date(row.opened_at) == market_date
        }
        equity_basis = self._equity_basis(run=run, positions=positions)
        if self._daily_loss_locked(
            run=run,
            positions=positions,
            market_date=market_date,
            equity_basis=equity_basis,
            now=now,
        ):
            return [], 0

        candidates = self._starter_candidates(
            activity=activity,
            advice_by_symbol=advice_by_symbol,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )
        if not candidates:
            return [], 0

        replacement_count = 0
        max_positions = self._max_open_positions()
        slots = max_positions - len(open_positions)
        if slots > 0:
            tracked_open_risk = current_open_risk(open_positions)
            risk_cap = equity_basis * self.settings.trade_plan_max_concurrent_open_risk_pct / 100.0
            for row, score in candidates[:slots]:
                order = self._enter_starter(
                    run=run,
                    positions=positions,
                    row=row,
                    market_phase=market_phase,
                    day_regime=day_regime,
                    now=now,
                    advice=advice_by_symbol.get(row.symbol),
                    starter_score=score,
                    tracked_open_risk=tracked_open_risk,
                    risk_cap=risk_cap,
                )
                if order is None:
                    continue
                orders.append(order)
                tracked_open_risk = current_open_risk([row for row in positions if row.status == "OPEN"])
            return orders, 0

        replacement = self._best_replacement(
            run=run,
            positions=positions,
            candidates=candidates,
            advice_by_symbol=advice_by_symbol,
            market_phase=market_phase,
            market_date=market_date,
            day_regime=day_regime,
            now=now,
        )
        if replacement is None:
            return [], 0

        loser, candidate, candidate_score = replacement
        row = self._activity_for_symbol(activity, loser.symbol)
        exit_reasons = [
            f"replacement_edge:{candidate_score - self._replacement_position_score(loser, row, now):.1f}",
            f"replacement_target:{candidate.symbol}",
        ]
        orders.append(
            self._close_position(
                run=run,
                position=loser,
                row=row,
                market_phase=market_phase,
                reason="loser_replacement",
                day_regime=day_regime,
                now=now,
                analysis_label=row.analysis_label if row is not None else "REPLACEMENT",
                analysis_score=row.analysis_score if row is not None else None,
                reasons=exit_reasons,
            )
        )
        replacement_count = 1
        tracked_open_risk = current_open_risk([row for row in positions if row.status == "OPEN"])
        risk_cap = equity_basis * self.settings.trade_plan_max_concurrent_open_risk_pct / 100.0
        entry_order = self._enter_starter(
            run=run,
            positions=positions,
            row=candidate,
            market_phase=market_phase,
            day_regime=day_regime,
            now=now,
            advice=advice_by_symbol.get(candidate.symbol),
            starter_score=candidate_score,
            tracked_open_risk=tracked_open_risk,
            risk_cap=risk_cap,
        )
        if entry_order is not None:
            orders.append(entry_order)
        return orders, replacement_count

    def _apply_multiday_winner_adds(
        self,
        *,
        run,
        positions: list[PaperPosition],
        activity: list[MarketActivity],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        day_regime: str,
        market_date: str,
        now: datetime,
    ) -> list[PaperOrder]:
        equity_basis = self._equity_basis(run=run, positions=positions)
        if self._daily_loss_locked(
            run=run,
            positions=positions,
            market_date=market_date,
            equity_basis=equity_basis,
            now=now,
        ):
            return []

        orders: list[PaperOrder] = []
        tracked_open_risk = current_open_risk([row for row in positions if row.status == "OPEN"])
        risk_cap = equity_basis * self.settings.trade_plan_max_concurrent_open_risk_pct / 100.0
        for position in [row for row in positions if row.status == "OPEN"]:
            row = self._activity_for_symbol(activity, position.symbol)
            advice = advice_by_symbol.get(position.symbol)
            if not self._can_multiday_add(
                position=position,
                row=row,
                advice=advice,
                market_phase=market_phase,
                now=now,
            ):
                continue
            reference_price = row.ask_price if row.ask_price is not None and row.ask_price > 0 else row.last_price
            requested_quantity = self._position_size(
                cash_balance=run.cash_balance,
                equity=run.equity,
                price=reference_price,
                fraction=self.settings.multiday_winner_add_fraction,
            )
            fill = self.fill_model.buy(
                row,
                market_phase=market_phase,
                requested_quantity=requested_quantity,
            )
            quantity = fill.quantity
            remaining_quantity = fill.remaining_quantity
            fill_status = fill.fill_status
            fill_price = fill.fill_price
            fill_reference_price = fill.fill_reference_price
            fill_slippage_pct = fill.fill_slippage_pct
            transaction_cost = fill.transaction_cost
            if quantity <= 0 or fill_price is None:
                continue
            existing_risk = current_open_risk([position])
            candidate_position = position.model_copy(deep=True)
            candidate_position.quantity += quantity
            candidate_position.cost_basis += fill_price * quantity + transaction_cost
            candidate_position.average_entry_price = candidate_position.cost_basis / max(candidate_position.quantity, 1)
            candidate_position.last_price = row.last_price
            candidate_position.add_count += 1
            candidate_position.stop_price = self._stop_price(candidate_position)
            candidate_position.planned_stop_price = candidate_position.stop_price
            incremental_risk = max(current_open_risk([candidate_position]) - existing_risk, 0.0)
            if tracked_open_risk + incremental_risk > risk_cap:
                continue

            notional = fill_price * quantity
            run.cash_balance -= notional + transaction_cost
            position.quantity += quantity
            position.cost_basis += notional + transaction_cost
            position.average_entry_price = position.cost_basis / max(position.quantity, 1)
            position.fees_paid_total += transaction_cost
            position.last_price = row.last_price
            position.highest_price = max(position.highest_price or row.last_price or fill_price, row.last_price or fill_price)
            position.add_count += 1
            position.strategy_bucket = f"add_{position.add_count}"
            position.updated_at = now
            position.entry_reasons = position.entry_reasons + [
                "winner_add",
                f"add_stage:add_{position.add_count}",
                f"day_survival:{self._day_survival_score(position, row, now):.1f}",
            ] + self._advice_reasons(advice)
            self._store_position_market_status(position, row.market_status)
            position.stop_price = self._stop_price(position)
            position.planned_stop_price = position.stop_price
            position.planned_risk_pct = self._planned_risk_pct(
                position.average_entry_price,
                position.planned_stop_price,
            )
            position.unrealized_pnl = (row.last_price - position.average_entry_price) * position.quantity
            position.market_value = row.last_price * position.quantity
            position.total_pnl = position.realized_pnl + position.unrealized_pnl
            orders.append(
                PaperOrder(
                    order_id=str(uuid.uuid4()),
                    run_id=run.run_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    market_phase=market_phase,
                    action="BUY",
                    intent="ADD",
                    quantity=quantity,
                    requested_quantity=requested_quantity,
                    remaining_quantity=remaining_quantity,
                    fill_status=fill_status,
                    price=fill_price,
                    notional=notional,
                    transaction_cost=transaction_cost,
                    strategy_bucket=position.strategy_bucket,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    planned_stop_price=position.planned_stop_price,
                    planned_risk_pct=position.planned_risk_pct,
                    fill_reference_price=fill_reference_price,
                    fill_slippage_pct=fill_slippage_pct,
                    bar_volume=fill.bar_volume,
                    bar_dollar_volume=fill.bar_dollar_volume,
                    shares_pct_of_bar_volume=fill.shares_pct_of_bar_volume,
                    notional_pct_of_bar_dollar_volume=fill.notional_pct_of_bar_dollar_volume,
                    estimated_capacity_at_1pct_volume=fill.estimated_capacity_at_1pct_volume,
                    estimated_capacity_at_2pct_volume=fill.estimated_capacity_at_2pct_volume,
                    capacity_limited=fill.capacity_limited,
                    participation_slippage_pct=fill.participation_slippage_pct,
                    day_regime=day_regime,
                    watchlist_rank_at_entry=position.watchlist_rank_at_entry,
                    reasons=(
                        ["winner_add", f"day_survival:{self._day_survival_score(position, row, now):.1f}"]
                        + (["partial_fill_cap"] if remaining_quantity > 0 else [])
                        + row.reasons[:3]
                    ),
                    created_at=now,
                )
            )
            tracked_open_risk += incremental_risk
            run.total_transaction_cost += transaction_cost
        return orders

    def _apply_multiday_exit_rules(
        self,
        *,
        run,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        orders: list[PaperOrder] = []
        for position in positions:
            if position.status != "OPEN":
                continue
            row = activity_by_symbol.get(position.symbol)
            if row is None or row.last_price is None:
                continue
            if market_status_blocks_trading(row.market_status):
                continue

            hold_days = self._hold_days(position, now)
            base_stop = self._base_multiday_stop(position)
            trail_stop = self._multiday_trail_stop(position)
            stop_price = self._stop_price(position)
            position.stop_price = stop_price
            gain_pct = self._position_gain_pct(position, row.last_price)
            day_survival = self._day_survival_score(position, row, now)

            reason: str | None = None
            if trail_stop is not None and row.last_price <= trail_stop:
                reason = "trail_exit"
            elif row.last_price <= base_stop:
                reason = "hard_stop"
            elif (
                hold_days == 2
                and market_phase in {"premarket", "regular"}
                and row.previous_close is not None
                and row.last_price < row.previous_close * 0.92
                and gain_pct < 0
            ):
                reason = "day2_gap_fail"
            elif (
                hold_days == 2
                and market_phase == "regular"
                and day_survival < self.settings.multiday_day2_survival_threshold
                and gain_pct < 2.0
            ):
                reason = "day2_continuation_failure"
            elif (
                hold_days >= 3
                and market_phase == "regular"
                and (
                    row.analysis_label == "NO_CHASE"
                    or row.trap_score >= 55.0
                    or day_survival < 40.0
                )
            ):
                reason = "day3_exhaustion"

            if reason is None:
                continue
            orders.append(
                self._close_position(
                    run=run,
                    position=position,
                    row=row,
                    market_phase=market_phase,
                    reason=reason,
                    day_regime=day_regime,
                    now=now,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    reasons=[
                        f"hold_days:{hold_days}",
                        f"day_survival:{day_survival:.1f}",
                    ] + row.reasons[:2],
                )
            )
        return orders

    def _apply_overnight_hold(
        self,
        *,
        run,
        positions: list[PaperPosition],
        market_phase: str,
        now: datetime,
    ) -> list[PaperOrder]:
        orders: list[PaperOrder] = []
        for position in positions:
            if position.status != "OPEN":
                continue
            score = self._overnight_hold_score(position, None, now)
            marker = f"overnight_hold:{self._market_date(now)}:{score:.1f}"
            if score >= self.settings.multiday_hold_score_threshold:
                if marker not in position.entry_reasons:
                    position.entry_reasons.append(marker)
                position.updated_at = now
                continue
            orders.append(
                self._close_position(
                    run=run,
                    position=position,
                    row=None,
                    market_phase=market_phase,
                    reason="overnight_hold_rejected",
                    day_regime=position.day_regime or "closed",
                    now=now,
                    analysis_label="OVERNIGHT_REJECTED",
                    analysis_score=None,
                    reasons=[
                        f"overnight_hold_score:{score:.1f}",
                        f"hold_days:{self._hold_days(position, now)}",
                    ],
                )
            )
        return orders

    def _starter_candidates(
        self,
        *,
        activity: list[MarketActivity],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> list[tuple[MarketActivity, float]]:
        candidates: list[tuple[MarketActivity, float]] = []
        for row in activity:
            advice = advice_by_symbol.get(row.symbol)
            score = self._starter_score(row, advice)
            if not self._eligible_for_multiday_starter(
                row=row,
                advice=advice,
                starter_score=score,
                market_phase=market_phase,
                open_symbols=open_symbols,
                traded_today=traded_today,
            ):
                continue
            candidates.append((row, score))
        return sorted(
            candidates,
            key=lambda item: (
                -item[1],
                self._best_rank(item[0]),
                -(item[0].dollar_volume or 0.0),
                item[0].symbol,
            ),
        )

    def _eligible_for_multiday_starter(
        self,
        *,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        starter_score: float,
        market_phase: str,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        if market_phase not in {"premarket", "regular"}:
            return False
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        if row.analysis_label not in MULTIDAY_ALLOWED_LABELS:
            return False
        if not self._has_tradeable_live_state(row, market_phase=market_phase):
            return False
        if advice is not None and advice.stance == "avoid":
            return False
        if row.trap_score > 0 and row.trap_score >= 60.0:
            return False
        if not row.predicted and self._best_rank(row) > 3 and row.analysis_score < self.settings.paper_entry_score_min + 0.5:
            return False
        return starter_score >= self.settings.multiday_hold_score_threshold

    def _enter_starter(
        self,
        *,
        run,
        positions: list[PaperPosition],
        row: MarketActivity,
        market_phase: str,
        day_regime: str,
        now: datetime,
        advice: MomentumAdvice | None,
        starter_score: float,
        tracked_open_risk: float,
        risk_cap: float,
    ) -> PaperOrder | None:
        reference_price = row.ask_price if row.ask_price is not None and row.ask_price > 0 else row.last_price
        requested_quantity = self._position_size(
            cash_balance=run.cash_balance,
            equity=run.equity,
            price=reference_price,
            fraction=self.settings.multiday_starter_entry_fraction,
        )
        fill = self.fill_model.buy(
            row,
            market_phase=market_phase,
            requested_quantity=requested_quantity,
        )
        quantity = fill.quantity
        remaining_quantity = fill.remaining_quantity
        fill_status = fill.fill_status
        fill_price = fill.fill_price
        fill_reference_price = fill.fill_reference_price
        fill_slippage_pct = fill.fill_slippage_pct
        transaction_cost = fill.transaction_cost
        if quantity <= 0 or fill_price is None:
            return None
        notional = fill_price * quantity
        planned_stop_price = fill_price * (1 - self.settings.paper_stop_loss_pct / 100.0)
        order_risk = quantity * max(fill_price - planned_stop_price, 0.0)
        if tracked_open_risk + order_risk > risk_cap:
            return None

        position = PaperPosition(
            position_id=str(uuid.uuid4()),
            run_id=run.run_id,
            symbol=row.symbol,
            status="OPEN",
            entry_phase=market_phase,
            entry_label=row.analysis_label,
            quantity=quantity,
            average_entry_price=(notional + transaction_cost) / max(quantity, 1),
            last_price=row.last_price,
            cost_basis=notional + transaction_cost,
            market_value=notional,
            stop_price=planned_stop_price,
            planned_stop_price=planned_stop_price,
            planned_risk_pct=self._planned_risk_pct(fill_price, planned_stop_price),
            highest_price=row.last_price or fill_price,
            strategy_bucket="starter",
            fill_reference_price=fill_reference_price,
            fill_slippage_pct=fill_slippage_pct,
            fees_paid_total=transaction_cost,
            day_regime=day_regime,
            watchlist_rank_at_entry=row.watchlist_rank,
            entry_reasons=self._multiday_entry_reasons(row, advice, starter_score, now)
            + (["partial_fill_cap"] if remaining_quantity > 0 else []),
            opened_at=now,
            updated_at=now,
        )
        self._store_position_market_status(position, row.market_status)
        positions.append(position)
        run.cash_balance -= notional + transaction_cost
        run.total_transaction_cost += transaction_cost
        return PaperOrder(
            order_id=str(uuid.uuid4()),
            run_id=run.run_id,
            position_id=position.position_id,
            symbol=position.symbol,
            market_phase=market_phase,
            action="BUY",
            intent="ENTRY",
            quantity=quantity,
            requested_quantity=requested_quantity,
            remaining_quantity=remaining_quantity,
            fill_status=fill_status,
            price=fill_price,
            notional=notional,
            transaction_cost=transaction_cost,
            strategy_bucket="starter",
            analysis_label=row.analysis_label,
            analysis_score=row.analysis_score,
            planned_stop_price=position.planned_stop_price,
            planned_risk_pct=position.planned_risk_pct,
            fill_reference_price=fill_reference_price,
            fill_slippage_pct=fill_slippage_pct,
            bar_volume=fill.bar_volume,
            bar_dollar_volume=fill.bar_dollar_volume,
            shares_pct_of_bar_volume=fill.shares_pct_of_bar_volume,
            notional_pct_of_bar_dollar_volume=fill.notional_pct_of_bar_dollar_volume,
            estimated_capacity_at_1pct_volume=fill.estimated_capacity_at_1pct_volume,
            estimated_capacity_at_2pct_volume=fill.estimated_capacity_at_2pct_volume,
            capacity_limited=fill.capacity_limited,
            participation_slippage_pct=fill.participation_slippage_pct,
            day_regime=day_regime,
            watchlist_rank_at_entry=row.watchlist_rank,
            reasons=position.entry_reasons[:6],
            created_at=now,
        )

    def _best_replacement(
        self,
        *,
        run,
        positions: list[PaperPosition],
        candidates: list[tuple[MarketActivity, float]],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        market_date: str,
        day_regime: str,
        now: datetime,
    ) -> tuple[PaperPosition, MarketActivity, float] | None:
        if self._replacement_done_today(positions, market_date):
            return None
        open_positions = [row for row in positions if row.status == "OPEN"]
        if not open_positions:
            return None

        weakest = min(
            open_positions,
            key=lambda position: self._replacement_position_score(position, None, now),
        )
        weakest_score = self._replacement_position_score(weakest, None, now)
        candidate, candidate_score = candidates[0]
        if candidate.symbol == weakest.symbol:
            return None
        if candidate_score < weakest_score + self.settings.multiday_replacement_min_edge:
            return None
        if weakest.total_pnl > 0 and weakest_score >= self.settings.multiday_day2_survival_threshold:
            return None
        return weakest, candidate, candidate_score

    def _replacement_done_today(
        self,
        positions: list[PaperPosition],
        market_date: str,
    ) -> bool:
        return any(
            row.exit_reason == "loser_replacement"
            and row.closed_at is not None
            and self._market_date(row.closed_at) == market_date
            for row in positions
        )

    def _multiday_entry_reasons(
        self,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        starter_score: float,
        now: datetime,
    ) -> list[str]:
        reasons = [
            "multiday_starter",
            "starter_stage:starter",
            f"starter_score:{starter_score:.1f}",
            f"opened_market_date:{self._market_date(now)}",
        ]
        reasons.extend(row.reasons[:4])
        reasons.extend(self._advice_reasons(advice))
        deduped: list[str] = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return deduped

    def _starter_score(
        self,
        row: MarketActivity,
        advice: MomentumAdvice | None,
    ) -> float:
        score = row.analysis_score * 10.0
        if row.predicted:
            score += 12.0
        best_rank = self._best_rank(row)
        if best_rank <= 3:
            score += 12.0
        elif best_rank <= 5:
            score += 6.0
        if row.leader_persistence_score >= 60.0:
            score += 12.0
        elif row.leader_persistence_score > 0:
            score -= 12.0
        if row.pullback_absorption_score >= 60.0:
            score += 8.0
        elif row.pullback_absorption_score > 0 and row.pullback_absorption_score < 40.0:
            score -= 8.0
        if row.behavioral_score >= 60.0:
            score += 6.0
        elif row.behavioral_score > 0 and row.behavioral_score < 40.0:
            score -= 6.0
        if row.dollar_volume is not None:
            if row.dollar_volume >= self.settings.premarket_min_dollar_volume * 3:
                score += 8.0
            elif row.dollar_volume < self.settings.premarket_min_dollar_volume * 1.5:
                score -= 10.0
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            score -= 20.0
        if row.trap_score > 0 and row.trap_score >= 50.0:
            score -= 18.0
        if advice is not None:
            if advice.stance == "buy" and advice.conviction >= 0.60:
                score += 8.0
            elif advice.stance == "avoid":
                score -= 20.0
        return max(0.0, min(score, 100.0))

    def _overnight_hold_score(
        self,
        position: PaperPosition,
        row: MarketActivity | None,
        now: datetime,
    ) -> float:
        score = 50.0
        gain_pct = self._position_gain_pct(position, position.last_price)
        if gain_pct >= 5.0:
            score += 15.0
        elif gain_pct >= 0:
            score += 8.0
        else:
            score -= 18.0
        if position.add_count > 0:
            score += 5.0
        if self._hold_days(position, now) >= 2 and gain_pct > 0:
            score += 5.0
        if row is not None:
            if row.leader_persistence_score >= 60.0:
                score += 15.0
            elif row.leader_persistence_score > 0 and row.leader_persistence_score < 40.0:
                score -= 10.0
            if row.pullback_absorption_score >= 60.0:
                score += 10.0
            elif row.pullback_absorption_score > 0 and row.pullback_absorption_score < 40.0:
                score -= 8.0
            if row.trap_score >= 50.0:
                score -= 15.0
            if row.analysis_label == "NO_CHASE":
                score -= 12.0
            if market_status_blocks_trading(row.market_status):
                score -= 20.0
        return max(0.0, min(score, 100.0))

    def _day_survival_score(
        self,
        position: PaperPosition,
        row: MarketActivity | None,
        now: datetime,
    ) -> float:
        score = self._overnight_hold_score(position, row, now)
        gain_pct = self._position_gain_pct(position, position.last_price)
        hold_days = self._hold_days(position, now)
        if hold_days == 2 and gain_pct > 0:
            score += 5.0
        if hold_days >= 3 and gain_pct > 0:
            score += 8.0
        if row is not None:
            if row.behavioral_score >= 65.0:
                score += 8.0
            if row.analysis_label == "NO_CHASE":
                score -= 15.0
            if row.trap_score >= 55.0:
                score -= 10.0
        return max(0.0, min(score, 100.0))

    def _replacement_position_score(
        self,
        position: PaperPosition,
        row: MarketActivity | None,
        now: datetime,
    ) -> float:
        score = self._day_survival_score(position, row, now)
        if position.total_pnl <= 0:
            score -= 10.0
        return max(0.0, min(score, 100.0))

    def _can_multiday_add(
        self,
        *,
        position: PaperPosition,
        row: MarketActivity | None,
        advice: MomentumAdvice | None,
        market_phase: str,
        now: datetime,
    ) -> bool:
        if position.add_count >= self.settings.multiday_max_adds:
            return False
        if row is None or row.last_price is None or row.last_price <= 0:
            return False
        if market_phase not in {"premarket", "regular"}:
            return False
        if self._hold_days(position, now) < 2:
            return False
        if not self._has_tradeable_live_state(row, market_phase=market_phase):
            return False
        if row.last_price <= position.average_entry_price:
            return False
        if advice is not None and advice.stance == "avoid":
            return False
        if row.trap_score >= 45.0:
            return False
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            return False
        if self._position_gain_pct(position, row.last_price) < self.settings.paper_add_trigger_pct:
            return False
        if self._day_survival_score(position, row, now) < 60.0:
            return False
        return (
            row.leader_persistence_score >= 60.0
            or row.pullback_absorption_score >= 65.0
            or row.behavioral_score >= 68.0
        )

    def _hold_days(self, position: PaperPosition, now: datetime) -> int:
        opened_date = date.fromisoformat(self._market_date(position.opened_at))
        current_date = date.fromisoformat(self._market_date(now))
        return max((current_date - opened_date).days + 1, 1)

    def _position_gain_pct(
        self,
        position: PaperPosition,
        last_price: float | None,
    ) -> float:
        if last_price is None or position.average_entry_price <= 0:
            return 0.0
        return ((last_price - position.average_entry_price) / position.average_entry_price) * 100.0

    def _base_multiday_stop(self, position: PaperPosition) -> float:
        base_stop = position.average_entry_price * (1 - self.settings.paper_stop_loss_pct / 100.0)
        if position.add_count > 0:
            return max(base_stop, position.average_entry_price)
        return base_stop

    def _multiday_trail_stop(self, position: PaperPosition) -> float | None:
        if position.highest_price is None:
            return None
        activation = position.average_entry_price * (1 + self.settings.paper_profit_activation_pct / 100.0)
        if position.highest_price < activation:
            return None
        return position.highest_price * (1 - self.settings.paper_trailing_stop_pct / 100.0)

    def _stop_price(self, position: PaperPosition) -> float:
        base_stop = self._base_multiday_stop(position)
        trail_stop = self._multiday_trail_stop(position)
        if trail_stop is None:
            return base_stop
        return max(base_stop, trail_stop)
