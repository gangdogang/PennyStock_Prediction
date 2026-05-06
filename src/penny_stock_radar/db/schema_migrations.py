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
                "paper_positions",
                "pyramid_state",
                "TEXT",
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
                "paper_orders",
                "leg_index",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "paper_orders",
                "setup_id",
                "TEXT NOT NULL DEFAULT 'legacy_momentum'",
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
            _ensure_column(connection, "historical_l1_quotes", "bid_exchange", "TEXT")
            _ensure_column(connection, "historical_l1_quotes", "ask_exchange", "TEXT")
            _ensure_column(
                connection,
                "historical_l1_quotes",
                "subscription_continuous",
                "INTEGER NOT NULL DEFAULT 1",
            )
            _ensure_column(
                connection,
                "historical_coverage_reports",
                "tier1_continuous_symbol_count",
                "INTEGER",
            )
            _ensure_column(
                connection,
                "historical_coverage_reports",
                "tier2_rotation_symbol_count",
                "INTEGER",
            )
            _ensure_column(
                connection,
                "historical_coverage_reports",
                "rotation_gap_seconds_p90",
                "REAL",
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_historical_minute_bars_date_symbol_time
                ON historical_minute_bars(market_date, symbol, bar_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS corporate_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_record_id TEXT,
                    action_category TEXT NOT NULL,
                    action_subtype TEXT NOT NULL,
                    symbol TEXT,
                    old_symbol TEXT,
                    new_symbol TEXT,
                    old_name TEXT,
                    new_name TEXT,
                    effective_date TEXT,
                    event_code TEXT,
                    event_reason TEXT,
                    raw_payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(
                        source,
                        source_record_id,
                        action_category,
                        action_subtype,
                        symbol,
                        effective_date
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_corporate_actions_effective_symbol
                ON corporate_actions(effective_date, symbol, action_category)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_corporate_actions_source_record
                ON corporate_actions(source, source_record_id)
                """
            )


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
