from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from ..models import ReplayEvaluation, SessionDecision, SocialSignal
from .connection import _resolve_scan_id, get_connection

def insert_session_decisions(
    database_path: Path,
    scan_id: str,
    decisions: Iterable[SessionDecision],
) -> None:
    rows = [
        (
            scan_id,
            decision.symbol,
            decision.decision,
            decision.anchored_vwap,
            decision.opening_range_high,
            decision.opening_range_low,
            decision.regular_high,
            decision.regular_low,
            decision.regular_close,
            decision.extension_z,
            decision.mfe_pct,
            decision.mae_pct,
            json.dumps(decision.reasons),
            decision.created_at.isoformat(),
        )
        for decision in decisions
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM session_decisions WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO session_decisions (
                scan_id,
                symbol,
                decision,
                anchored_vwap,
                opening_range_high,
                opening_range_low,
                regular_high,
                regular_low,
                regular_close,
                extension_z,
                mfe_pct,
                mae_pct,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_session_decisions(
    database_path: Path,
    limit: int = 20,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM session_decisions
            WHERE scan_id = ?
            ORDER BY
                CASE decision WHEN 'ENTER' THEN 0 WHEN 'WATCH' THEN 1 ELSE 2 END,
                extension_z ASC,
                symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()


def insert_replay_report(
    database_path: Path,
    scan_id: str,
    report: ReplayEvaluation,
) -> None:
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM replay_reports WHERE scan_id = ?", (scan_id,))
        connection.execute(
            """
            INSERT INTO replay_reports (
                scan_id,
                label,
                expectancy,
                profit_factor,
                precision_at_k,
                average_mfe_pct,
                average_mae_pct,
                symbol_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                report.label,
                report.expectancy,
                report.profit_factor,
                report.precision_at_k,
                report.average_mfe_pct,
                report.average_mae_pct,
                report.symbol_count,
                report.created_at.isoformat(),
            ),
        )


def fetch_latest_replay_report(
    database_path: Path,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> sqlite3.Row | None:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return None
        return connection.execute(
            """
            SELECT *
            FROM replay_reports
            WHERE scan_id = ?
            LIMIT 1
            """,
            (target_scan_id,),
        ).fetchone()


def insert_social_signals(
    database_path: Path,
    scan_id: str,
    signals: Iterable[SocialSignal],
) -> None:
    rows = [
        (
            scan_id,
            signal.symbol,
            signal.mention_count,
            signal.mention_velocity,
            signal.unique_authors,
            signal.cross_platform_count,
            signal.engagement_total,
            signal.social_score,
            json.dumps(signal.reasons),
            signal.created_at.isoformat(),
        )
        for signal in signals
    ]
    if not rows:
        return
    with get_connection(database_path) as connection:
        connection.execute("DELETE FROM social_signals WHERE scan_id = ?", (scan_id,))
        connection.executemany(
            """
            INSERT INTO social_signals (
                scan_id,
                symbol,
                mention_count,
                mention_velocity,
                unique_authors,
                cross_platform_count,
                engagement_total,
                social_score,
                reasons,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def fetch_latest_social_signals(
    database_path: Path,
    limit: int = 20,
    *,
    scan_id: str | None = None,
    prefer_reportable: bool = True,
) -> list[sqlite3.Row]:
    with get_connection(database_path) as connection:
        target_scan_id = _resolve_scan_id(
            database_path,
            scan_id=scan_id,
            prefer_reportable=prefer_reportable,
        )
        if target_scan_id is None:
            return []
        return connection.execute(
            """
            SELECT *
            FROM social_signals
            WHERE scan_id = ?
            ORDER BY social_score DESC, symbol ASC
            LIMIT ?
            """,
            (target_scan_id, limit),
        ).fetchall()


def insert_setup_alerts(
    database_path: Path,
    alerts: Iterable[object],
    *,
    replace_run: bool = False,
) -> None:
    rows = [alert.to_row() for alert in alerts if hasattr(alert, "to_row")]
    if not rows:
        return
    run_ids = sorted({str(row["run_id"]) for row in rows})
    with get_connection(database_path) as connection:
        if replace_run:
            connection.executemany(
                "DELETE FROM setup_alerts WHERE run_id = ?",
                [(run_id,) for run_id in run_ids],
            )
        connection.executemany(
            """
            INSERT INTO setup_alerts (
                run_id,
                source,
                market_date,
                symbol,
                observed_at,
                market_phase,
                setup_id,
                setup_family,
                alert_state,
                time_bucket,
                confidence,
                quality,
                risk,
                data_grade,
                required_data_grade,
                blocked_reasons,
                reasons,
                payload,
                created_at
            )
            VALUES (
                :run_id,
                :source,
                :market_date,
                :symbol,
                :observed_at,
                :market_phase,
                :setup_id,
                :setup_family,
                :alert_state,
                :time_bucket,
                :confidence,
                :quality,
                :risk,
                :data_grade,
                :required_data_grade,
                :blocked_reasons,
                :reasons,
                :payload,
                :created_at
            )
            """,
            rows,
        )


def fetch_latest_setup_alerts(
    database_path: Path,
    *,
    run_id: str | None = None,
    setup_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    filters: list[str] = []
    params: list[object] = []
    if run_id is not None:
        filters.append("run_id = ?")
        params.append(run_id)
    if setup_id is not None:
        filters.append("setup_id = ?")
        params.append(setup_id)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with get_connection(database_path) as connection:
        return connection.execute(
            f"""
            SELECT *
            FROM setup_alerts
            {where_clause}
            ORDER BY observed_at DESC, symbol ASC, setup_id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
