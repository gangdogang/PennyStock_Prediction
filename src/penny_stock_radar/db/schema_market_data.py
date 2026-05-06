from __future__ import annotations

import sqlite3

MARKET_DATA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS historical_l1_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    market_date TEXT NOT NULL,
    quote_at TEXT NOT NULL,
    bid_price REAL,
    ask_price REAL,
    bid_exchange TEXT,
    ask_exchange TEXT,
    last_price REAL,
    source TEXT NOT NULL,
    subscription_continuous INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historical_l1_quotes_symbol_date
ON historical_l1_quotes(symbol, market_date, quote_at);

CREATE TABLE IF NOT EXISTS historical_minute_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    market_date TEXT NOT NULL,
    market_phase TEXT NOT NULL,
    bar_at TEXT NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    close_price REAL NOT NULL,
    volume REAL NOT NULL,
    bid_price REAL,
    ask_price REAL,
    spread_pct REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historical_minute_bars_symbol_date
ON historical_minute_bars(symbol, market_date, bar_at);

CREATE INDEX IF NOT EXISTS idx_historical_minute_bars_date_symbol_time
ON historical_minute_bars(market_date, symbol, bar_at);

CREATE TABLE IF NOT EXISTS historical_halt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    market_date TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT,
    reason TEXT,
    resume_price REAL,
    source TEXT NOT NULL,
    inferred INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historical_halt_events_symbol_date
ON historical_halt_events(symbol, market_date, start_at);

CREATE TABLE IF NOT EXISTS historical_coverage_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_date TEXT NOT NULL,
    dataset_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    expected_symbol_count INTEGER NOT NULL,
    covered_symbol_count INTEGER NOT NULL,
    symbol_coverage_pct REAL NOT NULL,
    expected_interval_count INTEGER NOT NULL,
    covered_interval_count INTEGER NOT NULL,
    interval_coverage_pct REAL NOT NULL,
    tier1_continuous_symbol_count INTEGER,
    tier2_rotation_symbol_count INTEGER,
    rotation_gap_seconds_p90 REAL,
    notes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historical_coverage_reports_market_date_kind
ON historical_coverage_reports(market_date, dataset_kind, created_at DESC);

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
"""


def apply_market_data_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(MARKET_DATA_SCHEMA_SQL)
