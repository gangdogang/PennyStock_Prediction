from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import (
    FilingMatch,
    MarketActivity,
    PaperOrder,
    PaperPosition,
    PaperRunSnapshot,
    PaperTradingRun,
    PremarketSignal,
    PredictionOutcome,
    ReplayEvaluation,
    SessionDecision,
    SocialSignal,
    SnapshotRun,
    UniverseCandidate,
    WatchlistEntry,
)

EASTERN = ZoneInfo("America/New_York")
CORE_REPORTABLE_SECTIONS = (
    "universe",
    "watchlist",
    "premarket_signals",
    "session_decisions",
    "replay_reports",
)
SUPPLEMENTAL_REPORTABLE_SECTIONS = (
    "social_signals",
    "market_activity",
    "prediction_outcomes",
)
_SCAN_SECTION_TABLES = {
    "universe": "universe",
    "watchlist": "watchlist",
    "premarket_signals": "premarket_signals",
    "session_decisions": "session_decisions",
    "replay_reports": "replay_reports",
    "social_signals": "social_signals",
    "market_activity": "market_activity",
    "prediction_outcomes": "prediction_outcomes",
}


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


def resolve_market_phase(now: datetime | None = None) -> str:
    current = now.astimezone(EASTERN) if now is not None else datetime.now(EASTERN)
    if current.weekday() >= 5:
        return "closed"
    session_time = current.timetz().replace(tzinfo=None)
    if time(4, 0) <= session_time < time(9, 30):
        return "premarket"
    if time(9, 30) <= session_time < time(16, 0):
        return "regular"
    if time(16, 0) <= session_time < time(20, 0):
        return "afterhours"
    return "closed"


def fetch_scan_selection(
    database_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    if not database_path.exists():
        return _empty_scan_selection(now=now)
    try:
        with get_connection(database_path) as connection:
            return _fetch_scan_selection(connection, now=now)
    except sqlite3.Error:
        return _empty_scan_selection(now=now)


def fetch_latest_reportable_scan_id(database_path: Path) -> dict[str, str] | None:
    selection = fetch_scan_selection(database_path)
    scan_id = selection.get("selected_scan_id")
    created_at = selection.get("selected_created_at")
    if not scan_id or not created_at:
        return None
    return {
        "scan_id": str(scan_id),
        "created_at": str(created_at),
    }


def _empty_scan_selection(now: datetime | None = None) -> dict[str, object]:
    market_phase = resolve_market_phase(now)
    return {
        "selected_scan_id": None,
        "selected_created_at": None,
        "latest_scan_id": None,
        "latest_created_at": None,
        "is_fallback": False,
        "missing_core_sections": [],
        "missing_supplemental_sections": [],
        "market_phase": market_phase,
        "live_data_expected": market_phase in {"premarket", "regular"},
    }


def _fetch_scan_selection(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    market_phase = resolve_market_phase(now)
    latest_row = connection.execute(
        """
        SELECT scan_id, created_at
        FROM scan_runs
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if latest_row is None:
        return _empty_scan_selection(now=now)

    latest_scan_id = str(latest_row["scan_id"])
    latest_created_at = str(latest_row["created_at"])
    latest_missing_core, latest_missing_supplemental = _scan_missing_sections(
        connection,
        latest_scan_id,
    )

    selected_scan_id: str | None = None
    selected_created_at: str | None = None
    selected_missing_supplemental: list[str] = []
    for row in connection.execute(
        """
        SELECT scan_id, created_at
        FROM scan_runs
        ORDER BY created_at DESC
        """
    ).fetchall():
        scan_id = str(row["scan_id"])
        missing_core, missing_supplemental = _scan_missing_sections(connection, scan_id)
        if missing_core:
            continue
        selected_scan_id = scan_id
        selected_created_at = str(row["created_at"])
        selected_missing_supplemental = missing_supplemental
        break

    is_fallback = bool(
        selected_scan_id
        and latest_scan_id
        and selected_scan_id != latest_scan_id
    )
    if selected_scan_id is None:
        missing_core_sections = latest_missing_core
        missing_supplemental_sections = latest_missing_supplemental
    elif is_fallback:
        missing_core_sections = latest_missing_core
        missing_supplemental_sections = latest_missing_supplemental
    else:
        missing_core_sections = []
        missing_supplemental_sections = selected_missing_supplemental

    return {
        "selected_scan_id": selected_scan_id,
        "selected_created_at": selected_created_at,
        "latest_scan_id": latest_scan_id,
        "latest_created_at": latest_created_at,
        "is_fallback": is_fallback,
        "missing_core_sections": missing_core_sections,
        "missing_supplemental_sections": missing_supplemental_sections,
        "market_phase": market_phase,
        "live_data_expected": market_phase in {"premarket", "regular"},
    }


def _scan_missing_sections(
    connection: sqlite3.Connection,
    scan_id: str,
) -> tuple[list[str], list[str]]:
    section_state = _scan_section_presence(connection, scan_id)
    missing_core = [
        section for section in CORE_REPORTABLE_SECTIONS if not section_state[section]
    ]
    missing_supplemental = [
        section for section in SUPPLEMENTAL_REPORTABLE_SECTIONS if not section_state[section]
    ]
    return missing_core, missing_supplemental


def _scan_section_presence(connection: sqlite3.Connection, scan_id: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for section, table_name in _SCAN_SECTION_TABLES.items():
        row = connection.execute(
            f"""
            SELECT EXISTS(
                SELECT 1
                FROM {table_name}
                WHERE scan_id = ?
                LIMIT 1
            ) AS present
            """,
            (scan_id,),
        ).fetchone()
        result[section] = bool(row["present"]) if row is not None else False
    return result


def _resolve_scan_id(
    database_path: Path,
    *,
    scan_id: str | None,
    prefer_reportable: bool,
) -> str | None:
    if scan_id is not None:
        return scan_id
    if prefer_reportable:
        row = fetch_latest_reportable_scan_id(database_path)
    else:
        row = fetch_latest_scan_id(database_path)
    if row is None:
        return None
    return str(row["scan_id"])


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
                market_context_score REAL NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS market_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                market_phase TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                last_price REAL,
                previous_close REAL,
                pct_change REAL,
                volume REAL,
                dollar_volume REAL,
                trade_size INTEGER,
                spread_pct REAL,
                market_status TEXT,
                market_data_at TEXT,
                pct_rank INTEGER NOT NULL,
                volume_rank INTEGER NOT NULL,
                watchlist_rank INTEGER,
                watchlist_score REAL,
                predicted INTEGER NOT NULL DEFAULT 0,
                leader_persistence_score REAL NOT NULL DEFAULT 0,
                pullback_absorption_score REAL NOT NULL DEFAULT 0,
                trap_score REAL NOT NULL DEFAULT 0,
                behavioral_score REAL NOT NULL DEFAULT 0,
                analysis_label TEXT NOT NULL,
                analysis_score REAL NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_market_activity_scan_phase
            ON market_activity(scan_id, market_phase);

            CREATE TABLE IF NOT EXISTS market_activity_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                market_phase TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                last_price REAL,
                previous_close REAL,
                pct_change REAL,
                volume REAL,
                dollar_volume REAL,
                trade_size INTEGER,
                spread_pct REAL,
                market_status TEXT,
                market_data_at TEXT,
                pct_rank INTEGER NOT NULL,
                volume_rank INTEGER NOT NULL,
                watchlist_rank INTEGER,
                watchlist_score REAL,
                predicted INTEGER NOT NULL DEFAULT 0,
                leader_persistence_score REAL NOT NULL DEFAULT 0,
                pullback_absorption_score REAL NOT NULL DEFAULT 0,
                trap_score REAL NOT NULL DEFAULT 0,
                behavioral_score REAL NOT NULL DEFAULT 0,
                analysis_label TEXT NOT NULL,
                analysis_score REAL NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_market_activity_history_phase_symbol_created
            ON market_activity_history(market_phase, symbol, created_at DESC);

            CREATE TABLE IF NOT EXISTS prediction_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                market_phase TEXT NOT NULL,
                symbol TEXT NOT NULL,
                predicted INTEGER NOT NULL DEFAULT 0,
                watchlist_rank INTEGER,
                watchlist_score REAL,
                pct_rank INTEGER,
                volume_rank INTEGER,
                pct_change REAL,
                volume REAL,
                dollar_volume REAL,
                analysis_label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scan_id) REFERENCES scan_runs(scan_id)
            );

            CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_scan_phase
            ON prediction_outcomes(scan_id, market_phase);

            CREATE TABLE IF NOT EXISTS paper_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                strategy_name TEXT NOT NULL,
                status TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                cash_balance REAL NOT NULL,
                equity REAL NOT NULL,
                equity_peak REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_return_pct REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                closed_trade_count INTEGER NOT NULL DEFAULT 0,
                winning_trade_count INTEGER NOT NULL DEFAULT 0,
                losing_trade_count INTEGER NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0,
                gross_profit REAL NOT NULL DEFAULT 0,
                gross_loss REAL NOT NULL DEFAULT 0,
                profit_factor REAL NOT NULL DEFAULT 0,
                average_win REAL NOT NULL DEFAULT 0,
                average_loss REAL NOT NULL DEFAULT 0,
                reward_risk_ratio REAL NOT NULL DEFAULT 0,
                last_phase TEXT,
                last_market_date TEXT,
                notes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_paper_runs_status
            ON paper_runs(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_phase TEXT NOT NULL,
                entry_label TEXT,
                exit_reason TEXT,
                quantity INTEGER NOT NULL,
                average_entry_price REAL NOT NULL,
                last_price REAL,
                cost_basis REAL NOT NULL,
                market_value REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_pnl REAL NOT NULL,
                stop_price REAL,
                highest_price REAL,
                add_count INTEGER NOT NULL DEFAULT 0,
                entry_reasons TEXT NOT NULL DEFAULT '[]',
                exit_reasons TEXT NOT NULL DEFAULT '[]',
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_paper_positions_run_status
            ON paper_positions(run_id, status, symbol);

            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                position_id TEXT,
                symbol TEXT NOT NULL,
                market_phase TEXT NOT NULL,
                action TEXT NOT NULL,
                intent TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                notional REAL NOT NULL,
                analysis_label TEXT,
                analysis_score REAL,
                reasons TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                realized_pnl REAL,
                realized_pnl_pct REAL
            );

            CREATE INDEX IF NOT EXISTS idx_paper_orders_run_created
            ON paper_orders(run_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS paper_run_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                market_phase TEXT NOT NULL,
                cash_balance REAL NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_return_pct REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                open_position_count INTEGER NOT NULL,
                closed_trade_count INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                reward_risk_ratio REAL NOT NULL,
                notes TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_paper_run_snapshots_run_created
            ON paper_run_snapshots(run_id, created_at DESC);
            """
        )
        _ensure_column(connection, "filings", "item_numbers", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "filings", "themes", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "watchlist", "social_score", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "watchlist", "themes", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(
            connection,
            "watchlist",
            "market_context_score",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "market_activity",
            "leader_persistence_score",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "market_activity",
            "pullback_absorption_score",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "market_activity",
            "trap_score",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_column(
            connection,
            "market_activity",
            "behavioral_score",
            "REAL NOT NULL DEFAULT 0",
        )


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


def fetch_latest_session_decisions(
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
            FROM session_decisions
            WHERE scan_id = ?
            ORDER BY
                CASE decision WHEN 'ENTER' THEN 0 WHEN 'WATCH' THEN 1 ELSE 2 END,
                extension_z ASC,
                symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
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


def fetch_latest_replay_report(
    database_path: Path,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> sqlite3.Row | None:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return None
        return connection.execute(
            """
            SELECT *
            FROM replay_reports
            WHERE scan_id = ?
            LIMIT 1
            """,
            (target_scan_id,),
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


def fetch_latest_social_signals(
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
            FROM social_signals
            WHERE scan_id = ?
            ORDER BY social_score DESC, symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()


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
            row.previous_close,
            row.pct_change,
            row.volume,
            row.dollar_volume,
            row.trade_size,
            row.spread_pct,
            row.market_status,
            row.market_data_at.isoformat() if row.market_data_at else None,
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
                previous_close,
                pct_change,
                volume,
                dollar_volume,
                trade_size,
                spread_pct,
                market_status,
                market_data_at,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                previous_close,
                pct_change,
                volume,
                dollar_volume,
                trade_size,
                spread_pct,
                market_status,
                market_data_at,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def create_paper_trading_run(
    database_path: Path,
    strategy_name: str,
    initial_capital: float,
    notes: list[str] | None = None,
) -> PaperTradingRun:
    now = PaperTradingRun(
        run_id=str(uuid.uuid4()),
        strategy_name=strategy_name,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                strategy_name = excluded.strategy_name,
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
    query += " ORDER BY updated_at DESC LIMIT 1"
    with get_connection(database_path) as connection:
        row = connection.execute(query, tuple(params)).fetchone()
    return _paper_run_from_row(row)


def fetch_latest_paper_trading_run(
    database_path: Path,
    strategy_name: str | None = None,
) -> PaperTradingRun | None:
    query = """
        SELECT *
        FROM paper_runs
    """
    params: list[object] = []
    if strategy_name:
        query += " WHERE strategy_name = ?"
        params.append(strategy_name)
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
            ORDER BY strategy_name ASC, updated_at DESC
            """
        ).fetchall()
    latest_by_strategy: list[PaperTradingRun] = []
    seen: set[str] = set()
    for row in rows:
        strategy_name = str(row["strategy_name"])
        if strategy_name in seen:
            continue
        seen.add(strategy_name)
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
            row.highest_price,
            row.add_count,
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
                highest_price,
                add_count,
                entry_reasons,
                exit_reasons,
                opened_at,
                updated_at,
                closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                highest_price = excluded.highest_price,
                add_count = excluded.add_count,
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
            row.price,
            row.notional,
            row.analysis_label,
            row.analysis_score,
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
                price,
                notional,
                analysis_label,
                analysis_score,
                reasons,
                created_at,
                realized_pnl,
                realized_pnl_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _paper_run_from_row(row: sqlite3.Row | None) -> PaperTradingRun | None:
    if row is None:
        return None
    return PaperTradingRun(
        run_id=row["run_id"],
        strategy_name=row["strategy_name"],
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
        highest_price=float(row["highest_price"]) if row["highest_price"] is not None else None,
        add_count=int(row["add_count"]),
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
        price=float(row["price"]),
        notional=float(row["notional"]),
        analysis_label=row["analysis_label"],
        analysis_score=float(row["analysis_score"]) if row["analysis_score"] is not None else None,
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


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value in {None, ""}:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _safe_json_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        decoded = json.loads(str(value))
    except Exception:
        return [str(value)]
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return [str(decoded)]


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
