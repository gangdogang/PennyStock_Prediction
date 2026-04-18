from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

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
        bucket=str(row["bucket"] or "predictor_weighted"),
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
        total_transaction_cost=float(row["total_transaction_cost"]) if row["total_transaction_cost"] is not None else 0.0,
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
        planned_stop_price=float(row["planned_stop_price"]) if row["planned_stop_price"] is not None else None,
        planned_risk_pct=float(row["planned_risk_pct"]) if row["planned_risk_pct"] is not None else None,
        highest_price=float(row["highest_price"]) if row["highest_price"] is not None else None,
        add_count=int(row["add_count"]),
        partial_exit_count=int(row["partial_exit_count"]) if row["partial_exit_count"] is not None else 0,
        strategy_bucket=str(row["strategy_bucket"] or ""),
        fill_reference_price=float(row["fill_reference_price"]) if row["fill_reference_price"] is not None else None,
        fill_slippage_pct=float(row["fill_slippage_pct"]) if row["fill_slippage_pct"] is not None else None,
        fees_paid_total=float(row["fees_paid_total"]) if row["fees_paid_total"] is not None else 0.0,
        day_regime=row["day_regime"],
        watchlist_rank_at_entry=int(row["watchlist_rank_at_entry"]) if row["watchlist_rank_at_entry"] is not None else None,
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
        requested_quantity=int(row["requested_quantity"]) if row["requested_quantity"] is not None else None,
        remaining_quantity=int(row["remaining_quantity"]) if row["remaining_quantity"] is not None else 0,
        fill_status=str(row["fill_status"] or "FILLED"),
        price=float(row["price"]),
        notional=float(row["notional"]),
        transaction_cost=float(row["transaction_cost"]) if row["transaction_cost"] is not None else 0.0,
        strategy_bucket=str(row["strategy_bucket"] or ""),
        analysis_label=row["analysis_label"],
        analysis_score=float(row["analysis_score"]) if row["analysis_score"] is not None else None,
        planned_stop_price=float(row["planned_stop_price"]) if row["planned_stop_price"] is not None else None,
        planned_risk_pct=float(row["planned_risk_pct"]) if row["planned_risk_pct"] is not None else None,
        fill_reference_price=float(row["fill_reference_price"]) if row["fill_reference_price"] is not None else None,
        fill_slippage_pct=float(row["fill_slippage_pct"]) if row["fill_slippage_pct"] is not None else None,
        day_regime=row["day_regime"],
        watchlist_rank_at_entry=int(row["watchlist_rank_at_entry"]) if row["watchlist_rank_at_entry"] is not None else None,
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


def _safe_json_dict(value: object) -> dict[str, object]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    try:
        decoded = json.loads(str(value))
    except Exception:
        return {"raw": str(value)}
    if isinstance(decoded, dict):
        return {str(key): item for key, item in decoded.items()}
    return {"raw": decoded}


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
