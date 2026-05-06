from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import (
    HistoricalCoverageReport,
    HistoricalHaltEvent,
    HistoricalL1Quote,
    HistoricalMinuteBar,
)
from .connection import get_connection

_SQLITE_PARAMETER_LIMIT = 999
_BULK_SYMBOL_CHUNK_SIZE = 900


def insert_historical_l1_quotes(
    database_path: Path,
    quotes: Iterable[HistoricalL1Quote],
) -> None:
    rows = [
        (
            quote.symbol,
            quote.market_date,
            quote.timestamp.isoformat(),
            quote.bid_price,
            quote.ask_price,
            quote.bid_exchange,
            quote.ask_exchange,
            quote.last_price,
            quote.source,
            int(bool(quote.subscription_continuous)),
            quote.created_at.isoformat(),
        )
        for quote in quotes
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO historical_l1_quotes (
                symbol,
                market_date,
                quote_at,
                bid_price,
                ask_price,
                bid_exchange,
                ask_exchange,
                last_price,
                source,
                subscription_continuous,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_historical_l1_quotes(
    database_path: Path,
    *,
    market_date: str,
    symbol: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM historical_l1_quotes
        WHERE market_date = ?
    """
    params: list[object] = [market_date]
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY symbol ASC, quote_at ASC"
    with get_connection(database_path) as connection:
        return connection.execute(query, tuple(params)).fetchall()


def insert_historical_minute_bars(
    database_path: Path,
    bars: Iterable[HistoricalMinuteBar],
) -> None:
    rows = [
        (
            bar.symbol,
            bar.market_date,
            bar.market_phase,
            bar.timestamp.isoformat(),
            bar.open_price,
            bar.high_price,
            bar.low_price,
            bar.close_price,
            bar.volume,
            bar.bid_price,
            bar.ask_price,
            bar.spread_pct,
            bar.source,
            bar.created_at.isoformat(),
        )
        for bar in bars
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO historical_minute_bars (
                symbol,
                market_date,
                market_phase,
                bar_at,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                bid_price,
                ask_price,
                spread_pct,
                source,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_historical_minute_bars(
    database_path: Path,
    *,
    market_date: str,
    symbol: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM historical_minute_bars
        WHERE market_date = ?
    """
    params: list[object] = [market_date]
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY symbol ASC, bar_at ASC"
    with get_connection(database_path) as connection:
        return connection.execute(query, tuple(params)).fetchall()


def fetch_historical_minute_bars_for_symbols(
    database_path: Path,
    *,
    market_date: str,
    symbols: Iterable[str],
) -> list[sqlite3.Row]:
    normalized_symbols: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol).strip().upper()
        if normalized:
            normalized_symbols.add(normalized)
    requested_symbols = sorted(normalized_symbols)
    if not requested_symbols:
        return []

    rows_by_symbol: dict[str, list[sqlite3.Row]] = {}
    max_symbols_per_query = min(_BULK_SYMBOL_CHUNK_SIZE, _SQLITE_PARAMETER_LIMIT - 1)
    with get_connection(database_path) as connection:
        for start in range(0, len(requested_symbols), max_symbols_per_query):
            chunk = requested_symbols[start : start + max_symbols_per_query]
            placeholders = ", ".join("?" for _ in chunk)
            chunk_rows = connection.execute(
                f"""
                SELECT *
                FROM historical_minute_bars
                WHERE market_date = ?
                  AND symbol IN ({placeholders})
                ORDER BY symbol ASC, bar_at ASC
                """,
                (market_date, *chunk),
            ).fetchall()
            for row in chunk_rows:
                rows_by_symbol.setdefault(str(row["symbol"]).upper(), []).append(row)
    return [
        row
        for symbol in sorted(rows_by_symbol)
        for row in rows_by_symbol[symbol]
    ]


def insert_historical_halt_events(
    database_path: Path,
    events: Iterable[HistoricalHaltEvent],
) -> None:
    rows = [
        (
            event.symbol,
            event.market_date,
            event.start_at.isoformat(),
            event.end_at.isoformat() if event.end_at else None,
            event.reason,
            event.resume_price,
            event.source,
            int(event.inferred),
            json.dumps(event.notes),
            event.created_at.isoformat(),
        )
        for event in events
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO historical_halt_events (
                symbol,
                market_date,
                start_at,
                end_at,
                reason,
                resume_price,
                source,
                inferred,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_historical_halt_events(
    database_path: Path,
    *,
    market_date: str,
    symbol: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM historical_halt_events
        WHERE market_date = ?
    """
    params: list[object] = [market_date]
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY symbol ASC, start_at ASC"
    with get_connection(database_path) as connection:
        return connection.execute(query, tuple(params)).fetchall()


def insert_historical_coverage_report(
    database_path: Path,
    report: HistoricalCoverageReport,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO historical_coverage_reports (
                market_date,
                dataset_kind,
                source,
                expected_symbol_count,
                covered_symbol_count,
                symbol_coverage_pct,
                expected_interval_count,
                covered_interval_count,
                interval_coverage_pct,
                tier1_continuous_symbol_count,
                tier2_rotation_symbol_count,
                rotation_gap_seconds_p90,
                notes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.market_date,
                report.dataset_kind,
                report.source,
                report.expected_symbol_count,
                report.covered_symbol_count,
                report.symbol_coverage_pct,
                report.expected_interval_count,
                report.covered_interval_count,
                report.interval_coverage_pct,
                report.tier1_continuous_symbol_count,
                report.tier2_rotation_symbol_count,
                report.rotation_gap_seconds_p90,
                json.dumps(report.notes),
                report.created_at.isoformat(),
            ),
        )


def fetch_historical_coverage_reports(
    database_path: Path,
    *,
    market_date: str | None = None,
    dataset_kind: str | None = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT *
        FROM historical_coverage_reports
        WHERE 1 = 1
    """
    params: list[object] = []
    if market_date is not None:
        query += " AND market_date = ?"
        params.append(market_date)
    if dataset_kind is not None:
        query += " AND dataset_kind = ?"
        params.append(dataset_kind)
    query += " ORDER BY created_at DESC, market_date DESC"
    with get_connection(database_path) as connection:
        return connection.execute(query, tuple(params)).fetchall()
