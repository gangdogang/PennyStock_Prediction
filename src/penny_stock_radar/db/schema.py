from __future__ import annotations

from pathlib import Path

from .connection import get_connection
from .schema_core import apply_core_schema
from .schema_market_data import apply_market_data_schema
from .schema_migrations import apply_schema_migrations
from .schema_trading import apply_trading_schema


def init_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(database_path) as connection:
        apply_core_schema(connection)
        apply_market_data_schema(connection)
        apply_trading_schema(connection)
        apply_schema_migrations(connection)


initialize_database = init_database
