from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from ..models import FilingMatch, SnapshotRun, UniverseCandidate, WatchlistEntry
from .connection import _resolve_scan_id, get_connection

def create_snapshot_run(
    database_path: Path,
    source: str,
    symbol_count: int,
    *,
    market_date: str | None = None,
    snapshot_role: str = "live",
    point_in_time_tag: str | None = None,
) -> SnapshotRun:
    snapshot = SnapshotRun(
        snapshot_id=str(uuid.uuid4()),
        source=source,
        symbol_count=symbol_count,
        market_date=market_date,
        snapshot_role=snapshot_role,
        point_in_time_tag=point_in_time_tag,
    )
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO scan_runs (
                scan_id,
                source,
                symbol_count,
                market_date,
                snapshot_role,
                point_in_time_tag,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.source,
                snapshot.symbol_count,
                snapshot.market_date,
                snapshot.snapshot_role,
                snapshot.point_in_time_tag,
                snapshot.created_at.isoformat(),
            ),
        )
    return snapshot


def tag_scan_run_point_in_time(
    database_path: Path,
    scan_id: str,
    *,
    market_date: str,
    point_in_time_tag: str,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            UPDATE scan_runs
            SET market_date = ?,
                snapshot_role = 'point_in_time',
                point_in_time_tag = ?
            WHERE scan_id = ?
            """,
            (market_date, point_in_time_tag, scan_id),
        )


def insert_universe_candidates(
    database_path: Path,
    snapshot_id: str,
    candidates: Iterable[UniverseCandidate],
) -> None:
    rows = [
        (
            snapshot_id,
            candidate.symbol,
            candidate.company_name,
            candidate.exchange,
            candidate.quote_type,
            candidate.price,
            candidate.market_cap,
            candidate.float_shares,
            candidate.shares_outstanding,
            candidate.sector,
            candidate.industry,
            candidate.recent_filings_summary,
            int(candidate.passed_filters),
            json.dumps(candidate.filter_reasons),
            candidate.last_updated_at.isoformat(),
        )
        for candidate in candidates
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO universe (
                scan_id,
                symbol,
                company_name,
                exchange,
                quote_type,
                price,
                market_cap,
                float_shares,
                shares_outstanding,
                sector,
                industry,
                recent_filings_summary,
                passed_filters,
                filter_reasons,
                last_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_candidates(
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
            FROM universe
            WHERE scan_id = ?
            ORDER BY passed_filters DESC, price ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()


def fetch_latest_passed_universe(
    database_path: Path,
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
            FROM universe
            WHERE scan_id = ? AND passed_filters = 1
            ORDER BY price ASC
            """,
            (target_scan_id,),
        ).fetchall()


def fetch_point_in_time_scan_id(
    database_path: Path,
    market_date: str,
) -> sqlite3.Row | None:
    with get_connection(database_path) as connection:
        return connection.execute(
            """
            SELECT scan_id, created_at, market_date, snapshot_role, point_in_time_tag
            FROM scan_runs
            WHERE snapshot_role = 'point_in_time'
              AND market_date IS NOT NULL
              AND market_date <= ?
            ORDER BY market_date DESC, created_at DESC
            LIMIT 1
            """,
            (market_date,),
        ).fetchone()


def fetch_passed_universe_for_market_date(
    database_path: Path,
    market_date: str,
) -> list[sqlite3.Row]:
    target_scan = fetch_point_in_time_scan_id(database_path, market_date)
    if target_scan is None:
        return []
    return fetch_latest_passed_universe(
        database_path,
        scan_id=str(target_scan["scan_id"]),
        prefer_reportable=False,
    )


def insert_filings(
    database_path: Path,
    scan_id: str,
    filings: Iterable[FilingMatch],
) -> None:
    rows = [
        (
            scan_id,
            filing.symbol,
            filing.cik,
            filing.form,
            filing.filing_date,
            filing.acceptance_datetime,
            filing.accession_number,
            filing.primary_document,
            filing.primary_doc_description,
            filing.filing_url,
            json.dumps(filing.item_numbers),
            json.dumps(filing.themes),
            json.dumps(filing.matched_keywords),
            filing.summary,
            filing.created_at.isoformat(),
        )
        for filing in filings
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM filings WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO filings (
                scan_id,
                symbol,
                cik,
                form,
                filing_date,
                acceptance_datetime,
                accession_number,
                primary_document,
                primary_doc_description,
                filing_url,
                item_numbers,
                themes,
                matched_keywords,
                summary,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_filings(
    database_path: Path,
    limit: int = 100,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
    symbols: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return []
        query = """
            SELECT *
            FROM filings
            WHERE scan_id = ?
        """
        params: list[object] = [target_scan_id]
        if symbols:
            placeholders = ", ".join("?" for _ in symbols)
            query += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        query += " ORDER BY created_at DESC, symbol ASC LIMIT ?"
        params.append(limit)
        return connection.execute(query, tuple(params)).fetchall()


def update_universe_filing_summaries(
    database_path: Path,
    scan_id: str,
    summaries: dict[str, str],
) -> None:
    if not summaries:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            UPDATE universe
            SET recent_filings_summary = ?
            WHERE scan_id = ? AND symbol = ?
            """,
            [(summary, scan_id, symbol) for symbol, summary in summaries.items()],
        )


def insert_watchlist(
    database_path: Path,
    scan_id: str,
    entries: Iterable[WatchlistEntry],
) -> None:
    rows = [
        (
            scan_id,
            entry.symbol,
            entry.total_score,
            entry.catalyst_score,
            entry.technical_score,
            entry.sympathy_score,
            entry.market_context_score,
            entry.low_float_bonus,
            entry.social_score,
            json.dumps(entry.themes),
            json.dumps(entry.reasons),
            entry.created_at.isoformat(),
        )
        for entry in entries
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM watchlist WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO watchlist (
                scan_id,
                symbol,
                total_score,
                catalyst_score,
                technical_score,
                sympathy_score,
                market_context_score,
                low_float_bonus,
                social_score,
                themes,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_watchlist(
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
            FROM watchlist
            WHERE scan_id = ?
            ORDER BY total_score DESC, symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()
