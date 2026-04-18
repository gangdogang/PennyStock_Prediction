from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..models import PaperTradingRun
from .connection import get_connection
from .paper_rows import _paper_run_from_row


def create_paper_trading_run(
    database_path: Path,
    strategy_name: str,
    initial_capital: float,
    notes: list[str] | None = None,
    *,
    bucket: str = "predictor_weighted",
) -> PaperTradingRun:
    now = PaperTradingRun(
        run_id=str(uuid.uuid4()),
        strategy_name=strategy_name,
        bucket=bucket,
        initial_capital=float(initial_capital),
        cash_balance=float(initial_capital),
        equity=float(initial_capital),
        equity_peak=float(initial_capital),
        notes=list(notes or []),
    )
    upsert_paper_trading_run(database_path, now)
    return now


def upsert_paper_trading_run(
    database_path: Path,
    run: PaperTradingRun,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO paper_runs (
                run_id,
                strategy_name,
                bucket,
                status,
                initial_capital,
                cash_balance,
                equity,
                equity_peak,
                realized_pnl,
                unrealized_pnl,
                total_return_pct,
                max_drawdown_pct,
                closed_trade_count,
                winning_trade_count,
                losing_trade_count,
                win_rate,
                gross_profit,
                gross_loss,
                total_transaction_cost,
                profit_factor,
                average_win,
                average_loss,
                reward_risk_ratio,
                last_phase,
                last_market_date,
                notes,
                created_at,
                updated_at,
                ended_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                strategy_name = excluded.strategy_name,
                bucket = excluded.bucket,
                status = excluded.status,
                initial_capital = excluded.initial_capital,
                cash_balance = excluded.cash_balance,
                equity = excluded.equity,
                equity_peak = excluded.equity_peak,
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                total_return_pct = excluded.total_return_pct,
                max_drawdown_pct = excluded.max_drawdown_pct,
                closed_trade_count = excluded.closed_trade_count,
                winning_trade_count = excluded.winning_trade_count,
                losing_trade_count = excluded.losing_trade_count,
                win_rate = excluded.win_rate,
                gross_profit = excluded.gross_profit,
                gross_loss = excluded.gross_loss,
                total_transaction_cost = excluded.total_transaction_cost,
                profit_factor = excluded.profit_factor,
                average_win = excluded.average_win,
                average_loss = excluded.average_loss,
                reward_risk_ratio = excluded.reward_risk_ratio,
                last_phase = excluded.last_phase,
                last_market_date = excluded.last_market_date,
                notes = excluded.notes,
                updated_at = excluded.updated_at,
                ended_at = excluded.ended_at
            """,
            (
                run.run_id,
                run.strategy_name,
                run.bucket,
                run.status,
                run.initial_capital,
                run.cash_balance,
                run.equity,
                run.equity_peak,
                run.realized_pnl,
                run.unrealized_pnl,
                run.total_return_pct,
                run.max_drawdown_pct,
                run.closed_trade_count,
                run.winning_trade_count,
                run.losing_trade_count,
                run.win_rate,
                run.gross_profit,
                run.gross_loss,
                run.total_transaction_cost,
                run.profit_factor,
                run.average_win,
                run.average_loss,
                run.reward_risk_ratio,
                run.last_phase,
                run.last_market_date,
                json.dumps(run.notes),
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.ended_at.isoformat() if run.ended_at else None,
            ),
        )


def fetch_active_paper_trading_run(
    database_path: Path,
    strategy_name: str | None = None,
    *,
    bucket: str | None = None,
) -> PaperTradingRun | None:
    query = """
        SELECT *
        FROM paper_runs
        WHERE status = 'ACTIVE'
    """
    params: list[object] = []
    if strategy_name:
        query += " AND strategy_name = ?"
        params.append(strategy_name)
    if bucket:
        query += " AND bucket = ?"
        params.append(bucket)
    query += " ORDER BY updated_at DESC LIMIT 1"
    with get_connection(database_path) as connection:
        row = connection.execute(query, tuple(params)).fetchone()
    return _paper_run_from_row(row)


def fetch_latest_paper_trading_run(
    database_path: Path,
    strategy_name: str | None = None,
    *,
    bucket: str | None = None,
) -> PaperTradingRun | None:
    query = """
        SELECT *
        FROM paper_runs
    """
    params: list[object] = []
    if strategy_name:
        query += " WHERE strategy_name = ?"
        params.append(strategy_name)
    if bucket:
        query += " AND bucket = ?" if strategy_name else " WHERE bucket = ?"
        params.append(bucket)
    query += " ORDER BY updated_at DESC LIMIT 1"
    with get_connection(database_path) as connection:
        row = connection.execute(query, tuple(params)).fetchone()
    return _paper_run_from_row(row)


def fetch_latest_paper_strategy_runs(
    database_path: Path,
    limit: int = 10,
) -> list[PaperTradingRun]:
    with get_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM paper_runs
            ORDER BY strategy_name ASC, bucket ASC, updated_at DESC
            """
        ).fetchall()
    latest_by_strategy: list[PaperTradingRun] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        run_key = (
            str(row["strategy_name"]),
            str(row["bucket"] or "predictor_weighted"),
        )
        if run_key in seen:
            continue
        seen.add(run_key)
        parsed = _paper_run_from_row(row)
        if parsed is not None:
            latest_by_strategy.append(parsed)
        if len(latest_by_strategy) >= max(limit, 1):
            break
    latest_by_strategy.sort(key=lambda item: item.updated_at, reverse=True)
    return latest_by_strategy


def fetch_paper_trading_run_by_id(
    database_path: Path,
    run_id: str,
) -> PaperTradingRun | None:
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM paper_runs
            WHERE run_id = ?
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
    return _paper_run_from_row(row)
