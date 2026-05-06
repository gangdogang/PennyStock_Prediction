from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import PaperOrder, PaperPosition, PaperRunSnapshot
from .connection import get_connection
from .paper_rows import (
    _paper_order_from_row,
    _paper_position_from_row,
    _paper_snapshot_from_row,
)
from .paper_runs import (
    create_paper_trading_run,
    fetch_active_paper_trading_run,
    fetch_latest_paper_strategy_runs,
    fetch_latest_paper_trading_run,
    fetch_paper_trading_run_by_id,
    upsert_paper_trading_run,
)


def serialize_pyramid_state(state: object | None) -> str | None:
    if state is None:
        return None
    if isinstance(state, str):
        return state
    return json.dumps(state, sort_keys=True)


def deserialize_pyramid_state(value: str | None) -> dict[str, object] | None:
    if value in {None, ""}:
        return None
    decoded = json.loads(str(value))
    if isinstance(decoded, dict):
        return {str(key): item for key, item in decoded.items()}
    return {"value": decoded}


def upsert_paper_positions(
    database_path: Path,
    rows: Iterable[PaperPosition],
) -> None:
    payload = [
        (
            row.position_id,
            row.run_id,
            row.symbol,
            row.status,
            row.entry_phase,
            row.entry_label,
            row.exit_reason,
            row.quantity,
            row.average_entry_price,
            row.last_price,
            row.cost_basis,
            row.market_value,
            row.realized_pnl,
            row.unrealized_pnl,
            row.total_pnl,
            row.stop_price,
            row.planned_stop_price,
            row.planned_risk_pct,
            row.highest_price,
            row.add_count,
            row.partial_exit_count,
            row.strategy_bucket,
            row.fill_reference_price,
            row.fill_slippage_pct,
            row.fees_paid_total,
            row.day_regime,
            row.watchlist_rank_at_entry,
            row.pyramid_state,
            json.dumps(row.entry_reasons),
            json.dumps(row.exit_reasons),
            row.opened_at.isoformat(),
            row.updated_at.isoformat(),
            row.closed_at.isoformat() if row.closed_at else None,
        )
        for row in rows
    ]
    if not payload:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO paper_positions (
                position_id,
                run_id,
                symbol,
                status,
                entry_phase,
                entry_label,
                exit_reason,
                quantity,
                average_entry_price,
                last_price,
                cost_basis,
                market_value,
                realized_pnl,
                unrealized_pnl,
                total_pnl,
                stop_price,
                planned_stop_price,
                planned_risk_pct,
                highest_price,
                add_count,
                partial_exit_count,
                strategy_bucket,
                fill_reference_price,
                fill_slippage_pct,
                fees_paid_total,
                day_regime,
                watchlist_rank_at_entry,
                pyramid_state,
                entry_reasons,
                exit_reasons,
                opened_at,
                updated_at,
                closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(position_id) DO UPDATE SET
                run_id = excluded.run_id,
                symbol = excluded.symbol,
                status = excluded.status,
                entry_phase = excluded.entry_phase,
                entry_label = excluded.entry_label,
                exit_reason = excluded.exit_reason,
                quantity = excluded.quantity,
                average_entry_price = excluded.average_entry_price,
                last_price = excluded.last_price,
                cost_basis = excluded.cost_basis,
                market_value = excluded.market_value,
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                total_pnl = excluded.total_pnl,
                stop_price = excluded.stop_price,
                planned_stop_price = excluded.planned_stop_price,
                planned_risk_pct = excluded.planned_risk_pct,
                highest_price = excluded.highest_price,
                add_count = excluded.add_count,
                partial_exit_count = excluded.partial_exit_count,
                strategy_bucket = excluded.strategy_bucket,
                fill_reference_price = excluded.fill_reference_price,
                fill_slippage_pct = excluded.fill_slippage_pct,
                fees_paid_total = excluded.fees_paid_total,
                day_regime = excluded.day_regime,
                watchlist_rank_at_entry = excluded.watchlist_rank_at_entry,
                pyramid_state = excluded.pyramid_state,
                entry_reasons = excluded.entry_reasons,
                exit_reasons = excluded.exit_reasons,
                opened_at = excluded.opened_at,
                updated_at = excluded.updated_at,
                closed_at = excluded.closed_at
            """,
            payload,
        )


def fetch_paper_positions(
    database_path: Path,
    run_id: str,
    status: str | None = None,
) -> list[PaperPosition]:
    query = """
        SELECT *
        FROM paper_positions
        WHERE run_id = ?
    """
    params: list[object] = [run_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY opened_at ASC, symbol ASC"
    with get_connection(database_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_paper_position_from_row(row) for row in rows if row is not None]


def insert_paper_orders(
    database_path: Path,
    rows: Iterable[PaperOrder],
) -> None:
    payload = [
        (
            row.order_id,
            row.run_id,
            row.position_id,
            row.symbol,
            row.market_phase,
            row.action,
            row.intent,
            row.quantity,
            row.requested_quantity,
            row.remaining_quantity,
            row.fill_status,
            row.price,
            row.notional,
            row.transaction_cost,
            row.strategy_bucket,
            row.analysis_label,
            row.analysis_score,
            row.planned_stop_price,
            row.planned_risk_pct,
            row.fill_reference_price,
            row.fill_slippage_pct,
            row.bar_volume,
            row.bar_dollar_volume,
            row.shares_pct_of_bar_volume,
            row.notional_pct_of_bar_dollar_volume,
            row.estimated_capacity_at_1pct_volume,
            row.estimated_capacity_at_2pct_volume,
            int(row.capacity_limited),
            row.participation_slippage_pct,
            row.day_regime,
            row.watchlist_rank_at_entry,
            row.leg_index,
            row.setup_id,
            json.dumps(row.reasons),
            row.created_at.isoformat(),
            row.realized_pnl,
            row.realized_pnl_pct,
        )
        for row in rows
    ]
    if not payload:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO paper_orders (
                order_id,
                run_id,
                position_id,
                symbol,
                market_phase,
                action,
                intent,
                quantity,
                requested_quantity,
                remaining_quantity,
                fill_status,
                price,
                notional,
                transaction_cost,
                strategy_bucket,
                analysis_label,
                analysis_score,
                planned_stop_price,
                planned_risk_pct,
                fill_reference_price,
                fill_slippage_pct,
                bar_volume,
                bar_dollar_volume,
                shares_pct_of_bar_volume,
                notional_pct_of_bar_dollar_volume,
                estimated_capacity_at_1pct_volume,
                estimated_capacity_at_2pct_volume,
                capacity_limited,
                participation_slippage_pct,
                day_regime,
                watchlist_rank_at_entry,
                leg_index,
                setup_id,
                reasons,
                created_at,
                realized_pnl,
                realized_pnl_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


def fetch_paper_orders(
    database_path: Path,
    run_id: str,
    limit: int | None = None,
) -> list[PaperOrder]:
    query = """
        SELECT *
        FROM paper_orders
        WHERE run_id = ?
        ORDER BY created_at ASC, id ASC
    """
    params: list[object] = [run_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection(database_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_paper_order_from_row(row) for row in rows if row is not None]


def insert_paper_run_snapshot(
    database_path: Path,
    row: PaperRunSnapshot,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO paper_run_snapshots (
                run_id,
                market_phase,
                cash_balance,
                equity,
                realized_pnl,
                unrealized_pnl,
                total_return_pct,
                max_drawdown_pct,
                open_position_count,
                closed_trade_count,
                win_rate,
                profit_factor,
                reward_risk_ratio,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.run_id,
                row.market_phase,
                row.cash_balance,
                row.equity,
                row.realized_pnl,
                row.unrealized_pnl,
                row.total_return_pct,
                row.max_drawdown_pct,
                row.open_position_count,
                row.closed_trade_count,
                row.win_rate,
                row.profit_factor,
                row.reward_risk_ratio,
                json.dumps(row.notes),
                row.created_at.isoformat(),
            ),
        )


def fetch_paper_run_snapshots(
    database_path: Path,
    run_id: str,
    limit: int | None = None,
) -> list[PaperRunSnapshot]:
    query = """
        SELECT *
        FROM paper_run_snapshots
        WHERE run_id = ?
        ORDER BY created_at ASC, id ASC
    """
    params: list[object] = [run_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with get_connection(database_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_paper_snapshot_from_row(row) for row in rows if row is not None]
