from __future__ import annotations

import sqlite3

TRADING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    bucket TEXT NOT NULL DEFAULT 'predictor_weighted',
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
    total_transaction_cost REAL NOT NULL DEFAULT 0,
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
    planned_stop_price REAL,
    planned_risk_pct REAL,
    highest_price REAL,
    add_count INTEGER NOT NULL DEFAULT 0,
    partial_exit_count INTEGER NOT NULL DEFAULT 0,
    strategy_bucket TEXT NOT NULL DEFAULT '',
    fill_reference_price REAL,
    fill_slippage_pct REAL,
    day_regime TEXT,
    watchlist_rank_at_entry INTEGER,
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
    requested_quantity INTEGER,
    remaining_quantity INTEGER NOT NULL DEFAULT 0,
    fill_status TEXT NOT NULL DEFAULT 'FILLED',
    price REAL NOT NULL,
    notional REAL NOT NULL,
    transaction_cost REAL NOT NULL DEFAULT 0,
    strategy_bucket TEXT NOT NULL DEFAULT '',
    analysis_label TEXT,
    analysis_score REAL,
    planned_stop_price REAL,
    planned_risk_pct REAL,
    fill_reference_price REAL,
    fill_slippage_pct REAL,
    bar_volume REAL,
    bar_dollar_volume REAL,
    shares_pct_of_bar_volume REAL,
    notional_pct_of_bar_dollar_volume REAL,
    estimated_capacity_at_1pct_volume REAL,
    estimated_capacity_at_2pct_volume REAL,
    capacity_limited INTEGER NOT NULL DEFAULT 0,
    participation_slippage_pct REAL NOT NULL DEFAULT 0,
    day_regime TEXT,
    watchlist_rank_at_entry INTEGER,
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

CREATE TABLE IF NOT EXISTS execution_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL UNIQUE,
    broker_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    remaining_quantity INTEGER NOT NULL DEFAULT 0,
    limit_price REAL,
    avg_fill_price REAL,
    exchange_code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    strategy_bucket TEXT NOT NULL DEFAULT '',
    market_phase TEXT NOT NULL DEFAULT '',
    order_type TEXT NOT NULL DEFAULT 'limit',
    broker_order_id TEXT,
    original_broker_order_id TEXT,
    request_payload TEXT NOT NULL DEFAULT '{}',
    response_payload TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '[]',
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_orders_broker_account
ON execution_orders(broker_name, account_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_orders_symbol
ON execution_orders(symbol, updated_at DESC);

CREATE TABLE IF NOT EXISTS execution_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange_code TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL,
    available_quantity INTEGER,
    average_price REAL,
    market_price REAL,
    market_value REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    raw_payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(broker_name, account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_execution_positions_broker_account
ON execution_positions(broker_name, account_id, symbol);

CREATE TABLE IF NOT EXISTS execution_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cash_balance REAL,
    buying_power REAL,
    total_equity REAL,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(broker_name, account_id)
);
"""


def apply_trading_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(TRADING_SCHEMA_SQL)
