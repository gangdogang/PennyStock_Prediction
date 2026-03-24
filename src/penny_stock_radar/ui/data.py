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
