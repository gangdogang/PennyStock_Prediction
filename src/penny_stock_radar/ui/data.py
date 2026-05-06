from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ..db import (
    fetch_latest_paper_strategy_runs,
    fetch_latest_paper_trading_run,
    fetch_paper_orders,
    fetch_paper_positions,
    fetch_paper_run_snapshots,
    fetch_scan_selection,
)
from ..services.paper_reporting import paper_report_paths, read_replay_grade
from ..services.paper_trading import PREDICTOR_WEIGHTED_BUCKET, PRIMARY_PAPER_STRATEGY


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _read_table(database_path: Path, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    if not database_path.exists():
        return pd.DataFrame()
    with _connect(database_path) as connection:
        try:
            frame = pd.read_sql_query(query, connection, params=params)
        except Exception:
            return pd.DataFrame()
    return frame


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, comment="#")
    except Exception:
        return pd.DataFrame()


def _frame_from_models(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([row.model_dump(mode="json") for row in rows if row is not None])


@st.cache_data(show_spinner=False, ttl=10)
def load_scan_selection_metadata(database_path: Path) -> dict[str, Any]:
    return fetch_scan_selection(database_path)


@st.cache_data(show_spinner=False, ttl=10)
def load_latest_scan_id(database_path: Path) -> str | None:
    metadata = load_scan_selection_metadata(database_path)
    scan_id = metadata.get("selected_scan_id")
    if not scan_id:
        return None
    return str(scan_id)


@st.cache_data(show_spinner=False, ttl=10)
def load_universe(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM universe
        WHERE scan_id = ?
        ORDER BY passed_filters DESC, price ASC, symbol ASC
        """,
        (scan_id,),
    )
    return _decode_json_columns(frame, ["filter_reasons"])


@st.cache_data(show_spinner=False, ttl=10)
def load_watchlist(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM watchlist
        WHERE scan_id = ?
        ORDER BY total_score DESC, symbol ASC
        """,
        (scan_id,),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=10)
def load_premarket(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM premarket_signals
        WHERE scan_id = ?
        ORDER BY quality_score DESC, symbol ASC
        """,
        (scan_id,),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=10)
def load_session_decisions(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM session_decisions
        WHERE scan_id = ?
        ORDER BY
            CASE decision WHEN 'ENTER' THEN 0 WHEN 'WATCH' THEN 1 ELSE 2 END,
            extension_z ASC,
            symbol ASC
        """,
        (scan_id,),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=10)
def load_replay_report(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    return _read_table(
        database_path,
        """
        SELECT *
        FROM replay_reports
        WHERE scan_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (scan_id,),
    )


@st.cache_data(show_spinner=False, ttl=5)
def load_latest_paper_run(database_path: Path) -> pd.DataFrame:
    run = fetch_latest_paper_trading_run(
        database_path,
        PRIMARY_PAPER_STRATEGY,
        bucket=PREDICTOR_WEIGHTED_BUCKET,
    )
    return _frame_from_models([run] if run is not None else [])


def _load_latest_paper_run_id(database_path: Path) -> str | None:
    frame = load_latest_paper_run(database_path)
    if frame.empty:
        return None
    return str(frame.iloc[0]["run_id"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_positions(database_path: Path) -> pd.DataFrame:
    run_id = _load_latest_paper_run_id(database_path)
    if run_id is None:
        return pd.DataFrame()
    frame = _frame_from_models(fetch_paper_positions(database_path, run_id))
    if frame.empty:
        return frame
    status_order = frame.get("status", pd.Series(index=frame.index, dtype=object)).map({"OPEN": 0}).fillna(1)
    updated_order = pd.to_datetime(frame.get("updated_at"), errors="coerce")
    frame = frame.assign(_status_order=status_order, _updated_order=updated_order)
    frame = frame.sort_values(["_status_order", "_updated_order", "symbol"], ascending=[True, False, True])
    return frame.drop(columns=["_status_order", "_updated_order"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_orders(database_path: Path) -> pd.DataFrame:
    run_id = _load_latest_paper_run_id(database_path)
    if run_id is None:
        return pd.DataFrame()
    frame = _frame_from_models(fetch_paper_orders(database_path, run_id))
    if frame.empty:
        return frame
    created_order = pd.to_datetime(frame.get("created_at"), errors="coerce")
    frame = frame.assign(_created_order=created_order)
    frame = frame.sort_values(["_created_order", "order_id"], ascending=[False, False])
    return frame.drop(columns=["_created_order"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_snapshots(database_path: Path) -> pd.DataFrame:
    run_id = _load_latest_paper_run_id(database_path)
    if run_id is None:
        return pd.DataFrame()
    return _frame_from_models(fetch_paper_run_snapshots(database_path, run_id))


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_strategy_runs(database_path: Path) -> pd.DataFrame:
    return _frame_from_models(fetch_latest_paper_strategy_runs(database_path, limit=10))


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_backtest_kpis(export_dir: Path) -> pd.DataFrame:
    return _read_csv(paper_report_paths(export_dir).backtest_kpis)


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_regime_split(export_dir: Path) -> pd.DataFrame:
    return _read_csv(paper_report_paths(export_dir).regime_split)


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_predictor_kpis(export_dir: Path) -> pd.DataFrame:
    return _read_csv(paper_report_paths(export_dir).predictor_kpis)


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_execution_quality(export_dir: Path) -> pd.DataFrame:
    return _read_csv(paper_report_paths(export_dir).execution_quality)


@st.cache_data(show_spinner=False, ttl=5)
def load_replay_grade(export_dir: Path) -> dict[str, Any]:
    return dict(read_replay_grade(export_dir) or {})


@st.cache_data(show_spinner=False, ttl=10)
def load_social(database_path: Path) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM social_signals
        WHERE scan_id = ?
        ORDER BY social_score DESC, symbol ASC
        """,
        (scan_id,),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=5)
def load_market_activity(database_path: Path, market_phase: str) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM market_activity
        WHERE scan_id = ? AND market_phase = ?
        ORDER BY pct_rank ASC, symbol ASC
        """,
        (scan_id, market_phase),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=5)
def load_prediction_outcomes(database_path: Path, market_phase: str) -> pd.DataFrame:
    scan_id = load_latest_scan_id(database_path)
    if scan_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM prediction_outcomes
        WHERE scan_id = ? AND market_phase = ?
        ORDER BY
            predicted DESC,
            CASE WHEN pct_rank IS NULL THEN 1 ELSE 0 END ASC,
            pct_rank ASC,
            CASE WHEN volume_rank IS NULL THEN 1 ELSE 0 END ASC,
            volume_rank ASC,
            symbol ASC
        """,
        (scan_id, market_phase),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=10)
def load_run_overview(database_path: Path) -> dict[str, int]:
    overview = {
        "scan_runs": 0,
        "universe": 0,
        "watchlist": 0,
        "premarket": 0,
        "session": 0,
        "replay": 0,
        "social": 0,
        "market_activity": 0,
        "prediction_outcomes": 0,
        "paper_runs": 0,
        "paper_positions": 0,
        "paper_orders": 0,
        "paper_snapshots": 0,
    }
    if not database_path.exists():
        return overview

    with _connect(database_path) as connection:
        for key, table in (
            ("scan_runs", "scan_runs"),
            ("universe", "universe"),
            ("watchlist", "watchlist"),
            ("premarket", "premarket_signals"),
            ("session", "session_decisions"),
            ("replay", "replay_reports"),
            ("social", "social_signals"),
            ("market_activity", "market_activity"),
            ("prediction_outcomes", "prediction_outcomes"),
            ("paper_runs", "paper_runs"),
            ("paper_positions", "paper_positions"),
            ("paper_orders", "paper_orders"),
            ("paper_snapshots", "paper_run_snapshots"),
        ):
            try:
                row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
                overview[key] = int(row["count"]) if row else 0
            except sqlite3.OperationalError:
                overview[key] = 0
    return overview


def _decode_json_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in columns:
        if column in result.columns:
            result[column] = result[column].apply(_safe_json_loads)
    return result


def _safe_json_loads(value: Any) -> Any:
    if value is None or value == "":
        return value
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value
