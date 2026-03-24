from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Iterable

from .models import (
    FilingMatch,
    PremarketSignal,
    ReplayEvaluation,
    SessionDecision,
    SocialSignal,
    SnapshotRun,
    UniverseCandidate,
    WatchlistEntry,
)


def get_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Some SQLite environments do not allow changing the journal mode.
        pass
    return connection


def init_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                scan_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                symbol_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS universe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                company_name TEXT NOT NULL,
                exchange TEXT NOT NULL,
                quote_type TEXT,
                price REAL,
                market_cap INTEGER,
                float_shares INTEGER,
                shares_outstanding INTEGER,
                sector TEXT,
                industry TEXT,
                recent_filings_summary TEXT,
                passed_filters INTEGER NOT NULL DEFAULT 0,
                filter_reasons TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_universe_scan
            ON universe(scan_id);

            CREATE INDEX IF NOT EXISTS idx_universe_symbol
            ON universe(symbol);

            CREATE TABLE IF NOT EXISTS filings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                cik TEXT NOT NULL,
                form TEXT NOT NULL,
                filing_date TEXT NOT NULL,
                acceptance_datetime TEXT,
                accession_number TEXT NOT NULL,
                primary_document TEXT,
                primary_doc_description TEXT,
                filing_url TEXT NOT NULL,
                item_numbers TEXT NOT NULL DEFAULT '[]',
                themes TEXT NOT NULL DEFAULT '[]',
                matched_keywords TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_filings_scan_symbol
            ON filings(scan_id, symbol);

            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                total_score REAL NOT NULL,
                catalyst_score REAL NOT NULL,
                technical_score REAL NOT NULL,
                sympathy_score REAL NOT NULL,
                low_float_bonus REAL NOT NULL,
                social_score REAL NOT NULL DEFAULT 0,
                themes TEXT NOT NULL DEFAULT '[]',
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_watchlist_scan_symbol
            ON watchlist(scan_id, symbol);

            CREATE TABLE IF NOT EXISTS premarket_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                premarket_high REAL NOT NULL,
                premarket_low REAL NOT NULL,
                premarket_close REAL NOT NULL,
                premarket_vwap REAL NOT NULL,
                premarket_rvol REAL NOT NULL,
                dollar_volume REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                tps REAL NOT NULL,
                size_mean REAL NOT NULL,
                size_std REAL NOT NULL,
                size_cv REAL NOT NULL,
                spread_pct REAL NOT NULL,
                round_level_break INTEGER NOT NULL,
                tape_anomaly INTEGER NOT NULL,
                quality_score REAL NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_premarket_scan_symbol
            ON premarket_signals(scan_id, symbol);

            CREATE TABLE IF NOT EXISTS session_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                anchored_vwap REAL NOT NULL,
                opening_range_high REAL NOT NULL,
                opening_range_low REAL NOT NULL,
                regular_high REAL NOT NULL,
                regular_low REAL NOT NULL,
                regular_close REAL NOT NULL,
                extension_z REAL NOT NULL,
                mfe_pct REAL NOT NULL,
                mae_pct REAL NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_session_decisions_scan_symbol
            ON session_decisions(scan_id, symbol);

            CREATE TABLE IF NOT EXISTS replay_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                label TEXT NOT NULL,
                expectancy REAL NOT NULL,
                profit_factor REAL NOT NULL,
                precision_at_k REAL NOT NULL,
                average_mfe_pct REAL NOT NULL,
                average_mae_pct REAL NOT NULL,
                symbol_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE TABLE IF NOT EXISTS social_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                mention_count INTEGER NOT NULL,
                mention_velocity REAL NOT NULL,
                unique_authors INTEGER NOT NULL,
                cross_platform_count INTEGER NOT NULL,
                engagement_total REAL NOT NULL,
                social_score REAL NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_social_signals_scan_symbol
            ON social_signals(scan_id, symbol);
            """
        )
        _ensure_column(connection, "filings", "item_numbers", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "filings", "themes", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "watchlist", "social_score", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "watchlist", "themes", "TEXT NOT NULL DEFAULT '[]'")


def create_snapshot_run(
    database_path: Path,
    source: str,
    symbol_count: int,
) -> SnapshotRun:
    snapshot = SnapshotRun(
        snapshot_id=str(uuid.uuid4()),
        source=source,
        symbol_count=symbol_count,
    )
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO scan_runs (scan_id, source, symbol_count, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.source,
                snapshot.symbol_count,
                snapshot.created_at.isoformat(),
            ),
        )
    return snapshot


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


def fetch_latest_candidates(database_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        snapshot_row = fetch_latest_scan_id(database_path)
        if snapshot_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM universe
            WHERE scan_id = ?
            ORDER BY passed_filters DESC, price ASC
            LIMIT ?
            """,
            (snapshot_row["scan_id"], limit),
        ).fetchall()


def fetch_latest_passed_universe(database_path: Path) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        scan_row = fetch_latest_scan_id(database_path)
        if scan_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM universe
            WHERE scan_id = ? AND passed_filters = 1
            ORDER BY price ASC
            """,
            (scan_row["scan_id"],),
        ).fetchall()


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
                low_float_bonus,
                social_score,
                themes,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_watchlist(database_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        scan_row = fetch_latest_scan_id(database_path)
        if scan_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM watchlist
            WHERE scan_id = ?
            ORDER BY total_score DESC, symbol ASC
            LIMIT ?
            """,
            (scan_row["scan_id"], limit),
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


def fetch_latest_premarket_signals(database_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        scan_row = fetch_latest_scan_id(database_path)
        if scan_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM premarket_signals
            WHERE scan_id = ?
            ORDER BY quality_score DESC, symbol ASC
            LIMIT ?
            """,
            (scan_row["scan_id"], limit),
        ).fetchall()


def insert_session_decisions(
    database_path: Path,
    scan_id: str,
    decisions: Iterable[SessionDecision],
) -> None:
    rows = [
        (
            scan_id,
            decision.symbol,
            decision.decision,
            decision.anchored_vwap,
            decision.opening_range_high,
            decision.opening_range_low,
            decision.regular_high,
            decision.regular_low,
            decision.regular_close,
            decision.extension_z,
            decision.mfe_pct,
            decision.mae_pct,
            json.dumps(decision.reasons),
            decision.created_at.isoformat(),
        )
        for decision in decisions
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM session_decisions WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO session_decisions (
                scan_id,
                symbol,
                decision,
                anchored_vwap,
                opening_range_high,
                opening_range_low,
                regular_high,
                regular_low,
                regular_close,
                extension_z,
                mfe_pct,
                mae_pct,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_session_decisions(database_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        scan_row = fetch_latest_scan_id(database_path)
        if scan_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM session_decisions
            WHERE scan_id = ?
            ORDER BY
                CASE decision WHEN 'ENTER' THEN 0 WHEN 'WATCH' THEN 1 ELSE 2 END,
                extension_z ASC,
                symbol ASC
            LIMIT ?
            """,
            (scan_row["scan_id"], limit),
        ).fetchall()


def insert_replay_report(
    database_path: Path,
    scan_id: str,
    report: ReplayEvaluation,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM replay_reports WHERE scan_id = ?", (scan_id,))
        connection.execute(
            """
            INSERT INTO replay_reports (
                scan_id,
                label,
                expectancy,
                profit_factor,
                precision_at_k,
                average_mfe_pct,
                average_mae_pct,
                symbol_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                report.label,
                report.expectancy,
                report.profit_factor,
                report.precision_at_k,
                report.average_mfe_pct,
                report.average_mae_pct,
                report.symbol_count,
                report.created_at.isoformat(),
            ),
        )


def fetch_latest_replay_report(database_path: Path) -> sqlite3.Row | None:
    with get_connection(database_path) as connection:
        scan_row = fetch_latest_scan_id(database_path)
        if scan_row is None:
            return None
        return connection.execute(
            """
            SELECT *
            FROM replay_reports
            WHERE scan_id = ?
            LIMIT 1
            """,
            (scan_row["scan_id"],),
        ).fetchone()


def insert_social_signals(
    database_path: Path,
    scan_id: str,
    signals: Iterable[SocialSignal],
) -> None:
    rows = [
        (
            scan_id,
            signal.symbol,
            signal.mention_count,
            signal.mention_velocity,
            signal.unique_authors,
            signal.cross_platform_count,
            signal.engagement_total,
            signal.social_score,
            json.dumps(signal.reasons),
            signal.created_at.isoformat(),
        )
        for signal in signals
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM social_signals WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO social_signals (
                scan_id,
                symbol,
                mention_count,
                mention_velocity,
                unique_authors,
                cross_platform_count,
                engagement_total,
                social_score,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_social_signals(database_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        try:
            scan_row = connection.execute(
                """
                SELECT scan_id
                FROM social_signals
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            return []
        if scan_row is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM social_signals
            WHERE scan_id = ?
            ORDER BY social_score DESC, symbol ASC
            LIMIT ?
            """,
            (scan_row["scan_id"], limit),
        ).fetchall()


def fetch_latest_scan_id(database_path: Path) -> sqlite3.Row | None:
    with get_connection(database_path) as connection:
        return connection.execute(
            """
            SELECT scan_id
            FROM scan_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        try:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


initialize_database = init_database
