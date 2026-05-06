from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from ..db import get_connection
from .external_data_validator import is_cost_eligible_source


FastestPath = Literal["archive_only", "vendor_purchase", "either"]

AVERAGE_DAYS_PER_MONTH = 30.4375
OPERATIONAL_PLANNING_STAMP = (
    "operational_planning_only_not_decision_grade"
)


@dataclass(frozen=True, slots=True)
class ShortfallEstimate:
    blocker_name: str
    current_value: float
    required_value: float
    deficit: float
    estimated_calendar_days_to_unblock_via_archive: int | None
    estimated_data_cost_usd_to_unblock: tuple[int, int] | None
    fastest_path: FastestPath


@dataclass(frozen=True, slots=True)
class CoverageShortfallReport:
    blockers: list[ShortfallEstimate]
    total_calendar_days_archive_path: int
    total_cost_usd_vendor_path: tuple[int, int]
    recommendation: str


def estimate_shortfall(db_path: Path, target: dict) -> CoverageShortfallReport:
    values = _TargetValues.from_mapping(target)
    if not db_path.exists():
        return CoverageShortfallReport(
            blockers=[
                ShortfallEstimate(
                    blocker_name="database_missing",
                    current_value=0.0,
                    required_value=1.0,
                    deficit=1.0,
                    estimated_calendar_days_to_unblock_via_archive=None,
                    estimated_data_cost_usd_to_unblock=None,
                    fastest_path="archive_only",
                )
            ],
            total_calendar_days_archive_path=0,
            total_cost_usd_vendor_path=(0, 0),
            recommendation=(
                f"{OPERATIONAL_PLANNING_STAMP}: create/fill the research database before "
                "coverage shortfall planning."
            ),
        )

    with get_connection(db_path) as connection:
        table_names = _table_names(connection)
        blockers = _build_blockers(connection, table_names, values)

    archive_days = [
        item.estimated_calendar_days_to_unblock_via_archive
        for item in blockers
        if item.estimated_calendar_days_to_unblock_via_archive is not None
    ]
    costs = [
        item.estimated_data_cost_usd_to_unblock
        for item in blockers
        if item.estimated_data_cost_usd_to_unblock is not None
    ]
    total_cost = (
        sum(cost[0] for cost in costs),
        sum(cost[1] for cost in costs),
    )
    return CoverageShortfallReport(
        blockers=blockers,
        total_calendar_days_archive_path=max(archive_days) if archive_days else 0,
        total_cost_usd_vendor_path=total_cost,
        recommendation=_recommendation(blockers),
    )


def shortfall_report_to_dict(report: CoverageShortfallReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["planning_stamp"] = OPERATIONAL_PLANNING_STAMP
    payload["total_cost_usd_vendor_path"] = list(report.total_cost_usd_vendor_path)
    for index, blocker in enumerate(report.blockers):
        cost = blocker.estimated_data_cost_usd_to_unblock
        payload["blockers"][index]["estimated_data_cost_usd_to_unblock"] = (
            list(cost) if cost is not None else None
        )
    return payload


@dataclass(frozen=True, slots=True)
class _TargetValues:
    minute_bars_months: float
    cost_eligible_overlap_pct: float
    corporate_action_months: float
    vendor_quote_source: str | None
    vendor_quote_cost_per_month_usd: float | None

    @classmethod
    def from_mapping(cls, target: dict) -> "_TargetValues":
        return cls(
            minute_bars_months=_float_target(
                target,
                "target_minute_bars_months",
                "target-minute-bars-months",
                default=6.0,
            ),
            cost_eligible_overlap_pct=_float_target(
                target,
                "target_cost_eligible_overlap_pct",
                "target-cost-eligible-overlap-pct",
                default=80.0,
            ),
            corporate_action_months=_float_target(
                target,
                "target_corporate_action_months",
                "target-corporate-action-months",
                default=12.0,
            ),
            vendor_quote_source=_str_target(
                target,
                "vendor_quote_source",
                "vendor-quote-source",
            ),
            vendor_quote_cost_per_month_usd=_optional_float_target(
                target,
                "vendor_quote_cost_per_month_usd",
                "vendor-quote-cost-per-month-usd",
            ),
        )


def _build_blockers(
    connection: sqlite3.Connection,
    table_names: set[str],
    values: _TargetValues,
) -> list[ShortfallEstimate]:
    blockers: list[ShortfallEstimate] = []
    minute_months = _date_span_months(
        _distinct_dates(connection, table_names, "historical_minute_bars", "market_date")
    )
    minute_deficit = max(0.0, values.minute_bars_months - minute_months)
    if minute_deficit > 0:
        blockers.append(
            ShortfallEstimate(
                blocker_name="minute_bars_months_shortfall",
                current_value=round(minute_months, 4),
                required_value=values.minute_bars_months,
                deficit=round(minute_deficit, 4),
                estimated_calendar_days_to_unblock_via_archive=_archive_days_for_month_deficit(
                    connection,
                    table_names,
                    table="historical_minute_bars",
                    date_column="market_date",
                    deficit_months=minute_deficit,
                ),
                estimated_data_cost_usd_to_unblock=None,
                fastest_path="archive_only",
            )
        )

    cost_overlap_pct, cost_deficit_dates = _cost_overlap(connection, table_names, values)
    cost_deficit_pct = max(0.0, values.cost_eligible_overlap_pct - cost_overlap_pct)
    if cost_deficit_pct > 0:
        cost_estimate = _vendor_quote_cost_estimate(
            connection,
            table_names,
            values=values,
            cost_deficit_pct=cost_deficit_pct,
        )
        archive_estimate = _archive_days_for_cost_dates(
            connection,
            table_names,
            deficit_dates=cost_deficit_dates,
        )
        blockers.append(
            ShortfallEstimate(
                blocker_name="cost_eligible_overlap_pct_shortfall",
                current_value=round(cost_overlap_pct, 4),
                required_value=values.cost_eligible_overlap_pct,
                deficit=round(cost_deficit_pct, 4),
                estimated_calendar_days_to_unblock_via_archive=archive_estimate,
                estimated_data_cost_usd_to_unblock=cost_estimate,
                fastest_path=_fastest_path(archive_estimate, cost_estimate),
            )
        )

    corporate_months = _date_span_months(
        _distinct_dates(connection, table_names, "corporate_actions", "effective_date")
    )
    corporate_deficit = max(0.0, values.corporate_action_months - corporate_months)
    if corporate_deficit > 0:
        blockers.append(
            ShortfallEstimate(
                blocker_name="corporate_action_months_shortfall",
                current_value=round(corporate_months, 4),
                required_value=values.corporate_action_months,
                deficit=round(corporate_deficit, 4),
                estimated_calendar_days_to_unblock_via_archive=_archive_days_for_month_deficit(
                    connection,
                    table_names,
                    table="corporate_actions",
                    date_column="effective_date",
                    deficit_months=corporate_deficit,
                ),
                estimated_data_cost_usd_to_unblock=None,
                fastest_path="archive_only",
            )
        )
    return blockers


def _cost_overlap(
    connection: sqlite3.Connection,
    table_names: set[str],
    values: _TargetValues,
) -> tuple[float, int]:
    base_dates = _distinct_dates(connection, table_names, "historical_minute_bars", "market_date")
    if not base_dates:
        return 0.0, 0
    cost_dates = _cost_eligible_dates(connection, table_names)
    covered = len(base_dates & cost_dates)
    current_pct = covered / len(base_dates) * 100.0
    required_dates = math.ceil(len(base_dates) * values.cost_eligible_overlap_pct / 100.0)
    return current_pct, max(0, required_dates - covered)


def _vendor_quote_cost_estimate(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    values: _TargetValues,
    cost_deficit_pct: float,
) -> tuple[int, int] | None:
    unit_cost = values.vendor_quote_cost_per_month_usd
    if values.vendor_quote_source is None or unit_cost is None or unit_cost <= 0:
        return None
    universe_size = _current_universe_size(connection, table_names)
    if universe_size <= 0:
        return None
    low_months = max(0.0, values.minute_bars_months * cost_deficit_pct / 100.0)
    high_months = max(values.minute_bars_months, low_months)
    low = math.ceil(low_months * universe_size * unit_cost)
    high = math.ceil(high_months * universe_size * unit_cost)
    return (low, high)


def _archive_days_for_month_deficit(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    table: str,
    date_column: str,
    deficit_months: float,
) -> int | None:
    deficit_days = deficit_months * AVERAGE_DAYS_PER_MONTH
    velocity = _archive_coverage_days_per_calendar_day(
        connection,
        table_names,
        table=table,
        date_column=date_column,
    )
    if velocity is None or velocity <= 0:
        return None
    return max(1, math.ceil(deficit_days / velocity))


def _archive_days_for_cost_dates(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    deficit_dates: int,
) -> int | None:
    if deficit_dates <= 0:
        return 0
    created_dates = _cost_eligible_created_dates(connection, table_names)
    cost_dates = _cost_eligible_dates(connection, table_names)
    if len(created_dates) < 2 or len(cost_dates) < 1:
        return None
    created_span = (max(created_dates) - min(created_dates)).days
    if created_span <= 0:
        return None
    velocity = len(cost_dates) / created_span
    if velocity <= 0:
        return None
    return max(1, math.ceil(deficit_dates / velocity))


def _archive_coverage_days_per_calendar_day(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    table: str,
    date_column: str,
) -> float | None:
    dates = _distinct_dates(connection, table_names, table, date_column)
    created_dates = _created_dates(connection, table_names, table)
    if len(dates) < 2 or len(created_dates) < 2:
        return None
    coverage_span_days = (max(dates) - min(dates)).days
    created_span_days = (max(created_dates) - min(created_dates)).days
    if coverage_span_days <= 0 or created_span_days <= 0:
        return None
    return coverage_span_days / created_span_days


def _distinct_dates(
    connection: sqlite3.Connection,
    table_names: set[str],
    table: str,
    date_column: str,
) -> set[date]:
    if table not in table_names:
        return set()
    rows = connection.execute(
        f"""
        SELECT DISTINCT {date_column} AS value
        FROM {table}
        WHERE {date_column} IS NOT NULL
        """
    ).fetchall()
    dates: set[date] = set()
    for row in rows:
        parsed = _parse_date(row["value"])
        if parsed is not None:
            dates.add(parsed)
    return dates


def _created_dates(
    connection: sqlite3.Connection,
    table_names: set[str],
    table: str,
) -> set[date]:
    if table not in table_names or not _has_column(connection, table, "created_at"):
        return set()
    rows = connection.execute(
        f"""
        SELECT DISTINCT created_at AS value
        FROM {table}
        WHERE created_at IS NOT NULL
        """
    ).fetchall()
    dates: set[date] = set()
    for row in rows:
        parsed = _parse_datetime(row["value"])
        if parsed is not None:
            dates.add(parsed.date())
    return dates


def _cost_eligible_dates(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> set[date]:
    dates: set[date] = set()
    if "historical_l1_quotes" in table_names:
        subscription_clause = _l1_subscription_continuous_clause(
            connection,
            "historical_l1_quotes",
        )
        rows = connection.execute(
            f"""
            SELECT market_date, source
            FROM historical_l1_quotes
            WHERE market_date IS NOT NULL
              AND bid_price IS NOT NULL
              AND ask_price IS NOT NULL
              AND bid_price > 0
              AND ask_price >= bid_price
              {subscription_clause}
            """
        ).fetchall()
        for row in rows:
            if is_cost_eligible_source(row["source"]):
                parsed = _parse_date(row["market_date"])
                if parsed is not None:
                    dates.add(parsed)
    if "historical_minute_bars" in table_names:
        rows = connection.execute(
            """
            SELECT market_date, source
            FROM historical_minute_bars
            WHERE market_date IS NOT NULL
              AND spread_pct IS NOT NULL
              AND spread_pct >= 0
            """
        ).fetchall()
        for row in rows:
            if is_cost_eligible_source(row["source"]):
                parsed = _parse_date(row["market_date"])
                if parsed is not None:
                    dates.add(parsed)
    return dates


def _cost_eligible_created_dates(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> set[date]:
    dates: set[date] = set()
    if "historical_l1_quotes" in table_names:
        subscription_clause = _l1_subscription_continuous_clause(
            connection,
            "historical_l1_quotes",
        )
        rows = connection.execute(
            f"""
            SELECT created_at, source
            FROM historical_l1_quotes
            WHERE created_at IS NOT NULL
              AND bid_price IS NOT NULL
              AND ask_price IS NOT NULL
              AND bid_price > 0
              AND ask_price >= bid_price
              {subscription_clause}
            """
        ).fetchall()
        for row in rows:
            if is_cost_eligible_source(row["source"]):
                parsed = _parse_datetime(row["created_at"])
                if parsed is not None:
                    dates.add(parsed.date())
    if "historical_minute_bars" in table_names:
        rows = connection.execute(
            """
            SELECT created_at, source
            FROM historical_minute_bars
            WHERE created_at IS NOT NULL
              AND spread_pct IS NOT NULL
              AND spread_pct >= 0
            """
        ).fetchall()
        for row in rows:
            if is_cost_eligible_source(row["source"]):
                parsed = _parse_datetime(row["created_at"])
                if parsed is not None:
                    dates.add(parsed.date())
    return dates


def _current_universe_size(connection: sqlite3.Connection, table_names: set[str]) -> int:
    counts: list[int] = []
    if "scan_runs" in table_names and "universe" in table_names:
        row = connection.execute(
            """
            SELECT COUNT(u.id) AS count
            FROM scan_runs sr
            JOIN universe u ON u.scan_id = sr.scan_id
            WHERE sr.snapshot_role = 'point_in_time'
              AND sr.market_date IS NOT NULL
            GROUP BY sr.scan_id
            ORDER BY sr.market_date DESC, sr.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            counts.append(int(row["count"] or 0))
    for table in ("historical_minute_bars", "historical_l1_quotes"):
        if table in table_names:
            row = connection.execute(
                f"SELECT COUNT(DISTINCT symbol) AS count FROM {table} WHERE symbol IS NOT NULL"
            ).fetchone()
            counts.append(int(row["count"] or 0))
    return max(counts) if counts else 0


def _date_span_months(dates: set[date]) -> float:
    if not dates:
        return 0.0
    span_days = (max(dates) - min(dates)).days
    if span_days == 0:
        return 1.0 / AVERAGE_DAYS_PER_MONTH
    return span_days / AVERAGE_DAYS_PER_MONTH


def _fastest_path(
    archive_estimate: int | None,
    cost_estimate: tuple[int, int] | None,
) -> FastestPath:
    if cost_estimate is None:
        return "archive_only"
    if archive_estimate is None:
        return "vendor_purchase"
    return "vendor_purchase" if archive_estimate > 0 else "either"


def _recommendation(blockers: list[ShortfallEstimate]) -> str:
    if not blockers:
        return (
            f"{OPERATIONAL_PLANNING_STAMP}: all configured coverage targets are met; "
            "no shortfall blockers."
        )
    archive_known = [
        item
        for item in blockers
        if item.estimated_calendar_days_to_unblock_via_archive is not None
    ]
    vendor_known = [
        item for item in blockers if item.estimated_data_cost_usd_to_unblock is not None
    ]
    if vendor_known and len(vendor_known) == len(blockers):
        path = "vendor_purchase has complete cost estimates for all blockers."
    elif archive_known and len(archive_known) == len(blockers):
        path = "archive_only can be estimated for all blockers from current archive velocity."
    elif vendor_known:
        path = "vendor_purchase can quantify quote-cost blockers; archive or new sources remain needed for the rest."
    elif archive_known:
        path = "archive_only has partial estimates; blockers with null archive days lack current archive velocity."
    else:
        path = "no unblock path is fully quantified from current inputs."
    return f"{OPERATIONAL_PLANNING_STAMP}: {path}"


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row["name"]) == column for row in rows)


def _l1_subscription_continuous_clause(
    connection: sqlite3.Connection,
    table: str,
) -> str:
    if not _has_column(connection, table, "subscription_continuous"):
        return ""
    return "AND COALESCE(subscription_continuous, 1) = 1"


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _float_target(
    target: dict,
    underscore_key: str,
    hyphen_key: str,
    *,
    default: float,
) -> float:
    value = target.get(underscore_key, target.get(hyphen_key, default))
    return float(value)


def _optional_float_target(
    target: dict,
    underscore_key: str,
    hyphen_key: str,
) -> float | None:
    value = target.get(underscore_key, target.get(hyphen_key))
    if value is None or value == "":
        return None
    return float(value)


def _str_target(target: dict, underscore_key: str, hyphen_key: str) -> str | None:
    value = target.get(underscore_key, target.get(hyphen_key))
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
