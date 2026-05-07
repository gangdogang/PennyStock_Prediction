from __future__ import annotations

import sqlite3

from ..models import PaperOrder, PaperPosition, PaperRunSnapshot, PaperTradingRun
from .connection import _parse_iso_datetime, _safe_json_list

def _paper_run_from_row(row: sqlite3.Row | None) -> PaperTradingRun | None:
    if row is None:
        return None
    return PaperTradingRun(
        run_id=row["run_id"],
        strategy_name=row["strategy_name"],
        bucket=str(row["bucket"] or "predictor_weighted"),
        status=row["status"],
        initial_capital=float(row["initial_capital"]),
        cash_balance=float(row["cash_balance"]),
        equity=float(row["equity"]),
        equity_peak=float(row["equity_peak"]),
        realized_pnl=float(row["realized_pnl"]),
        unrealized_pnl=float(row["unrealized_pnl"]),
        total_return_pct=float(row["total_return_pct"]),
        max_drawdown_pct=float(row["max_drawdown_pct"]),
        closed_trade_count=int(row["closed_trade_count"]),
        winning_trade_count=int(row["winning_trade_count"]),
        losing_trade_count=int(row["losing_trade_count"]),
        win_rate=float(row["win_rate"]),
        gross_profit=float(row["gross_profit"]),
        gross_loss=float(row["gross_loss"]),
        total_transaction_cost=float(row["total_transaction_cost"]) if row["total_transaction_cost"] is not None else 0.0,
        profit_factor=float(row["profit_factor"]),
        average_win=float(row["average_win"]),
        average_loss=float(row["average_loss"]),
        reward_risk_ratio=float(row["reward_risk_ratio"]),
        last_phase=row["last_phase"],
        last_market_date=row["last_market_date"],
        notes=_safe_json_list(row["notes"]),
        created_at=_parse_iso_datetime(row["created_at"]),
        updated_at=_parse_iso_datetime(row["updated_at"]),
        ended_at=_parse_iso_datetime(row["ended_at"]),
    )


def _paper_position_from_row(row: sqlite3.Row) -> PaperPosition:
    return PaperPosition(
        position_id=row["position_id"],
        run_id=row["run_id"],
        symbol=row["symbol"],
        status=row["status"],
        entry_phase=row["entry_phase"],
        entry_label=row["entry_label"],
        exit_reason=row["exit_reason"],
        quantity=int(row["quantity"]),
        average_entry_price=float(row["average_entry_price"]),
        last_price=float(row["last_price"]) if row["last_price"] is not None else None,
        cost_basis=float(row["cost_basis"]),
        market_value=float(row["market_value"]),
        realized_pnl=float(row["realized_pnl"]),
        unrealized_pnl=float(row["unrealized_pnl"]),
        total_pnl=float(row["total_pnl"]),
        stop_price=float(row["stop_price"]) if row["stop_price"] is not None else None,
        planned_stop_price=float(row["planned_stop_price"]) if row["planned_stop_price"] is not None else None,
        planned_risk_pct=float(row["planned_risk_pct"]) if row["planned_risk_pct"] is not None else None,
        highest_price=float(row["highest_price"]) if row["highest_price"] is not None else None,
        add_count=int(row["add_count"]),
        partial_exit_count=int(row["partial_exit_count"]) if row["partial_exit_count"] is not None else 0,
        strategy_bucket=str(row["strategy_bucket"] or ""),
        fill_reference_price=float(row["fill_reference_price"]) if row["fill_reference_price"] is not None else None,
        fill_slippage_pct=float(row["fill_slippage_pct"]) if row["fill_slippage_pct"] is not None else None,
        fees_paid_total=float(row["fees_paid_total"]) if row["fees_paid_total"] is not None else 0.0,
        day_regime=row["day_regime"],
        watchlist_rank_at_entry=int(row["watchlist_rank_at_entry"]) if row["watchlist_rank_at_entry"] is not None else None,
        pyramid_state=row["pyramid_state"],
        entry_spread_pct=float(row["entry_spread_pct"]) if row["entry_spread_pct"] is not None else None,
        direction=str(row["direction"]) if row["direction"] is not None else "LONG",
        entry_reasons=_safe_json_list(row["entry_reasons"]),
        exit_reasons=_safe_json_list(row["exit_reasons"]),
        opened_at=_parse_iso_datetime(row["opened_at"]),
        updated_at=_parse_iso_datetime(row["updated_at"]),
        closed_at=_parse_iso_datetime(row["closed_at"]),
    )


def _paper_order_from_row(row: sqlite3.Row) -> PaperOrder:
    return PaperOrder(
        order_id=row["order_id"],
        run_id=row["run_id"],
        position_id=row["position_id"],
        symbol=row["symbol"],
        market_phase=row["market_phase"],
        action=row["action"],
        intent=row["intent"],
        quantity=int(row["quantity"]),
        requested_quantity=int(row["requested_quantity"]) if row["requested_quantity"] is not None else None,
        remaining_quantity=int(row["remaining_quantity"]) if row["remaining_quantity"] is not None else 0,
        fill_status=str(row["fill_status"] or "FILLED"),
        price=float(row["price"]),
        notional=float(row["notional"]),
        transaction_cost=float(row["transaction_cost"]) if row["transaction_cost"] is not None else 0.0,
        strategy_bucket=str(row["strategy_bucket"] or ""),
        analysis_label=row["analysis_label"],
        analysis_score=float(row["analysis_score"]) if row["analysis_score"] is not None else None,
        planned_stop_price=float(row["planned_stop_price"]) if row["planned_stop_price"] is not None else None,
        planned_risk_pct=float(row["planned_risk_pct"]) if row["planned_risk_pct"] is not None else None,
        fill_reference_price=float(row["fill_reference_price"]) if row["fill_reference_price"] is not None else None,
        fill_slippage_pct=float(row["fill_slippage_pct"]) if row["fill_slippage_pct"] is not None else None,
        bar_volume=float(row["bar_volume"]) if row["bar_volume"] is not None else None,
        bar_dollar_volume=float(row["bar_dollar_volume"]) if row["bar_dollar_volume"] is not None else None,
        shares_pct_of_bar_volume=(
            float(row["shares_pct_of_bar_volume"])
            if row["shares_pct_of_bar_volume"] is not None
            else None
        ),
        notional_pct_of_bar_dollar_volume=(
            float(row["notional_pct_of_bar_dollar_volume"])
            if row["notional_pct_of_bar_dollar_volume"] is not None
            else None
        ),
        estimated_capacity_at_1pct_volume=(
            float(row["estimated_capacity_at_1pct_volume"])
            if row["estimated_capacity_at_1pct_volume"] is not None
            else None
        ),
        estimated_capacity_at_2pct_volume=(
            float(row["estimated_capacity_at_2pct_volume"])
            if row["estimated_capacity_at_2pct_volume"] is not None
            else None
        ),
        capacity_limited=bool(row["capacity_limited"]),
        participation_slippage_pct=(
            float(row["participation_slippage_pct"])
            if row["participation_slippage_pct"] is not None
            else 0.0
        ),
        day_regime=row["day_regime"],
        watchlist_rank_at_entry=int(row["watchlist_rank_at_entry"]) if row["watchlist_rank_at_entry"] is not None else None,
        leg_index=int(row["leg_index"]) if row["leg_index"] is not None else 0,
        setup_id=str(row["setup_id"] or "legacy_momentum"),
        direction=str(row["direction"]) if row["direction"] is not None else "LONG",
        reasons=_safe_json_list(row["reasons"]),
        created_at=_parse_iso_datetime(row["created_at"]),
        realized_pnl=float(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
        realized_pnl_pct=float(row["realized_pnl_pct"]) if row["realized_pnl_pct"] is not None else None,
    )


def _paper_snapshot_from_row(row: sqlite3.Row) -> PaperRunSnapshot:
    return PaperRunSnapshot(
        run_id=row["run_id"],
        market_phase=row["market_phase"],
        cash_balance=float(row["cash_balance"]),
        equity=float(row["equity"]),
        realized_pnl=float(row["realized_pnl"]),
        unrealized_pnl=float(row["unrealized_pnl"]),
        total_return_pct=float(row["total_return_pct"]),
        max_drawdown_pct=float(row["max_drawdown_pct"]),
        open_position_count=int(row["open_position_count"]),
        closed_trade_count=int(row["closed_trade_count"]),
        win_rate=float(row["win_rate"]),
        profit_factor=float(row["profit_factor"]),
        reward_risk_ratio=float(row["reward_risk_ratio"]),
        notes=_safe_json_list(row["notes"]),
        created_at=_parse_iso_datetime(row["created_at"]),
    )
