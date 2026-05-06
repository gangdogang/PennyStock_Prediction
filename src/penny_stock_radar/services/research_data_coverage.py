from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..db import get_connection
from .falsification_research import _is_cost_eligible_source, _is_diagnostic_only_source
from .paper_runtime import write_csv
from .universe_tradability_audit import (
    classify_listing_exchange,
    normalize_listing_exchange,
    summarize_kis_tradability_from_connection,
)

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class ResearchDataCoverageOptions:
    db_path: Path
    output_root: Path = Path("data/backtest_lab/research_runs")
    run_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    strategy_run_dir: Path | None = None
    strategy_trade_log: Path | None = None
    strategy_bucket: str | None = None
    sec_cutoff_time: time = time(8, 0)


@dataclass(frozen=True, slots=True)
class ResearchDataCoverageResult:
    export_dir: Path
    report_path: Path
    csv_path: Path
    summary_path: Path
    report: dict[str, Any]


class ResearchDataCoverageAuditor:
    def run(self, options: ResearchDataCoverageOptions) -> ResearchDataCoverageResult:
        run_id = options.run_id or datetime.now().strftime("coverage_%Y%m%d_%H%M%S")
        export_dir = options.output_root / run_id
        export_dir.mkdir(parents=True, exist_ok=True)

        if not options.db_path.exists():
            report = _empty_report(options, run_id, "database_missing")
            return _write_result(export_dir, report)

        strategy_entries = _load_strategy_entries(options)
        with get_connection(options.db_path) as connection:
            table_names = _table_names(connection)
            date_rows = _date_rows(
                connection,
                table_names=table_names,
                options=options,
                strategy_dates=sorted({entry["market_date"] for entry in strategy_entries}),
            )
            summary = _summary(
                connection,
                table_names=table_names,
                date_rows=date_rows,
                strategy_entries=strategy_entries,
                options=options,
            )

        report = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "db_path": str(options.db_path),
            "date_count": len(date_rows),
            "by_date": date_rows,
            "summary": summary,
            "policy": {
                "alpaca_iex": (
                    "diagnostic-only; excluded from cost evidence and matched "
                    "random-entry cost overlap"
                ),
                "cost_eligible_sources": (
                    "kis_l1_snapshot or explicit full NBBO/SIP source names/prefixes"
                ),
                "nasdaq_symbol_directory": (
                    "forward PIT archive only; does not reconstruct past replay dates"
                ),
            },
        }
        return _write_result(export_dir, report)


def _date_rows(
    connection: sqlite3.Connection,
    *,
    table_names: set[str],
    options: ResearchDataCoverageOptions,
    strategy_dates: list[str],
) -> list[dict[str, Any]]:
    dates = _requested_dates(options)
    if not dates:
        dates = sorted(
            set(strategy_dates)
            | set(_date_counts(connection, table_names, "historical_minute_bars", "market_date"))
            | set(_date_counts(connection, table_names, "historical_l1_quotes", "market_date"))
            | set(_date_counts(connection, table_names, "scan_runs", "market_date"))
            | set(_date_counts(connection, table_names, "corporate_actions", "effective_date"))
        )

    minute_rows = _date_counts(connection, table_names, "historical_minute_bars", "market_date")
    pit_counts = _pit_universe_counts(connection, table_names)
    pit_tradability = _pit_tradability_by_date(connection, table_names)
    l1_counts = _source_counts_by_date(
        connection,
        table_names,
        "historical_l1_quotes",
        "market_date",
        """
        bid_price IS NOT NULL
        AND ask_price IS NOT NULL
        AND bid_price > 0
        AND ask_price >= bid_price
        """,
    )
    minute_spread_counts = _source_counts_by_date(
        connection,
        table_names,
        "historical_minute_bars",
        "market_date",
        "spread_pct IS NOT NULL AND spread_pct >= 0",
    )
    corporate_counts = _date_counts(connection, table_names, "corporate_actions", "effective_date")
    sec_counts = _sec_cutoff_counts(connection, table_names, cutoff_time=options.sec_cutoff_time)
    corporate_status = _corporate_status(table_names, corporate_counts)

    rows: list[dict[str, Any]] = []
    for market_date in dates:
        l1_date_counts = l1_counts.get(market_date, {})
        minute_date_counts = minute_spread_counts.get(market_date, {})
        sec_date_counts = sec_counts.get(market_date, {"eligible": 0, "post_cutoff": 0})
        rows.append(
            {
                "market_date": market_date,
                "minute_bar_rows": minute_rows.get(market_date, 0),
                "pit_universe_exists": market_date in pit_counts,
                "pit_universe_count": pit_counts.get(market_date, 0),
                "kis_tradable_universe_pct": pit_tradability.get(market_date, {}).get(
                    "kis_tradable_universe_pct"
                ),
                "l1_cost_eligible_quote_count": _classified_count(
                    l1_date_counts,
                    cost_eligible=True,
                ),
                "l1_diagnostic_only_alpaca_iex_count": _classified_count(
                    l1_date_counts,
                    diagnostic_only=True,
                ),
                "minute_spread_cost_eligible_count": _classified_count(
                    minute_date_counts,
                    cost_eligible=True,
                ),
                "minute_spread_diagnostic_only_count": _classified_count(
                    minute_date_counts,
                    diagnostic_only=True,
                ),
                "corporate_action_coverage_status": corporate_status(
                    market_date,
                    corporate_counts.get(market_date, 0),
                ),
                "corporate_action_rows": corporate_counts.get(market_date, 0),
                "sec_filing_cutoff_coverage": (
                    "covered"
                    if sec_date_counts["eligible"] > 0
                    else ("diagnostic_only_after_cutoff" if sec_date_counts["post_cutoff"] else "missing")
                ),
                "sec_filing_cutoff_eligible_count": sec_date_counts["eligible"],
                "sec_filing_post_cutoff_diagnostic_count": sec_date_counts["post_cutoff"],
            }
        )
    return rows


def _summary(
    connection: sqlite3.Connection,
    *,
    table_names: set[str],
    date_rows: list[dict[str, Any]],
    strategy_entries: list[dict[str, str]],
    options: ResearchDataCoverageOptions,
) -> dict[str, Any]:
    minute_dates = [
        row["market_date"]
        for row in date_rows
        if int(row.get("minute_bar_rows") or 0) > 0
    ]
    minute_span_days = _span_days(minute_dates)
    strategy_dates = sorted({entry["market_date"] for entry in strategy_entries})
    missing_strategy_cost_dates = [
        row["market_date"]
        for row in date_rows
        if row["market_date"] in strategy_dates
        and int(row.get("l1_cost_eligible_quote_count") or 0)
        + int(row.get("minute_spread_cost_eligible_count") or 0)
        <= 0
    ]
    diagnostic_only_dates = [
        row["market_date"]
        for row in date_rows
        if int(row.get("l1_diagnostic_only_alpaca_iex_count") or 0)
        + int(row.get("minute_spread_diagnostic_only_count") or 0)
        > 0
    ]
    pit_missing_dates = [
        row["market_date"]
        for row in date_rows
        if int(row.get("minute_bar_rows") or 0) > 0 and not bool(row.get("pit_universe_exists"))
    ]
    corporate_summary = _corporate_summary(connection, table_names, minute_dates)
    tradability_summary = summarize_kis_tradability_from_connection(connection, table_names)
    blockers: list[str] = []
    has_6mo = len(minute_dates) >= 126 and (minute_span_days or 0) >= 180
    if not has_6mo:
        blockers.append("minute_bars_6_month_coverage_missing")
    if not strategy_entries:
        blockers.append("strategy_entry_schedule_missing_for_cost_overlap_check")
    if missing_strategy_cost_dates:
        blockers.append("strategy_dates_cost_eligible_overlap_missing")
    if pit_missing_dates:
        blockers.append("pit_universe_missing_for_minute_dates")
    if corporate_summary["status"] != "covered":
        blockers.append(f"corporate_actions_{corporate_summary['status']}")

    return {
        "minute_bar_distinct_dates": len(minute_dates),
        "minute_bar_date_span_days": minute_span_days,
        "has_6_month_minute_bar_coverage": has_6mo,
        "strategy_entry_count": len(strategy_entries),
        "strategy_dates": strategy_dates,
        "strategy_dates_cost_eligible_overlap": not missing_strategy_cost_dates
        if strategy_entries
        else False,
        "strategy_dates_missing_cost_eligible_overlap": missing_strategy_cost_dates,
        "diagnostic_only_data_presence": {
            "present": bool(diagnostic_only_dates),
            "market_dates": diagnostic_only_dates,
        },
        "pit_universe_missing_dates": pit_missing_dates,
        "kis_tradable_universe_pct": tradability_summary.get("kis_tradable_universe_pct"),
        "universe_tradability": tradability_summary,
        "corporate_actions": corporate_summary,
        "blockers": blockers,
        "next_allowed_action": (
            "run-falsification-audit"
            if not blockers
            else "fix coverage/PIT/cost/survivorship data before rerunning falsification audit"
        ),
    }


def _corporate_summary(
    connection: sqlite3.Connection,
    table_names: set[str],
    minute_dates: list[str],
) -> dict[str, Any]:
    if "corporate_actions" not in table_names:
        return {"status": "table_missing", "row_count": 0}
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS row_count,
            MIN(effective_date) AS min_effective_date,
            MAX(effective_date) AS max_effective_date
        FROM corporate_actions
        WHERE effective_date IS NOT NULL
        """
    ).fetchone()
    row_count = int(row["row_count"] or 0)
    if row_count <= 0:
        return {"status": "no_rows", "row_count": 0}
    min_effective = row["min_effective_date"]
    max_effective = row["max_effective_date"]
    status = "covered"
    if minute_dates and (
        min_effective is None
        or max_effective is None
        or min_effective > min(minute_dates)
        or max_effective < max(minute_dates)
    ):
        status = "date_range_mismatch"
    return {
        "status": status,
        "row_count": row_count,
        "min_effective_date": min_effective,
        "max_effective_date": max_effective,
    }


def _corporate_status(
    table_names: set[str],
    counts: dict[str, int],
):
    total = sum(counts.values())

    def status(_: str, date_count: int) -> str:
        if "corporate_actions" not in table_names:
            return "table_missing"
        if total <= 0:
            return "no_rows"
        if date_count > 0:
            return "covered"
        return "no_same_date_rows"

    return status


def _requested_dates(options: ResearchDataCoverageOptions) -> list[str]:
    if not options.start_date and not options.end_date:
        return []
    start = date.fromisoformat(options.start_date or options.end_date or "")
    end = date.fromisoformat(options.end_date or options.start_date or "")
    if end < start:
        raise ValueError("--end-date must be greater than or equal to --start-date.")
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current = date.fromordinal(current.toordinal() + 1)
    return days


def _date_counts(
    connection: sqlite3.Connection,
    table_names: set[str],
    table: str,
    date_column: str,
) -> dict[str, int]:
    if table not in table_names:
        return {}
    rows = connection.execute(
        f"""
        SELECT {date_column} AS market_date, COUNT(*) AS row_count
        FROM {table}
        WHERE {date_column} IS NOT NULL
        GROUP BY {date_column}
        ORDER BY {date_column}
        """
    ).fetchall()
    return {str(row["market_date"]): int(row["row_count"] or 0) for row in rows}


def _pit_universe_counts(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> dict[str, int]:
    if "scan_runs" not in table_names or "universe" not in table_names:
        return {}
    rows = connection.execute(
        """
        SELECT sr.market_date AS market_date, COUNT(u.id) AS row_count
        FROM scan_runs sr
        LEFT JOIN universe u ON u.scan_id = sr.scan_id
        WHERE sr.snapshot_role = 'point_in_time'
          AND sr.market_date IS NOT NULL
        GROUP BY sr.market_date
        ORDER BY sr.market_date
        """
    ).fetchall()
    return {str(row["market_date"]): int(row["row_count"] or 0) for row in rows}


def _pit_tradability_by_date(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> dict[str, dict[str, Any]]:
    if "scan_runs" not in table_names or "universe" not in table_names:
        return {}
    rows = connection.execute(
        """
        SELECT sr.market_date AS market_date, u.symbol, u.exchange
        FROM scan_runs sr
        JOIN universe u ON u.scan_id = sr.scan_id
        WHERE sr.snapshot_role = 'point_in_time'
          AND sr.market_date IS NOT NULL
        ORDER BY sr.market_date, u.symbol
        """
    ).fetchall()
    by_date: dict[str, dict[str, str | None]] = {}
    for row in rows:
        market_date = str(row["market_date"])
        symbol = str(row["symbol"] or "").strip().upper()
        if not symbol:
            continue
        by_date.setdefault(market_date, {}).setdefault(
            symbol,
            normalize_listing_exchange(row["exchange"]),
        )
    summaries: dict[str, dict[str, Any]] = {}
    for market_date, symbols in by_date.items():
        classifications = [classify_listing_exchange(exchange) for exchange in symbols.values()]
        total = len(classifications)
        tradable = sum(1 for item in classifications if item == "tradable")
        untradable = sum(1 for item in classifications if item == "untradable")
        unknown = sum(1 for item in classifications if item == "unknown")
        summaries[market_date] = {
            "total_symbols": total,
            "tradable_count": tradable,
            "untradable_count": untradable,
            "unknown_count": unknown,
            "kis_tradable_universe_pct": (tradable / total * 100.0) if total else None,
        }
    return summaries


def _source_counts_by_date(
    connection: sqlite3.Connection,
    table_names: set[str],
    table: str,
    date_column: str,
    where_clause: str,
) -> dict[str, dict[str, int]]:
    if table not in table_names:
        return {}
    rows = connection.execute(
        f"""
        SELECT {date_column} AS market_date, source, COUNT(*) AS row_count
        FROM {table}
        WHERE {date_column} IS NOT NULL
          AND {where_clause}
        GROUP BY {date_column}, source
        ORDER BY {date_column}, source
        """
    ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        market_date = str(row["market_date"])
        source = str(row["source"] or "")
        counts.setdefault(market_date, {})[source] = int(row["row_count"] or 0)
    return counts


def _sec_cutoff_counts(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    cutoff_time: time,
) -> dict[str, dict[str, int]]:
    if "filings" not in table_names or "scan_runs" not in table_names:
        return {}
    rows = connection.execute(
        """
        SELECT sr.market_date AS market_date, f.acceptance_datetime AS acceptance_datetime
        FROM filings f
        JOIN scan_runs sr ON sr.scan_id = f.scan_id
        WHERE sr.market_date IS NOT NULL
        """
    ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        market_date = str(row["market_date"] or "")
        if not market_date:
            continue
        accepted = _parse_datetime(row["acceptance_datetime"])
        bucket = "eligible"
        if accepted is not None:
            cutoff = datetime.combine(
                date.fromisoformat(market_date),
                cutoff_time,
                tzinfo=EASTERN,
            )
            bucket = "eligible" if accepted.astimezone(EASTERN) <= cutoff else "post_cutoff"
        counts.setdefault(market_date, {"eligible": 0, "post_cutoff": 0})[bucket] += 1
    return counts


def _classified_count(
    counts: dict[str, int],
    *,
    cost_eligible: bool = False,
    diagnostic_only: bool = False,
) -> int:
    total = 0
    for source, count in counts.items():
        if cost_eligible and _is_cost_eligible_source(source):
            total += count
        if diagnostic_only and _is_diagnostic_only_source(source):
            total += count
    return total


def _load_strategy_entries(options: ResearchDataCoverageOptions) -> list[dict[str, str]]:
    trade_log_path = options.strategy_trade_log
    if trade_log_path is None and options.strategy_run_dir is not None:
        trade_log_path = options.strategy_run_dir / "paper_trade_log.csv"
    if trade_log_path is None or not trade_log_path.exists():
        return []
    with trade_log_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    entries: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("event") or "").strip().upper() not in {"", "ENTRY"}:
            continue
        if options.strategy_bucket is not None and str(row.get("bucket") or "") != options.strategy_bucket:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        entry_at = str(row.get("entry_at") or row.get("opened_at") or "").strip()
        market_date = str(row.get("market_date") or "").strip() or _market_date_from_timestamp(entry_at)
        if symbol and market_date:
            entries.append({"symbol": symbol, "market_date": market_date})
    return entries


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _span_days(dates: list[str]) -> int | None:
    if not dates:
        return None
    return (date.fromisoformat(max(dates)) - date.fromisoformat(min(dates))).days


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed


def _market_date_from_timestamp(value: str) -> str:
    parsed = _parse_datetime(value)
    return parsed.astimezone(EASTERN).date().isoformat() if parsed is not None else ""


def _empty_report(
    options: ResearchDataCoverageOptions,
    run_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "db_path": str(options.db_path),
        "date_count": 0,
        "by_date": [],
        "summary": {
            "blockers": [reason],
            "next_allowed_action": "create/fill the research database before falsification audit",
        },
    }


def _write_result(export_dir: Path, report: dict[str, Any]) -> ResearchDataCoverageResult:
    report_path = export_dir / "research_data_coverage_report.json"
    csv_path = export_dir / "research_data_coverage_by_date.csv"
    summary_path = export_dir / "research_data_coverage_summary.md"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, list(report.get("by_date", [])))
    summary_path.write_text(_markdown_summary(report), encoding="utf-8")
    return ResearchDataCoverageResult(
        export_dir=export_dir,
        report_path=report_path,
        csv_path=csv_path,
        summary_path=summary_path,
        report=report,
    )


def _markdown_summary(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    blockers = summary.get("blockers", [])
    lines = [
        "# Research Data Coverage Audit",
        "",
        f"- DB: `{report.get('db_path')}`",
        f"- Dates: {report.get('date_count', 0)}",
        f"- 6mo minute bars: {summary.get('has_6_month_minute_bar_coverage', False)}",
        f"- Strategy cost overlap: {summary.get('strategy_dates_cost_eligible_overlap', False)}",
        f"- KIS tradable universe: {summary.get('kis_tradable_universe_pct')}",
        f"- Diagnostic-only data present: {summary.get('diagnostic_only_data_presence', {}).get('present', False) if isinstance(summary.get('diagnostic_only_data_presence'), dict) else False}",
        f"- Next allowed action: {summary.get('next_allowed_action')}",
        "",
        "## Blockers",
        "",
    ]
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- Alpaca IEX quote/spread rows are diagnostic-only and do not clear cost gates.",
            "- Nasdaq Symbol Directory snapshots are forward PIT only.",
            "- SEC filings count only when acceptance time is at or before the market-date cutoff.",
        ]
    )
    return "\n".join(lines) + "\n"
