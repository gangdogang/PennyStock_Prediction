from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ...config import AppSettings
from ...models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from ..fill_model import FillModel
from ..trading_support import market_status_blocks_trading


@dataclass(frozen=True, slots=True)
class ProfitRuleDeps:
    settings: AppSettings
    fill_model: FillModel
    strategy_mode: str
    decision_basis_price: Callable[[PaperPosition], float]
    stop_price: Callable[[PaperPosition], float]
    order_id_factory: Callable[[], str]


def apply_profit_management(
    deps: ProfitRuleDeps,
    *,
    run: PaperTradingRun,
    positions: list[PaperPosition],
    activity_by_symbol: dict[str, MarketActivity],
    market_phase: str,
    day_regime: str,
    now: datetime,
) -> list[PaperOrder]:
    if deps.strategy_mode != "adaptive":
        return []
    orders: list[PaperOrder] = []
    for position in positions:
        if position.status != "OPEN" or position.quantity <= 1 or position.partial_exit_count > 0:
            continue
        row = activity_by_symbol.get(position.symbol)
        if row is None or position.last_price is None:
            continue
        if market_status_blocks_trading(row.market_status):
            continue
        decision_basis = deps.decision_basis_price(position)
        gain_pct = (
            ((position.last_price - decision_basis) / decision_basis) * 100.0
            if decision_basis > 0
            else 0.0
        )
        if gain_pct < deps.settings.paper_adaptive_partial_take_profit_pct:
            continue
        trim_quantity = max(
            int(position.quantity * deps.settings.paper_adaptive_partial_exit_fraction),
            1,
        )
        if trim_quantity >= position.quantity:
            trim_quantity = position.quantity - 1
        if trim_quantity <= 0:
            continue
        orders.append(
            trim_position(
                deps,
                run=run,
                position=position,
                row=row,
                market_phase=market_phase,
                quantity=trim_quantity,
                reason="partial_take_profit",
                day_regime=day_regime,
                now=now,
                analysis_label=row.analysis_label,
                analysis_score=row.analysis_score,
                reasons=row.reasons[:5],
            )
        )
    return orders


def trim_position(
    deps: ProfitRuleDeps,
    *,
    run: PaperTradingRun,
    position: PaperPosition,
    row: MarketActivity,
    market_phase: str,
    quantity: int,
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
        requested_quantity=min(max(quantity, 1), max(position.quantity - 1, 1)),
    )
    trim_quantity = min(fill.quantity, max(position.quantity - 1, 1))
    remaining_quantity = fill.remaining_quantity
    price = fill.fill_price
    notional = (price or 0.0) * trim_quantity
    transaction_cost = fill.transaction_cost
    realized_pnl = ((price or 0.0) - position.average_entry_price) * trim_quantity - transaction_cost
    realized_pnl_pct = (
        ((((price or 0.0) - position.average_entry_price) / position.average_entry_price) * 100.0)
        if position.average_entry_price > 0
        else 0.0
    )
    run.cash_balance += notional - transaction_cost
    run.total_transaction_cost += transaction_cost
    position.quantity -= trim_quantity
    position.cost_basis = position.average_entry_price * position.quantity
    position.realized_pnl += realized_pnl
    position.fees_paid_total += transaction_cost
    position.market_value = (price or 0.0) * position.quantity
    position.unrealized_pnl = ((price or position.average_entry_price) - position.average_entry_price) * position.quantity
    position.total_pnl = position.realized_pnl + position.unrealized_pnl
    position.partial_exit_count += 1
    position.stop_price = deps.stop_price(position)
    position.updated_at = now
    position.exit_reasons = position.exit_reasons + [reason] + reasons[:2]
    return PaperOrder(
        order_id=deps.order_id_factory(),
        run_id=run.run_id,
        position_id=position.position_id,
        symbol=position.symbol,
        market_phase=market_phase,
        action="SELL",
        intent="TRIM",
        quantity=trim_quantity,
        requested_quantity=trim_quantity + remaining_quantity,
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
