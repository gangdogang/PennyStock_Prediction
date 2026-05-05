from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import get_connection
from .paper_runtime import write_csv


@dataclass(frozen=True, slots=True)
class PointInTimeUniverseAuditOptions:
    db_path: Path
    output_root: Path = Path("data/backtest_lab/research_runs")
    run_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    min_bars_per_symbol: int = 30


@dataclass(frozen=True, slots=True)
class PointInTimeUniverseAuditResult:
    export_dir: Path
    report_path: Path
    summary_path: Path
    csv_path: Path
    status: str


class PointInTimeUniverseAuditor:
    """Audit whether historical dates have usable point-in-time universe inputs."""

    def run(self, options: PointInTimeUniverseAuditOptions) -> PointInTimeUniverseAuditResult:
        run_id = options.run_id or datetime.now(timezone.utc).strftime("pit_universe_%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        rows = self._audit_rows(options)
        summary = self._summary(rows)
        report: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": summary["status"],
            "interpretation": (
                "This audit does not create point-in-time universes. Bar-derived reconstruction "
                "is diagnostic-only and must not be used as live-strategy edge evidence."
            ),
            "options": {
                "db_path": str(options.db_path),
                "start_date": options.start_date,
                "end_date": options.end_date,
                "min_bars_per_symbol": options.min_bars_per_symbol,
            },
            "summary": summary,
            "rows": rows,
            "next_allowed_action": self._next_allowed_action(summary),
        }

        report_path = export_dir / "pit_universe_audit_report.json"
        summary_path = export_dir / "pit_universe_audit_summary.md"
        csv_path = export_dir / "pit_universe_audit_dates.csv"
        _write_json(report_path, report)
        summary_path.write_text(self._markdown_summary(report), encoding="utf-8")
        write_csv(csv_path, rows)
        return PointInTimeUniverseAuditResult(
            export_dir=export_dir,
            report_path=report_path,
            summary_path=summary_path,
            csv_path=csv_path,
            status=str(summary["status"]),
        )

    def _audit_rows(self, options: PointInTimeUniverseAuditOptions) -> list[dict[str, Any]]:
        if not options.db_path.exists():
            return []
        with get_connection(options.db_path) as connection:
            tables = _table_names(connection)
            if "historical_minute_bars" not in tables:
                return []
            dates = self._market_dates(connection, options)
            rows: list[dict[str, Any]] = []
            for market_date in dates:
                exact_pit = self._exact_pit_snapshot(connection, market_date, tables=tables)
                prior_pit = self._prior_pit_snapshot(connection, market_date, tables=tables)
                bar_universe = self._bar_universe_summary(
                    connection,
                    market_date,
                    min_bars_per_symbol=options.min_bars_per_symbol,
                )
                decision = self._row_decision(
                    exact_pit_count=int(exact_pit.get("passed_count") or 0),
                    diagnostic_count=int(bar_universe["eligible_symbol_count"]),
                )
                rows.append(
                    {
                        "market_date": market_date,
                        "decision": decision,
                        "exact_pit_scan_id": exact_pit.get("scan_id"),
                        "exact_pit_passed_count": exact_pit.get("passed_count", 0),
                        "latest_prior_pit_date": prior_pit.get("market_date"),
                        "latest_prior_pit_scan_id": prior_pit.get("scan_id"),
                        "prior_pit_staleness_days": _date_gap_days(
                            str(prior_pit["market_date"]),
                            market_date,
                        )
                        if prior_pit.get("market_date")
                        else None,
                        "bar_symbol_count": bar_universe["symbol_count"],
                        "diagnostic_bar_universe_count": bar_universe["eligible_symbol_count"],
                        "min_bars_per_symbol": options.min_bars_per_symbol,
                        "warning": self._row_warning(decision),
                    }
                )
        return rows

    def _market_dates(
        self,
        connection: sqlite3.Connection,
        options: PointInTimeUniverseAuditOptions,
    ) -> list[str]:
        clauses = ["market_date IS NOT NULL"]
        parameters: list[str] = []
        if options.start_date is not None:
            clauses.append("market_date >= ?")
            parameters.append(options.start_date)
        if options.end_date is not None:
            clauses.append("market_date <= ?")
            parameters.append(options.end_date)
        where = " AND ".join(clauses)
        rows = connection.execute(
            f"""
            SELECT DISTINCT market_date
            FROM historical_minute_bars
            WHERE {where}
            ORDER BY market_date ASC
            """,
            parameters,
        ).fetchall()
        return [str(row["market_date"]) for row in rows]

    def _exact_pit_snapshot(
        self,
        connection: sqlite3.Connection,
        market_date: str,
        *,
        tables: set[str],
    ) -> dict[str, Any]:
        if "scan_runs" not in tables or "universe" not in tables:
            return {}
        row = connection.execute(
            """
            SELECT scan_id, market_date, created_at
            FROM scan_runs
            WHERE snapshot_role = 'point_in_time'
              AND market_date = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (market_date,),
        ).fetchone()
        if row is None:
            return {}
        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM universe
            WHERE scan_id = ?
              AND passed_filters = 1
            """,
            (row["scan_id"],),
        ).fetchone()
        return {
            "scan_id": str(row["scan_id"]),
            "market_date": str(row["market_date"]),
            "passed_count": int(count["count"] or 0),
        }

    def _prior_pit_snapshot(
        self,
        connection: sqlite3.Connection,
        market_date: str,
        *,
        tables: set[str],
    ) -> dict[str, Any]:
        if "scan_runs" not in tables:
            return {}
        row = connection.execute(
            """
            SELECT scan_id, market_date, created_at
            FROM scan_runs
            WHERE snapshot_role = 'point_in_time'
              AND market_date IS NOT NULL
              AND market_date < ?
            ORDER BY market_date DESC, created_at DESC
            LIMIT 1
            """,
            (market_date,),
        ).fetchone()
        if row is None:
            return {}
        return {
            "scan_id": str(row["scan_id"]),
            "market_date": str(row["market_date"]),
        }

    def _bar_universe_summary(
        self,
        connection: sqlite3.Connection,
        market_date: str,
        *,
        min_bars_per_symbol: int,
    ) -> dict[str, int]:
        rows = connection.execute(
            """
            SELECT symbol, COUNT(*) AS bar_count
            FROM historical_minute_bars
            WHERE market_date = ?
              AND open_price > 0
              AND high_price > 0
              AND low_price > 0
              AND close_price > 0
            GROUP BY symbol
            """,
            (market_date,),
        ).fetchall()
        return {
            "symbol_count": len(rows),
            "eligible_symbol_count": sum(
                1 for row in rows if int(row["bar_count"] or 0) >= min_bars_per_symbol
            ),
        }

    def _row_decision(self, *, exact_pit_count: int, diagnostic_count: int) -> str:
        if exact_pit_count > 0:
            return "exact_point_in_time_ready"
        if diagnostic_count > 0:
            return "diagnostic_bar_universe_possible"
        return "blocked_no_reconstruction_input"

    def _row_warning(self, decision: str) -> str:
        if decision == "exact_point_in_time_ready":
            return ""
        if decision == "diagnostic_bar_universe_possible":
            return "Bar-derived universe is diagnostic-only and may contain lookahead/adverse-selection bias."
        return "No exact PIT universe and no sufficient historical bars for diagnostic reconstruction."

    def _summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(rows)
        exact = sum(1 for row in rows if row["decision"] == "exact_point_in_time_ready")
        diagnostic = sum(1 for row in rows if row["decision"] == "diagnostic_bar_universe_possible")
        blocked = sum(1 for row in rows if row["decision"] == "blocked_no_reconstruction_input")
        status = "blocked"
        if total and exact == total:
            status = "exact_pit_ready"
        elif total and exact + diagnostic == total:
            status = "diagnostic_reconstruction_possible"
        return {
            "status": status,
            "market_date_count": total,
            "exact_point_in_time_ready_count": exact,
            "diagnostic_bar_universe_possible_count": diagnostic,
            "blocked_no_reconstruction_input_count": blocked,
        }

    def _next_allowed_action(self, summary: dict[str, Any]) -> str:
        status = str(summary.get("status"))
        if status == "exact_pit_ready":
            return "Run falsification audit and same-universe null benchmark on exact PIT dates."
        if status == "diagnostic_reconstruction_possible":
            return (
                "Use bar-derived universe only for diagnostic null plumbing; do not clear "
                "survivorship/adverse-selection blockers from it."
            )
        return "Backfill true point-in-time universe snapshots or historical bars before benchmark work."

    def _markdown_summary(self, report: dict[str, Any]) -> str:
        summary = report["summary"]
        lines = [
            "# Point-in-Time Universe Reconstruction Audit",
            "",
            f"- Status: `{summary['status']}`",
            f"- Market dates: {summary['market_date_count']}",
            f"- Exact PIT ready: {summary['exact_point_in_time_ready_count']}",
            f"- Diagnostic bar-universe possible: {summary['diagnostic_bar_universe_possible_count']}",
            f"- Blocked: {summary['blocked_no_reconstruction_input_count']}",
            f"- Next allowed action: {report['next_allowed_action']}",
            "",
            "## Caveat",
            "- Bar-derived reconstruction is diagnostic-only and must not clear edge, survivorship, or adverse-selection gates.",
        ]
        return "\n".join(lines) + "\n"


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _date_gap_days(start: str, end: str) -> int | None:
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    except ValueError:
        return None
    return (end_date - start_date).days


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    for index in range(2, 1000):
        candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate a unique export directory for {requested_dir}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
