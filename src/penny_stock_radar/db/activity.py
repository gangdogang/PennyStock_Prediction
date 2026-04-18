from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..models import MarketActivity, PredictionOutcome
from .connection import _resolve_scan_id, get_connection

def insert_market_activity(
    database_path: Path,
    scan_id: str,
    market_phase: str,
    rows: Iterable[MarketActivity],
) -> None:
    payload = [
        (
            scan_id,
            market_phase,
            row.symbol,
            row.source,
            row.last_price,
            row.bid_price,
            row.ask_price,
            row.previous_close,
            row.pct_change,
            row.volume,
            row.dollar_volume,
            row.trade_size,
            row.spread_pct,
            row.market_status,
            row.market_data_at.isoformat() if row.market_data_at else None,
            row.data_age_seconds,
            int(row.has_live_trade),
            int(row.has_live_quote),
            row.pct_rank,
            row.volume_rank,
            row.watchlist_rank,
            row.watchlist_score,
            int(row.predicted),
            row.leader_persistence_score,
            row.pullback_absorption_score,
            row.trap_score,
            row.behavioral_score,
            row.analysis_label,
            row.analysis_score,
            json.dumps(row.reasons),
            row.created_at.isoformat(),
        )
        for row in rows
    ]
    if not payload:
        return
    with get_connection(database_path) as connection:
        connection.execute(
            "DELETE FROM market_activity WHERE scan_id = ? AND market_phase = ?",
            (scan_id, market_phase),
        )
        connection.executemany(
            """
            INSERT INTO market_activity (
                scan_id,
                market_phase,
                symbol,
                source,
                last_price,
                bid_price,
                ask_price,
                previous_close,
                pct_change,
                volume,
                dollar_volume,
                trade_size,
                spread_pct,
                market_status,
                market_data_at,
                data_age_seconds,
                has_live_trade,
                has_live_quote,
                pct_rank,
                volume_rank,
                watchlist_rank,
                watchlist_score,
                predicted,
                leader_persistence_score,
                pullback_absorption_score,
                trap_score,
                behavioral_score,
                analysis_label,
                analysis_score,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.executemany(
            """
            INSERT INTO market_activity_history (
                scan_id,
                market_phase,
                symbol,
                source,
                last_price,
                bid_price,
                ask_price,
                previous_close,
                pct_change,
                volume,
                dollar_volume,
                trade_size,
                spread_pct,
                market_status,
                market_data_at,
                data_age_seconds,
                has_live_trade,
                has_live_quote,
                pct_rank,
                volume_rank,
                watchlist_rank,
                watchlist_score,
                predicted,
                leader_persistence_score,
                pullback_absorption_score,
                trap_score,
                behavioral_score,
                analysis_label,
                analysis_score,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


def fetch_latest_market_activity(
    database_path: Path,
    market_phase: str,
    limit: int = 20,
    sort_by: str = "pct_change",
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> list[sqlite3.Row]:
    order_by = "pct_rank ASC, symbol ASC"
    if sort_by.lower() in {"volume", "dollar_volume"}:
        order_by = "volume_rank ASC, symbol ASC"
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return []
        return connection.execute(
            f"""
            SELECT *
            FROM market_activity
            WHERE scan_id = ? AND market_phase = ?
            ORDER BY {order_by}
            LIMIT ?
            """,
            (target_scan_id, market_phase, limit),
        ).fetchall()


def fetch_recent_market_activity(
    database_path: Path,
    limit: int = 50,
    market_phases: tuple[str, ...] = ("premarket", "regular"),
    symbols: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    if limit <= 0 or not market_phases:
        return []
    phase_placeholders = ", ".join("?" for _ in market_phases)
    params: list[object] = [*market_phases]
    symbol_clause = ""
    if symbols:
        symbol_placeholders = ", ".join("?" for _ in symbols)
        symbol_clause = f" AND symbol IN ({symbol_placeholders})"
        params.extend(symbols)
    with get_connection(database_path) as connection:
        return connection.execute(
            f"""
            SELECT *
            FROM market_activity_history
            WHERE market_phase IN ({phase_placeholders})
            {symbol_clause}
            ORDER BY created_at DESC, analysis_score DESC, pct_rank ASC, volume_rank ASC, symbol ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()


def insert_prediction_outcomes(
    database_path: Path,
    scan_id: str,
    market_phase: str,
    rows: Iterable[PredictionOutcome],
) -> None:
    payload = [
        (
            scan_id,
            market_phase,
            row.symbol,
            int(row.predicted),
            row.watchlist_rank,
            row.watchlist_score,
            row.pct_rank,
            row.volume_rank,
            row.pct_change,
            row.volume,
            row.dollar_volume,
            row.analysis_label,
            row.outcome,
            json.dumps(row.reasons),
            row.created_at.isoformat(),
        )
        for row in rows
    ]
    if not payload:
        return
    with get_connection(database_path) as connection:
        connection.execute(
            "DELETE FROM prediction_outcomes WHERE scan_id = ? AND market_phase = ?",
            (scan_id, market_phase),
        )
        connection.executemany(
            """
            INSERT INTO prediction_outcomes (
                scan_id,
                market_phase,
                symbol,
                predicted,
                watchlist_rank,
                watchlist_score,
                pct_rank,
                volume_rank,
                pct_change,
                volume,
                dollar_volume,
                analysis_label,
                outcome,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


def fetch_latest_prediction_outcomes(
    database_path: Path,
    market_phase: str,
    limit: int = 50,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM prediction_outcomes
            WHERE scan_id = ? AND market_phase = ?
            ORDER BY
                predicted DESC,
                CASE
                    WHEN pct_rank IS NULL THEN 1
                    ELSE 0
                END ASC,
                pct_rank ASC,
                CASE
                    WHEN volume_rank IS NULL THEN 1
                    ELSE 0
                END ASC,
                volume_rank ASC,
                symbol ASC
            LIMIT ?
            """,
            (target_scan_id, market_phase, limit),
        ).fetchall()
