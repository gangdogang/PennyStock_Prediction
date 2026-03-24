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
