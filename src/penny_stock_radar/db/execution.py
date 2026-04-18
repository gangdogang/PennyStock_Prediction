from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..providers.broker import BrokerAccount, BrokerOrder, BrokerPosition
from .connection import _parse_iso_datetime, _safe_json_dict, _safe_json_list, get_connection

def upsert_execution_orders(
    database_path: Path,
    rows: Iterable[BrokerOrder],
) -> None:
    payload = [
        (
            row.client_order_id,
            row.broker_name,
            row.account_id,
            row.symbol,
            row.side,
            row.quantity,
            row.filled_quantity,
            row.remaining_quantity,
            row.limit_price,
            row.avg_fill_price,
            row.exchange_code,
            row.status,
            row.intent,
            row.strategy_bucket,
            row.market_phase,
            row.order_type,
            row.broker_order_id,
            row.original_broker_order_id,
            json.dumps(row.request_payload),
            json.dumps(row.response_payload),
            json.dumps(row.notes),
            row.submitted_at.isoformat(),
            row.updated_at.isoformat(),
        )
        for row in rows
    ]
    if not payload:
        return
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO execution_orders (
                client_order_id,
                broker_name,
                account_id,
                symbol,
                side,
                quantity,
                filled_quantity,
                remaining_quantity,
                limit_price,
                avg_fill_price,
                exchange_code,
                status,
                intent,
                strategy_bucket,
                market_phase,
                order_type,
                broker_order_id,
                original_broker_order_id,
                request_payload,
                response_payload,
                notes,
                submitted_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                broker_name = excluded.broker_name,
                account_id = excluded.account_id,
                symbol = excluded.symbol,
                side = excluded.side,
                quantity = excluded.quantity,
                filled_quantity = excluded.filled_quantity,
                remaining_quantity = excluded.remaining_quantity,
                limit_price = excluded.limit_price,
                avg_fill_price = excluded.avg_fill_price,
                exchange_code = excluded.exchange_code,
                status = excluded.status,
                intent = excluded.intent,
                strategy_bucket = excluded.strategy_bucket,
                market_phase = excluded.market_phase,
                order_type = excluded.order_type,
                broker_order_id = excluded.broker_order_id,
                original_broker_order_id = excluded.original_broker_order_id,
                request_payload = excluded.request_payload,
                response_payload = excluded.response_payload,
                notes = excluded.notes,
                submitted_at = excluded.submitted_at,
                updated_at = excluded.updated_at
            """,
            payload,
        )


def fetch_execution_order(
    database_path: Path,
    client_order_id: str,
) -> BrokerOrder | None:
    with get_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM execution_orders
            WHERE client_order_id = ?
            LIMIT 1
            """,
            (client_order_id,),
        ).fetchone()
    return _execution_order_from_row(row)


def fetch_execution_orders(
    database_path: Path,
    *,
    broker_name: str | None = None,
    account_id: str | None = None,
    status: str | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> list[BrokerOrder]:
    query = """
        SELECT *
        FROM execution_orders
        WHERE 1 = 1
    """
    params: list[object] = []
    if broker_name is not None:
        query += " AND broker_name = ?"
        params.append(broker_name)
    if account_id is not None:
        query += " AND account_id = ?"
        params.append(account_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    if symbol is not None:
        query += " AND symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(limit, 1))
    with get_connection(database_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_execution_order_from_row(row) for row in rows if row is not None]


def replace_execution_positions_snapshot(
    database_path: Path,
    *,
    broker_name: str,
    account_id: str,
    rows: Iterable[BrokerPosition],
) -> None:
    payload = list(rows)
    with get_connection(database_path) as connection:
        connection.execute(
            """
            DELETE FROM execution_positions
            WHERE broker_name = ? AND account_id = ?
            """,
            (broker_name, account_id),
        )
        if not payload:
            return
        connection.executemany(
            """
            INSERT INTO execution_positions (
                broker_name,
                account_id,
                symbol,
                exchange_code,
                quantity,
                available_quantity,
                average_price,
                market_price,
                market_value,
                currency,
                raw_payload,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.broker_name,
                    row.account_id,
                    row.symbol,
                    row.exchange_code,
                    row.quantity,
                    row.available_quantity,
                    row.average_price,
                    row.market_price,
                    row.market_value,
                    row.currency,
                    json.dumps(row.raw_payload),
                    row.updated_at.isoformat(),
                )
                for row in payload
            ],
        )


def fetch_execution_positions(
    database_path: Path,
    *,
    broker_name: str | None = None,
    account_id: str | None = None,
) -> list[BrokerPosition]:
    query = """
        SELECT *
        FROM execution_positions
        WHERE 1 = 1
    """
    params: list[object] = []
    if broker_name is not None:
        query += " AND broker_name = ?"
        params.append(broker_name)
    if account_id is not None:
        query += " AND account_id = ?"
        params.append(account_id)
    query += " ORDER BY symbol ASC"
    with get_connection(database_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_execution_position_from_row(row) for row in rows if row is not None]


def upsert_execution_account(
    database_path: Path,
    row: BrokerAccount,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO execution_accounts (
                broker_name,
                account_id,
                currency,
                cash_balance,
                buying_power,
                total_equity,
                raw_payload,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(broker_name, account_id) DO UPDATE SET
                currency = excluded.currency,
                cash_balance = excluded.cash_balance,
                buying_power = excluded.buying_power,
                total_equity = excluded.total_equity,
                raw_payload = excluded.raw_payload,
                updated_at = excluded.updated_at
            """,
            (
                row.broker_name,
                row.account_id,
                row.currency,
                row.cash_balance,
                row.buying_power,
                row.total_equity,
                json.dumps(row.raw_payload),
                row.updated_at.isoformat(),
            ),
        )


def fetch_latest_execution_account(
    database_path: Path,
    *,
    broker_name: str | None = None,
    account_id: str | None = None,
) -> BrokerAccount | None:
    query = """
        SELECT *
        FROM execution_accounts
        WHERE 1 = 1
    """
    params: list[object] = []
    if broker_name is not None:
        query += " AND broker_name = ?"
        params.append(broker_name)
    if account_id is not None:
        query += " AND account_id = ?"
        params.append(account_id)
    query += " ORDER BY updated_at DESC, id DESC LIMIT 1"
    with get_connection(database_path) as connection:
        row = connection.execute(query, tuple(params)).fetchone()
    return _execution_account_from_row(row)


def _execution_order_from_row(row: sqlite3.Row | None) -> BrokerOrder | None:
    if row is None:
        return None
    return BrokerOrder(
        client_order_id=str(row["client_order_id"]),
        broker_name=str(row["broker_name"]),
        account_id=str(row["account_id"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=int(row["quantity"]),
        filled_quantity=int(row["filled_quantity"]) if row["filled_quantity"] is not None else 0,
        remaining_quantity=int(row["remaining_quantity"]) if row["remaining_quantity"] is not None else 0,
        limit_price=float(row["limit_price"]) if row["limit_price"] is not None else None,
        avg_fill_price=float(row["avg_fill_price"]) if row["avg_fill_price"] is not None else None,
        exchange_code=str(row["exchange_code"] or ""),
        status=str(row["status"]),
        intent=str(row["intent"] or ""),
        strategy_bucket=str(row["strategy_bucket"] or ""),
        market_phase=str(row["market_phase"] or ""),
        order_type=str(row["order_type"] or "limit"),
        broker_order_id=str(row["broker_order_id"]) if row["broker_order_id"] not in {None, ""} else None,
        original_broker_order_id=(
            str(row["original_broker_order_id"])
            if row["original_broker_order_id"] not in {None, ""}
            else None
        ),
        request_payload=_safe_json_dict(row["request_payload"]),
        response_payload=_safe_json_dict(row["response_payload"]),
        notes=_safe_json_list(row["notes"]),
        submitted_at=_parse_iso_datetime(row["submitted_at"]) or datetime.now(),
        updated_at=_parse_iso_datetime(row["updated_at"]) or datetime.now(),
    )


def _execution_position_from_row(row: sqlite3.Row) -> BrokerPosition:
    return BrokerPosition(
        broker_name=str(row["broker_name"]),
        account_id=str(row["account_id"]),
        symbol=str(row["symbol"]),
        exchange_code=str(row["exchange_code"] or ""),
        quantity=int(row["quantity"]),
        available_quantity=int(row["available_quantity"]) if row["available_quantity"] is not None else None,
        average_price=float(row["average_price"]) if row["average_price"] is not None else None,
        market_price=float(row["market_price"]) if row["market_price"] is not None else None,
        market_value=float(row["market_value"]) if row["market_value"] is not None else None,
        currency=str(row["currency"] or "USD"),
        raw_payload=_safe_json_dict(row["raw_payload"]),
        updated_at=_parse_iso_datetime(row["updated_at"]) or datetime.now(),
    )


def _execution_account_from_row(row: sqlite3.Row | None) -> BrokerAccount | None:
    if row is None:
        return None
    return BrokerAccount(
        broker_name=str(row["broker_name"]),
        account_id=str(row["account_id"]),
        currency=str(row["currency"] or "USD"),
        cash_balance=float(row["cash_balance"]) if row["cash_balance"] is not None else None,
        buying_power=float(row["buying_power"]) if row["buying_power"] is not None else None,
        total_equity=float(row["total_equity"]) if row["total_equity"] is not None else None,
        raw_payload=_safe_json_dict(row["raw_payload"]),
        updated_at=_parse_iso_datetime(row["updated_at"]) or datetime.now(),
    )
