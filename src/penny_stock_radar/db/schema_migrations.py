from __future__ import annotations

import sqlite3


def apply_schema_migrations(connection: sqlite3.Connection) -> None:
            _ensure_column(connection, "scan_runs", "market_date", "TEXT")
            _ensure_column(
                connection,
                "scan_runs",
                "snapshot_role",
                "TEXT NOT NULL DEFAULT 'live'",
            )
            _ensure_column(connection, "scan_runs", "point_in_time_tag", "TEXT")
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
                "paper_runs",
                "bucket",
                "TEXT NOT NULL DEFAULT 'predictor_weighted'",
            )
            _ensure_column(
                connection,
                "paper_runs",
                "total_transaction_cost",
                "REAL NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity",
                "bid_price",
                "REAL",
            )
            _ensure_column(connection, "paper_positions", "fees_paid_total", "REAL NOT NULL DEFAULT 0")
            _ensure_column(connection, "paper_orders", "requested_quantity", "INTEGER")
            _ensure_column(
                connection,
                "paper_orders",
                "remaining_quantity",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "fill_status",
                "TEXT NOT NULL DEFAULT 'FILLED'",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "transaction_cost",
                "REAL NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity",
                "ask_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "market_activity",
                "data_age_seconds",
                "REAL",
            )
            _ensure_column(
                connection,
                "market_activity",
                "has_live_trade",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity",
                "has_live_quote",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity_history",
                "bid_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "market_activity_history",
                "ask_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "market_activity_history",
                "data_age_seconds",
                "REAL",
            )
            _ensure_column(
                connection,
                "market_activity_history",
                "has_live_trade",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity_history",
                "has_live_quote",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "market_activity",
                "leader_persistence_score",
                "REAL NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "planned_stop_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "planned_risk_pct",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "partial_exit_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "strategy_bucket",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "fill_reference_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "fill_slippage_pct",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "day_regime",
                "TEXT",
            )
            _ensure_column(
                connection,
                "paper_positions",
                "watchlist_rank_at_entry",
                "INTEGER",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "strategy_bucket",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "planned_stop_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "planned_risk_pct",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "fill_reference_price",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "fill_slippage_pct",
                "REAL",
            )
            _ensure_column(connection, "paper_orders", "bar_volume", "REAL")
            _ensure_column(connection, "paper_orders", "bar_dollar_volume", "REAL")
            _ensure_column(connection, "paper_orders", "shares_pct_of_bar_volume", "REAL")
            _ensure_column(
                connection,
                "paper_orders",
                "notional_pct_of_bar_dollar_volume",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "estimated_capacity_at_1pct_volume",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "estimated_capacity_at_2pct_volume",
                "REAL",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "capacity_limited",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "participation_slippage_pct",
                "REAL NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "day_regime",
                "TEXT",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "watchlist_rank_at_entry",
                "INTEGER",
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
            _ensure_column(connection, "premkt_predictions", "cutoff_at", "TEXT")
            _ensure_column(
                connection,
                "premkt_predictions",
                "source",
                "TEXT NOT NULL DEFAULT 'premkt_prediction'",
            )
            _ensure_column(connection, "premkt_predictions", "market_date", "TEXT")


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
