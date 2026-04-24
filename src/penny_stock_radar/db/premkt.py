from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import PremktPrediction, PremarketSignal
from .connection import _resolve_scan_id, get_connection

def insert_premkt_predictions(
    database_path: Path,
    scan_id: str,
    predictions: Iterable[PremktPrediction],
) -> None:
    rows = [
        (
            scan_id,
            row.symbol,
            row.score,
            row.max_hold_days,
            row.entry_rationale,
            json.dumps(row.themes),
            row.filing_summary,
            row.generated_at.isoformat(),
            (row.cutoff_at or row.generated_at).isoformat(),
            row.source,
            row.market_date,
        )
        for row in predictions
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM premkt_predictions WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO premkt_predictions (
                scan_id,
                symbol,
                score,
                max_hold_days,
                entry_rationale,
                themes,
                filing_summary,
                generated_at,
                cutoff_at,
                source,
                market_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_premkt_predictions(
    database_path: Path,
    limit: int = 20,
    *,
    scan_id: str | None = None,
    market_date: str | None = None,
    prefer_reportable: bool = False,
) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        if market_date is not None:
            return connection.execute(
                """
                SELECT *
                FROM premkt_predictions
                WHERE market_date = ?
                ORDER BY score DESC, symbol ASC
                LIMIT ?
                """,
                (market_date, limit),
            ).fetchall()
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
            FROM premkt_predictions
            WHERE scan_id = ?
            ORDER BY score DESC, symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()


def insert_premarket_signals(
    database_path: Path,
    scan_id: str,
    signals: Iterable[PremarketSignal],
) -> None:
    rows = [
        (
            scan_id,
            signal.symbol,
            signal.premarket_high,
            signal.premarket_low,
            signal.premarket_close,
            signal.premarket_vwap,
            signal.premarket_rvol,
            signal.dollar_volume,
            signal.trade_count,
            signal.tps,
            signal.size_mean,
            signal.size_std,
            signal.size_cv,
            signal.spread_pct,
            int(signal.round_level_break),
            int(signal.tape_anomaly),
            signal.quality_score,
            json.dumps(signal.reasons),
            signal.created_at.isoformat(),
        )
        for signal in signals
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM premarket_signals WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO premarket_signals (
                scan_id,
                symbol,
                premarket_high,
                premarket_low,
                premarket_close,
                premarket_vwap,
                premarket_rvol,
                dollar_volume,
                trade_count,
                tps,
                size_mean,
                size_std,
                size_cv,
                spread_pct,
                round_level_break,
                tape_anomaly,
                quality_score,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_premarket_signals(
    database_path: Path,
    limit: int = 20,
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
            FROM premarket_signals
            WHERE scan_id = ?
            ORDER BY quality_score DESC, symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()
