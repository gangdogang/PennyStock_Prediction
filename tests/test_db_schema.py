from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.conftest import load_first_module


DB_CANDIDATES = (
    "penny_stock_radar.db",
    "db",
)


def test_initialize_database_creates_expected_tables(tmp_path: Path) -> None:
    module = load_first_module(DB_CANDIDATES)

    init_fn = getattr(module, "initialize_database", None) or getattr(module, "init_db", None)
    assert callable(init_fn), (
        "Milestone 1 should expose initialize_database(path) or init_db(path)."
    )

    db_path = tmp_path / "penny_stock.sqlite3"
    init_fn(db_path)

    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }

    expected_tables = {
        "universe",
        "scan_runs",
        "premkt_predictions",
        "historical_l1_quotes",
        "historical_minute_bars",
        "historical_halt_events",
        "historical_coverage_reports",
        "corporate_actions",
        "execution_orders",
        "execution_positions",
        "execution_accounts",
    }
    assert tables & expected_tables, "Database should create at least one core Milestone 1 table."
    assert "universe" in tables


def test_initialize_database_indexes_historical_minute_bars_by_date_symbol_time(tmp_path: Path) -> None:
    module = load_first_module(DB_CANDIDATES)
    init_fn = getattr(module, "initialize_database", None) or getattr(module, "init_db", None)
    assert callable(init_fn)

    db_path = tmp_path / "penny_stock.sqlite3"
    init_fn(db_path)

    with sqlite3.connect(db_path) as conn:
        index_names = {
            row[1]
            for row in conn.execute("PRAGMA index_list(historical_minute_bars)")
        }

    assert "idx_historical_minute_bars_date_symbol_time" in index_names


def test_initialize_database_includes_trade_plan_and_fill_columns(tmp_path: Path) -> None:
    module = load_first_module(DB_CANDIDATES)
    init_fn = getattr(module, "initialize_database", None) or getattr(module, "init_db", None)
    assert callable(init_fn)

    db_path = tmp_path / "penny_stock.sqlite3"
    init_fn(db_path)

    with sqlite3.connect(db_path) as conn:
        market_activity_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(market_activity)")
        }
        scan_run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(scan_runs)")
        }
        paper_run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_runs)")
        }
        paper_position_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_positions)")
        }
        paper_order_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_orders)")
        }
        execution_order_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_orders)")
        }
        execution_position_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_positions)")
        }
        execution_account_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(execution_accounts)")
        }

    assert {"bid_price", "ask_price", "data_age_seconds", "has_live_trade", "has_live_quote"} <= market_activity_columns
    assert {"market_date", "snapshot_role", "point_in_time_tag"} <= scan_run_columns
    assert {"bucket", "total_transaction_cost"} <= paper_run_columns
    assert {
        "planned_stop_price",
        "planned_risk_pct",
        "strategy_bucket",
        "fill_reference_price",
        "fill_slippage_pct",
        "fees_paid_total",
        "day_regime",
        "watchlist_rank_at_entry",
    } <= paper_position_columns
    assert {
        "strategy_bucket",
        "planned_stop_price",
        "planned_risk_pct",
        "fill_reference_price",
        "fill_slippage_pct",
        "requested_quantity",
        "remaining_quantity",
        "fill_status",
        "transaction_cost",
        "day_regime",
        "watchlist_rank_at_entry",
    } <= paper_order_columns
    assert {
        "client_order_id",
        "broker_name",
        "account_id",
        "broker_order_id",
        "status",
        "request_payload",
        "response_payload",
    } <= execution_order_columns
    assert {
        "broker_name",
        "account_id",
        "symbol",
        "quantity",
        "market_value",
        "raw_payload",
    } <= execution_position_columns
    assert {
        "broker_name",
        "account_id",
        "cash_balance",
        "buying_power",
        "total_equity",
        "raw_payload",
    } <= execution_account_columns
