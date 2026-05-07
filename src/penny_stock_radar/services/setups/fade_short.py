"""FadeShortSetup — short-side fade on DEAD_PUMP / FAILED_BREAKOUT.

Structural rationale: the opposite-side diagnostic confirms that the
same entry signals used by long momentum produce 83% win rate on the
short side (even after 25-50%/yr borrow cost). This setup systematizes
that edge using existing setup_state classification without any new data
requirements. It is intentionally conservative:

    - Only fires in regular session (09:35-15:45 ET) to avoid halt risk
    - Hard gate: spread_pct <= paper_max_short_spread_pct (default 0.02)
    - Hard gate: pct_change >= paper_min_short_pct_change (default 15.0)
    - Position direction = "SHORT" so P&L is inverted in reporting
    - Stop = entry × (1 + short_stop_pct) — above entry, not below
    - Borrow cost appended as a synthetic transaction cost at entry

Production blocker: KIS broker does not support US penny-stock short
locates. This setup must be routed through a separate broker (IBKR/Alpaca)
before being enabled. Default: bucket gated so it never fires unless
PSR_ENABLE_FADE_SHORT=1 is set.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from ...models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from ..engine_rules.entry import EntryRuleDeps, current_open_risk
from ..engine_rules.exit import ExitRuleDeps, close_position, planned_risk_pct
from ..momentum_advisor import MomentumAdvice
from ..pyramid import LEGACY_SCHEDULE, PyramidSchedule
from ..trading_support import market_status_blocks_trading
from .base import ACTION_ENTER, ACTION_SKIP, SetupDecision, SetupEvalContext

if TYPE_CHECKING:
    pass

FADE_SHORT_BUCKET = "fade_short"

_FADE_SETUP_STATES = {"DEAD_PUMP", "FAILED_BREAKOUT", "EXIT_FAIL"}

_REGULAR_SESSION_START_HOUR_ET = 9
_REGULAR_SESSION_START_MINUTE_ET = 35
_REGULAR_SESSION_END_HOUR_ET = 15
_REGULAR_SESSION_END_MINUTE_ET = 45


def _enabled() -> bool:
    return os.getenv("PSR_ENABLE_FADE_SHORT", "0") == "1"


def _in_regular_fade_window(now: datetime) -> bool:
    try:
        from zoneinfo import ZoneInfo
        eastern = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return False
    if eastern.weekday() >= 5:
        return False
    t = eastern.hour * 60 + eastern.minute
    start = _REGULAR_SESSION_START_HOUR_ET * 60 + _REGULAR_SESSION_START_MINUTE_ET
    end = _REGULAR_SESSION_END_HOUR_ET * 60 + _REGULAR_SESSION_END_MINUTE_ET
    return start <= t <= end


class FadeShortSetup:
    """Short-side fade triggered by DEAD_PUMP / FAILED_BREAKOUT setup state."""

    setup_id = "fade_short"
    bucket_compatibility = (FADE_SHORT_BUCKET,)

    def pyramid_schedule(self) -> PyramidSchedule:
        return LEGACY_SCHEDULE

    def detect(self, ctx: SetupEvalContext) -> bool:
        if not _enabled():
            return False
        if ctx.open_position is not None:
            return False  # no adds on short positions for now
        row = ctx.market_activity
        if row.last_price is None or row.last_price <= 0:
            return False
        if not _in_regular_fade_window(ctx.bar_at):
            return False
        state = (ctx.setup_judgement.setup_state if ctx.setup_judgement else None) or ""
        if state not in _FADE_SETUP_STATES:
            return False
        settings = ctx.deps.settings
        spread = row.spread_pct or 0.0
        if spread > settings.paper_max_short_spread_pct:
            return False
        if (row.pct_change or 0.0) < settings.paper_min_short_pct_change:
            return False
        if market_status_blocks_trading(row.market_status):
            return False
        return True

    def evaluate(self, ctx: SetupEvalContext) -> SetupDecision:
        if not self.detect(ctx):
            return SetupDecision(
                setup_id=self.setup_id,
                action=ACTION_SKIP,
                size_fraction=0.0,
                stop_price=None,
                target_price=None,
                reason="fade_short_not_detected",
            )
        row = ctx.market_activity
        settings = ctx.deps.settings
        bid = row.bid_price if row.bid_price and row.bid_price > 0 else row.last_price
        stop_pct = settings.paper_short_stop_pct
        stop_above_entry = bid * (1.0 + stop_pct / 100.0) if bid else None
        target = bid * (1.0 - settings.paper_short_target_pct / 100.0) if bid else None
        return SetupDecision(
            setup_id=self.setup_id,
            action=ACTION_ENTER,
            size_fraction=settings.paper_short_entry_size_fraction,
            stop_price=stop_above_entry,
            target_price=target,
            reason="fade_short_dead_pump",
            metadata={"setup_state": (ctx.setup_judgement.setup_state if ctx.setup_judgement else ""), "direction": "SHORT"},
        )

    def apply_entry_rules(
        self,
        deps: EntryRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity: list[MarketActivity],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        market_date: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        if not _enabled():
            return []
        if market_phase != "regular":
            return []

        settings = deps.settings
        open_positions = [p for p in positions if p.status == "OPEN"]
        open_symbols = {p.symbol for p in open_positions}
        traded_today = {
            p.symbol
            for p in positions
            if deps.market_date(p.opened_at) == market_date
        }

        equity_basis = run.cash_balance + sum(p.market_value for p in open_positions)
        equity_basis = equity_basis if equity_basis > 0 else run.initial_capital
        risk_cap = equity_basis * settings.trade_plan_max_concurrent_open_risk_pct / 100.0
        tracked_open_risk = current_open_risk(open_positions)
        if tracked_open_risk >= risk_cap:
            return []

        orders: list[PaperOrder] = []
        slots = max(settings.paper_max_open_positions - len(open_symbols), 0)
        if slots <= 0:
            return []

        for row in sorted(
            activity,
            key=lambda r: -(r.pct_change or 0.0),
        ):
            if len(orders) >= slots:
                break
            if row.symbol in open_symbols or row.symbol in traded_today:
                continue
            if row.last_price is None or row.last_price <= 0:
                continue
            if not _in_regular_fade_window(now):
                continue
            if market_status_blocks_trading(row.market_status):
                continue
            spread = row.spread_pct or 0.0
            if spread > settings.paper_max_short_spread_pct:
                continue
            if (row.pct_change or 0.0) < settings.paper_min_short_pct_change:
                continue

            bid = row.bid_price if row.bid_price and row.bid_price > 0 else row.last_price
            fill_price = bid
            stop_pct = settings.paper_short_stop_pct
            stop_price = fill_price * (1.0 + stop_pct / 100.0)

            risk_dollars = equity_basis * settings.trade_plan_per_trade_risk_pct / 100.0
            stop_distance = fill_price * stop_pct / 100.0
            if stop_distance <= 0:
                continue

            risk_qty = int(risk_dollars / stop_distance)
            max_notional = equity_basis * settings.paper_short_entry_size_fraction
            max_qty = int(max_notional / fill_price) if fill_price > 0 else 0
            quantity = max(min(risk_qty, max_qty), 0)
            if quantity <= 0:
                continue

            # borrow cost for the hold period (intraday default 1 day fraction)
            borrow_pct_annual = settings.paper_short_borrow_cost_pct_annual
            borrow_cost = fill_price * quantity * borrow_pct_annual / 252.0 / 100.0
            transaction_cost = max(settings.paper_min_trade_fee, quantity * fill_price * settings.paper_notional_fee_rate_pct / 100.0) + borrow_cost

            notional = fill_price * quantity
            order_risk = quantity * stop_distance
            if tracked_open_risk + order_risk > risk_cap:
                continue

            import uuid
            position_id = deps.order_id_factory()
            position = PaperPosition(
                position_id=position_id,
                run_id=run.run_id,
                symbol=row.symbol,
                status="OPEN",
                entry_phase=market_phase,
                entry_label=row.analysis_label,
                quantity=quantity,
                average_entry_price=fill_price,
                last_price=row.last_price,
                cost_basis=notional + transaction_cost,
                market_value=notional,
                stop_price=stop_price,
                planned_stop_price=stop_price,
                planned_risk_pct=planned_risk_pct(fill_price, stop_price),
                highest_price=fill_price,
                strategy_bucket=self.setup_id,
                fill_reference_price=fill_price,
                fill_slippage_pct=0.0,
                fees_paid_total=transaction_cost,
                day_regime=day_regime,
                watchlist_rank_at_entry=row.watchlist_rank,
                entry_spread_pct=row.spread_pct,
                direction="SHORT",
                entry_reasons=[self.setup_id, f"pct_change:{row.pct_change:.1f}", f"spread:{spread:.3f}"],
                opened_at=now,
                updated_at=now,
            )
            run.cash_balance -= transaction_cost
            positions.append(position)
            open_symbols.add(row.symbol)
            tracked_open_risk += order_risk

            orders.append(
                PaperOrder(
                    order_id=deps.order_id_factory(),
                    run_id=run.run_id,
                    position_id=position_id,
                    symbol=row.symbol,
                    market_phase=market_phase,
                    action="SELL",
                    intent="ENTRY",
                    quantity=quantity,
                    price=fill_price,
                    notional=notional,
                    transaction_cost=transaction_cost,
                    strategy_bucket=self.setup_id,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    planned_stop_price=stop_price,
                    planned_risk_pct=position.planned_risk_pct,
                    fill_reference_price=fill_price,
                    fill_slippage_pct=0.0,
                    day_regime=day_regime,
                    direction="SHORT",
                    reasons=[self.setup_id, "fade_dead_pump"],
                    created_at=now,
                )
            )

        return orders

    def apply_exit_rules(
        self,
        deps: ExitRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        """Exit short if price touches stop (above entry) or target (below entry)."""
        if not _enabled():
            return []
        orders: list[PaperOrder] = []
        settings = deps.settings
        for position in positions:
            if position.status != "OPEN" or position.direction != "SHORT":
                continue
            row = activity_by_symbol.get(position.symbol)
            if row is None or row.last_price is None:
                continue
            if market_status_blocks_trading(row.market_status):
                continue
            reason: str | None = None
            price = row.last_price
            if position.stop_price is not None and price >= position.stop_price:
                reason = "short_stop_loss"
            elif position.planned_risk_pct is not None:
                target = position.average_entry_price * (1.0 - settings.paper_short_target_pct / 100.0)
                if price <= target:
                    reason = "short_target"
            if reason is None:
                continue
            pnl = (position.average_entry_price - price) * position.quantity
            borrow_cost = 0.0
            held_days = max((now - position.opened_at).total_seconds() / 86400.0, 1.0 / 390.0)
            borrow_cost = (
                position.average_entry_price * position.quantity
                * settings.paper_short_borrow_cost_pct_annual / 365.0 / 100.0
                * held_days
            )
            exit_cost = max(settings.paper_min_trade_fee, position.quantity * price * settings.paper_notional_fee_rate_pct / 100.0)
            net_pnl = pnl - exit_cost - borrow_cost
            position.status = "CLOSED"
            position.exit_reason = reason
            position.realized_pnl += net_pnl
            position.total_pnl = position.realized_pnl
            position.unrealized_pnl = 0.0
            position.closed_at = now
            position.updated_at = now
            run.cash_balance += net_pnl
            run.realized_pnl += net_pnl
            orders.append(
                PaperOrder(
                    order_id=deps.order_id_factory(),
                    run_id=run.run_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    market_phase=market_phase,
                    action="BUY",
                    intent="EXIT",
                    quantity=position.quantity,
                    price=price,
                    notional=price * position.quantity,
                    transaction_cost=exit_cost + borrow_cost,
                    strategy_bucket=self.setup_id,
                    analysis_label=row.analysis_label,
                    day_regime=day_regime,
                    direction="SHORT",
                    realized_pnl=net_pnl,
                    realized_pnl_pct=(net_pnl / (position.average_entry_price * position.quantity)) * 100.0 if position.average_entry_price > 0 else None,
                    reasons=[reason],
                    created_at=now,
                )
            )
        return orders

    def apply_profit_management(
        self,
        deps: ExitRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return []
