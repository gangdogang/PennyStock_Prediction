from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


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


@st.cache_data(show_spinner=False, ttl=10)
def load_latest_scan_id(database_path: Path) -> str | None:
    frame = _read_table(
        database_path,
        """
        SELECT scan_id
        FROM scan_runs
        ORDER BY created_at DESC
        LIMIT 1
        """,
    )
    if frame.empty:
        return None
    return str(frame.iloc[0]["scan_id"])


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
    return _decode_json_columns(
        _read_table(
            database_path,
            """
            SELECT *
            FROM paper_runs
            ORDER BY
                CASE strategy_name WHEN 'auto_paper_v1' THEN 0 ELSE 1 END,
                updated_at DESC
            LIMIT 1
            """,
        ),
        ["notes"],
    )


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
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM paper_positions
        WHERE run_id = ?
        ORDER BY
            CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
            updated_at DESC,
            symbol ASC
        """,
        (run_id,),
    )
    return _decode_json_columns(frame, ["entry_reasons", "exit_reasons"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_orders(database_path: Path) -> pd.DataFrame:
    run_id = _load_latest_paper_run_id(database_path)
    if run_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM paper_orders
        WHERE run_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (run_id,),
    )
    return _decode_json_columns(frame, ["reasons"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_snapshots(database_path: Path) -> pd.DataFrame:
    run_id = _load_latest_paper_run_id(database_path)
    if run_id is None:
        return pd.DataFrame()
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM paper_run_snapshots
        WHERE run_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (run_id,),
    )
    return _decode_json_columns(frame, ["notes"])


@st.cache_data(show_spinner=False, ttl=5)
def load_paper_strategy_runs(database_path: Path) -> pd.DataFrame:
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM paper_runs
        ORDER BY strategy_name ASC, updated_at DESC
        """,
    )
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["strategy_name"], keep="first")
    frame = frame.sort_values(["updated_at", "strategy_name"], ascending=[False, True])
    return _decode_json_columns(frame, ["notes"])


@st.cache_data(show_spinner=False, ttl=10)
def load_social(database_path: Path) -> pd.DataFrame:
    frame = _read_table(
        database_path,
        """
        SELECT *
        FROM social_signals
        ORDER BY created_at DESC, social_score DESC, symbol ASC
        """,
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
