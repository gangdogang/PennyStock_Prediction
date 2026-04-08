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

    expected_tables = {"universe", "scan_runs"}
    assert tables & expected_tables, "Database should create at least one core Milestone 1 table."
    assert "universe" in tables


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
        paper_position_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_positions)")
        }
        paper_order_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(paper_orders)")
        }

    assert {"bid_price", "ask_price", "data_age_seconds", "has_live_trade", "has_live_quote"} <= market_activity_columns
    assert {
        "planned_stop_price",
        "planned_risk_pct",
        "strategy_bucket",
        "fill_reference_price",
        "fill_slippage_pct",
        "day_regime",
        "watchlist_rank_at_entry",
    } <= paper_position_columns
    assert {
        "strategy_bucket",
        "planned_stop_price",
        "planned_risk_pct",
        "fill_reference_price",
        "fill_slippage_pct",
        "day_regime",
        "watchlist_rank_at_entry",
    } <= paper_order_columns
