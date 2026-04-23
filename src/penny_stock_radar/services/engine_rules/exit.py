from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ...config import AppSettings
from ...models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from ..fill_model import FillModel
from ..trading_support import market_status_blocks_trading


@dataclass(frozen=True, slots=True)
class ExitRuleDeps:
    settings: AppSettings
    fill_model: FillModel
    strategy_mode: str
    position_market_status: Callable[[PaperPosition], str]
    decision_basis_price: Callable[[PaperPosition], float]
    order_id_factory: Callable[[], str]


def apply_exit_rules(
    deps: ExitRuleDeps,
    *,
    run: PaperTradingRun,
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
        if row is None or position.last_price is None:
            continue
        if market_status_blocks_trading(row.market_status):
            continue
        current_stop_price = stop_price(deps.settings, deps.strategy_mode, position)
        position.stop_price = current_stop_price
        decision_basis = deps.decision_basis_price(position)
        gain_pct = (
            ((position.last_price - decision_basis) / decision_basis) * 100.0
            if decision_basis > 0
            else 0.0
        )
        reason: str | None = None
        if position.last_price <= current_stop_price:
            if deps.strategy_mode == "adaptive":
                reason = "stop_loss"
            else:
                reason = "trailing_stop" if gain_pct > 0 else "stop_loss"
        elif deps.strategy_mode == "adaptive" and market_phase == "premarket":
            held_minutes = (now - position.opened_at).total_seconds() / 60.0
            peak_price = position.highest_price or position.last_price or decision_basis
            max_progress_pct = (
                ((peak_price - decision_basis) / decision_basis) * 100.0
                if decision_basis > 0
                else 0.0
            )
            if (
                held_minutes >= deps.settings.paper_adaptive_time_stop_minutes
                and max_progress_pct < deps.settings.paper_adaptive_time_stop_min_progress_pct
                and gain_pct < 1.0
            ):
                reason = "time_stop"
        elif deps.strategy_mode != "adaptive":
            if row.trap_score >= 78.0:
                reason = "trap_warning_exit" if gain_pct >= 0 else "momentum_failure"
            elif best_rank(row) > 5 and row.leader_persistence_score < 32.0 and gain_pct >= 1.0:
                reason = "leadership_lost"
            elif market_phase == "regular" and row.analysis_label == "NO_CHASE" and gain_pct >= 2.0:
                reason = "momentum_cooldown"
        if reason is None:
            continue
        orders.append(
            close_position(
                deps,
                run=run,
                position=position,
                row=row,
                market_phase=market_phase,
                reason=reason,
                day_regime=day_regime,
                now=now,
                analysis_label=row.analysis_label,
                analysis_score=row.analysis_score,
                reasons=row.reasons[:5],
            )
        )
    return orders


def close_for_session_end(
    deps: ExitRuleDeps,
    *,
    run: PaperTradingRun,
    positions: list[PaperPosition],
    market_phase: str,
    now: datetime,
) -> list[PaperOrder]:
    if run.last_phase not in {"premarket", "regular"}:
        return []
    orders: list[PaperOrder] = []
    for position in positions:
        if position.status != "OPEN":
            continue
        if market_status_blocks_trading(deps.position_market_status(position)):
            continue
        orders.append(
            close_position(
                deps,
                run=run,
                position=position,
                row=None,
                market_phase=market_phase,
                reason="session_closed",
                day_regime=position.day_regime or "closed",
                now=now,
                analysis_label="SESSION_END",
                analysis_score=None,
                reasons=["session_closed"],
            )
        )
    return orders


def close_position(
    deps: ExitRuleDeps,
    *,
    run: PaperTradingRun,
    position: PaperPosition,
    row: MarketActivity | None,
    market_phase: str,
    reason: str,
    day_regime: str,
    now: datetime,
    analysis_label: str | None,
    analysis_score: float | None,
    reasons: list[str],
) -> PaperOrder:
    fill = deps.fill_model.sell(
        position=position,
        row=row,
        reason=reason,
        market_phase=market_phase,
        requested_quantity=position.quantity,
    )
    fill_quantity = min(fill.quantity, position.quantity)
    remaining_quantity = fill.remaining_quantity
    price = fill.fill_price
    notional = (price or 0.0) * fill_quantity
    transaction_cost = fill.transaction_cost
    realized_pnl = ((price or 0.0) - position.average_entry_price) * fill_quantity - transaction_cost
    realized_pnl_pct = (
        ((((price or 0.0) - position.average_entry_price) / position.average_entry_price) * 100.0)
        if position.average_entry_price > 0
        else 0.0
    )
    run.cash_balance += notional - transaction_cost
    run.total_transaction_cost += transaction_cost
    position.exit_reasons = position.exit_reasons + [reason] + reasons[:2]
    position.realized_pnl += realized_pnl
    position.fees_paid_total += transaction_cost
    if fill_quantity >= position.quantity:
        position.status = "CLOSED"
        position.exit_reason = reason
        position.unrealized_pnl = 0.0
        position.total_pnl = position.realized_pnl
        position.market_value = 0.0
        position.closed_at = now
        position.quantity = fill_quantity
    else:
        position.quantity -= fill_quantity
        position.cost_basis = position.average_entry_price * position.quantity
        mark_price = row.last_price if row is not None and row.last_price is not None else price
        position.last_price = mark_price
        position.market_value = (mark_price or 0.0) * position.quantity
        position.unrealized_pnl = ((mark_price or position.average_entry_price) - position.average_entry_price) * position.quantity
        position.total_pnl = position.realized_pnl + position.unrealized_pnl
        position.partial_exit_count += 1
    position.updated_at = now
    return PaperOrder(
        order_id=deps.order_id_factory(),
        run_id=run.run_id,
        position_id=position.position_id,
        symbol=position.symbol,
        market_phase=market_phase,
        action="SELL",
        intent="EXIT" if remaining_quantity == 0 else "EXIT_PARTIAL",
        quantity=fill_quantity,
        requested_quantity=fill_quantity + remaining_quantity,
        remaining_quantity=remaining_quantity,
        fill_status=fill.fill_status,
        price=price or 0.0,
        notional=notional,
        transaction_cost=transaction_cost,
        strategy_bucket=position.strategy_bucket,
        analysis_label=analysis_label,
        analysis_score=analysis_score,
        planned_stop_price=position.planned_stop_price,
        planned_risk_pct=position.planned_risk_pct,
        fill_reference_price=fill.fill_reference_price,
        fill_slippage_pct=fill.fill_slippage_pct,
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
        reasons=[reason] + (["partial_fill_cap"] if remaining_quantity > 0 else []) + reasons[:4],
        created_at=now,
        realized_pnl=realized_pnl,
        realized_pnl_pct=realized_pnl_pct,
    )


def planned_risk_pct(
    entry_price: float | None,
    stop_price_value: float | None,
) -> float | None:
    if entry_price is None or stop_price_value is None or entry_price <= 0:
        return None
    return ((entry_price - stop_price_value) / entry_price) * 100.0


def stop_price(
    settings: AppSettings,
    strategy_mode: str,
    position: PaperPosition,
) -> float:
    base_stop = position.average_entry_price * (1 - settings.paper_stop_loss_pct / 100.0)
    if strategy_mode == "adaptive":
        if position.partial_exit_count > 0:
            return max(base_stop, position.average_entry_price)
        return base_stop
    if (
        position.highest_price is not None
        and position.highest_price
        >= position.average_entry_price * (1 + settings.paper_profit_activation_pct / 100.0)
    ):
        trail_stop = position.highest_price * (
            1 - settings.paper_trailing_stop_pct / 100.0
        )
        return max(base_stop, trail_stop)
    return base_stop


def best_rank(row: MarketActivity) -> int:
    return min(
        row.pct_rank if row.pct_rank > 0 else 9999,
        row.volume_rank if row.volume_rank > 0 else 9999,
    )
