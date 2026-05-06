from __future__ import annotations

import csv
import json
import random
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from ..db import get_connection
from .external_data_validator import (
    cost_source_policy_summary,
    is_cost_eligible_source,
    is_diagnostic_only_source,
)
from .replay_grade_stamper import ReplayGrade, csv_decision_grade_comment, replay_grade_to_dict

EASTERN = ZoneInfo("America/New_York")
DEFAULT_ENTRY_TIME = time(9, 30)
MARKET_MINUTE_COUNT = (16 * 60) - (9 * 60 + 30)


Direction = Literal["long", "short", "cash"]


@dataclass(frozen=True, slots=True)
class EntryEvent:
    benchmark: str
    symbol: str
    market_date: date
    entry_at: datetime
    direction: Direction = "long"
    quantity: int = 1
    entry_price: float | None = None
    exit_price: float | None = None
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    strategy_symbol: str | None = None
    strategy_entry_at: datetime | None = None
    rank_metric: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Benchmark(Protocol):
    name: str

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]: ...


@dataclass(frozen=True, slots=True)
class SameUniverseRandomEntry:
    entries_per_date: int = 1
    name: str = "same_universe_random_entry"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        symbols = _normalized_universe(universe)
        if not symbols:
            return []
        rng = random.Random(seed)
        rows: list[EntryEvent] = []
        for market_date in sorted(market_dates):
            for _ in range(max(self.entries_per_date, 0)):
                symbol = rng.choice(symbols)
                rows.append(
                    EntryEvent(
                        benchmark=self.name,
                        symbol=symbol,
                        market_date=market_date,
                        entry_at=_random_market_datetime(market_date, rng),
                        direction="long",
                    )
                )
        return rows


@dataclass(frozen=True, slots=True)
class RandomTimeWithinDay:
    strategy_entries: tuple[EntryEvent, ...] = ()
    name: str = "random_time_within_day"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        rng = random.Random(seed + 101)
        if self.strategy_entries:
            return [
                EntryEvent(
                    benchmark=self.name,
                    symbol=entry.symbol,
                    market_date=entry.market_date,
                    entry_at=_random_market_datetime(entry.market_date, rng),
                    direction=entry.direction,
                    strategy_symbol=entry.symbol,
                    strategy_entry_at=entry.entry_at,
                )
                for entry in self.strategy_entries
            ]
        rows: list[EntryEvent] = []
        for market_date in sorted(market_dates):
            for symbol in _normalized_universe(universe):
                rows.append(
                    EntryEvent(
                        benchmark=self.name,
                        symbol=symbol,
                        market_date=market_date,
                        entry_at=_random_market_datetime(market_date, rng),
                        direction="long",
                    )
                )
        return rows


@dataclass(frozen=True, slots=True)
class TopGainerNaive:
    market_leaders: dict[tuple[date, str], dict[str, float]] = field(default_factory=dict)
    top_n: int = 5
    name: str = "top_gainer_naive"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        del seed
        return _leader_entries(
            benchmark_name=self.name,
            universe=universe,
            market_dates=market_dates,
            market_leaders=self.market_leaders,
            metric_name="pct_change",
            top_n=self.top_n,
        )


@dataclass(frozen=True, slots=True)
class VolumeLeaderNaive:
    market_leaders: dict[tuple[date, str], dict[str, float]] = field(default_factory=dict)
    top_n: int = 5
    name: str = "volume_leader_naive"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        del seed
        return _leader_entries(
            benchmark_name=self.name,
            universe=universe,
            market_dates=market_dates,
            market_leaders=self.market_leaders,
            metric_name="volume",
            top_n=self.top_n,
        )


@dataclass(frozen=True, slots=True)
class OppositeSide:
    strategy_entries: tuple[EntryEvent, ...] = ()
    name: str = "opposite_side"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        del universe, market_dates, seed
        return [
            EntryEvent(
                benchmark=self.name,
                symbol=entry.symbol,
                market_date=entry.market_date,
                entry_at=entry.entry_at,
                direction=_opposite_direction(entry.direction),
                quantity=entry.quantity,
                entry_price=entry.entry_price,
                strategy_symbol=entry.symbol,
                strategy_entry_at=entry.entry_at,
                metadata={"diagnostic_only": True},
            )
            for entry in self.strategy_entries
            if entry.direction in {"long", "short"}
        ]


@dataclass(frozen=True, slots=True)
class CashNoTrade:
    name: str = "cash_no_trade"

    def generate_entries(
        self,
        universe: list[str],
        market_dates: list[date],
        seed: int,
    ) -> list[EntryEvent]:
        del universe, market_dates, seed
        return []


@dataclass(frozen=True, slots=True)
class BenchmarkComparisonReport:
    strategy_kpis: dict[str, Any]
    benchmark_kpis: dict[str, dict[str, Any]]
    incremental_vs_each: dict[str, dict[str, Any]]
    decision_grade: ReplayGrade
    status: Literal["ready", "blocked"] = "ready"
    reason: str | None = None
    cost_audit: dict[str, Any] = field(default_factory=dict)
    execution_policy: dict[str, Any] = field(default_factory=dict)
    benchmark_entry_counts: dict[str, int] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteOptions:
    db_path: Path
    export_root: Path = Path("data/backtest_lab/research_runs")
    run_id: str | None = None
    universe: tuple[str, ...] = ()
    market_dates: tuple[date, ...] = ()
    strategy_entries: tuple[EntryEvent, ...] = ()
    strategy_trade_log: Path | None = None
    strategy_run_dir: Path | None = None
    strategy_bucket: str | None = None
    strategy_kpis: dict[str, Any] = field(default_factory=dict)
    strategy_kpis_path: Path | None = None
    seed: int = 20260506
    entries_per_date: int = 1
    top_n: int = 5
    require_cost_eligible: bool = True


@dataclass(frozen=True, slots=True)
class BenchmarkSuiteResult:
    export_dir: Path
    manifest_path: Path
    report_path: Path
    entries_path: Path
    status: str


class BenchmarkSuiteRunner:
    """Generate benchmark entry events behind the same cost-source gate as replay work."""

    def run(self, options: BenchmarkSuiteOptions) -> BenchmarkSuiteResult:
        run_id = options.run_id or datetime.now(timezone.utc).strftime(
            "benchmark_suite_%Y%m%d_%H%M%S"
        )
        export_dir = _unique_export_dir(options.export_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        market_dates = list(options.market_dates) or _load_market_dates(options.db_path)
        universe = list(options.universe) or _load_point_in_time_universe(options.db_path, market_dates)
        strategy_entries = list(options.strategy_entries) or _load_strategy_entries(
            strategy_trade_log=options.strategy_trade_log,
            strategy_run_dir=options.strategy_run_dir,
            strategy_bucket=options.strategy_bucket,
        )
        strategy_kpis = _load_strategy_kpis(options)
        cost_audit = _cost_audit(options.db_path, market_dates=market_dates)
        blocked_reason = _blocked_reason(
            cost_audit=cost_audit,
            market_dates=market_dates,
            universe=universe,
            require_cost_eligible=options.require_cost_eligible,
        )
        grade = _decision_grade(blocked_reason=blocked_reason, cost_audit=cost_audit)

        entries_by_benchmark: dict[str, list[EntryEvent]] = {}
        benchmark_kpis: dict[str, dict[str, Any]]
        if blocked_reason:
            benchmarks = _default_benchmarks(
                strategy_entries=tuple(strategy_entries),
                entries_per_date=options.entries_per_date,
                top_n=options.top_n,
            )
            benchmark_kpis = {
                benchmark.name: {
                    "status": "blocked",
                    "reason": blocked_reason,
                    "trade_count": 0,
                    "net_pnl": 0.0,
                }
                for benchmark in benchmarks
            }
        else:
            entries_by_benchmark = {
                benchmark.name: benchmark.generate_entries(universe, market_dates, options.seed)
                for benchmark in _default_benchmarks(
                    strategy_entries=tuple(strategy_entries),
                    entries_per_date=options.entries_per_date,
                    top_n=options.top_n,
                    market_leaders=_load_market_leaders(options.db_path, market_dates),
                )
            }
            benchmark_kpis = {
                name: _kpis_for_entries(name, rows)
                for name, rows in entries_by_benchmark.items()
            }

        report = BenchmarkComparisonReport(
            strategy_kpis=strategy_kpis,
            benchmark_kpis=benchmark_kpis,
            incremental_vs_each=_incremental_vs_each(strategy_kpis, benchmark_kpis),
            decision_grade=grade,
            status="blocked" if blocked_reason else "ready",
            reason=blocked_reason,
            cost_audit=cost_audit,
            execution_policy=_execution_policy(),
            benchmark_entry_counts={name: len(rows) for name, rows in entries_by_benchmark.items()},
        )
        manifest = {
            "run_id": run_id,
            "command": "run-benchmark-suite",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(options.db_path),
            "export_dir": str(export_dir),
            "seed": options.seed,
            "entries_per_date": options.entries_per_date,
            "top_n": options.top_n,
            "strategy_trade_log": str(options.strategy_trade_log) if options.strategy_trade_log else None,
            "strategy_run_dir": str(options.strategy_run_dir) if options.strategy_run_dir else None,
            "strategy_bucket": options.strategy_bucket,
            "require_cost_eligible": options.require_cost_eligible,
            "generation_policy": "blocked_cost_policy_refuses_benchmark_entry_generation",
            "execution_policy": _execution_policy(),
        }
        manifest_path = export_dir / "run_manifest.json"
        report_path = export_dir / "benchmark_suite_report.json"
        entries_path = export_dir / "benchmark_entries.csv"
        _write_json(manifest_path, manifest)
        _write_json(report_path, benchmark_comparison_report_to_dict(report))
        _write_entries_csv(entries_path, entries_by_benchmark, grade)
        return BenchmarkSuiteResult(
            export_dir=export_dir,
            manifest_path=manifest_path,
            report_path=report_path,
            entries_path=entries_path,
            status=report.status,
        )


def benchmark_comparison_report_to_dict(report: BenchmarkComparisonReport) -> dict[str, Any]:
    return {
        "strategy_kpis": report.strategy_kpis,
        "benchmark_kpis": report.benchmark_kpis,
        "incremental_vs_each": report.incremental_vs_each,
        "decision_grade": replay_grade_to_dict(report.decision_grade),
        "status": report.status,
        "reason": report.reason,
        "cost_audit": report.cost_audit,
        "execution_policy": report.execution_policy,
        "benchmark_entry_counts": report.benchmark_entry_counts,
        "generated_at": report.generated_at.isoformat(),
    }


def _execution_policy() -> dict[str, Any]:
    return {
        "entry_generation": "read_only_strategy_state_no_mutation",
        "fill_model": "penny_stock_radar.services.fill_model.FillModel",
        "cost_source_policy": "penny_stock_radar.services.external_data_validator",
        "grade_stamp": "penny_stock_radar.services.replay_grade_stamper.ReplayGrade",
        "note": (
            "Benchmark entries are generated separately from strategy logic and must be "
            "executed with the same fill model, cost policy, and ReplayGrade gate as the "
            "strategy run."
        ),
    }


def _default_benchmarks(
    *,
    strategy_entries: tuple[EntryEvent, ...],
    entries_per_date: int,
    top_n: int,
    market_leaders: dict[tuple[date, str], dict[str, float]] | None = None,
) -> tuple[Benchmark, ...]:
    leaders = market_leaders or {}
    return (
        SameUniverseRandomEntry(entries_per_date=entries_per_date),
        RandomTimeWithinDay(strategy_entries=strategy_entries),
        TopGainerNaive(market_leaders=leaders, top_n=top_n),
        VolumeLeaderNaive(market_leaders=leaders, top_n=top_n),
        OppositeSide(strategy_entries=strategy_entries),
        CashNoTrade(),
    )


def _normalized_universe(universe: list[str]) -> list[str]:
    symbols: list[str] = []
    for symbol in universe:
        normalized = str(symbol).strip().upper()
        if normalized and normalized not in symbols:
            symbols.append(normalized)
    return symbols


def _random_market_datetime(market_date: date, rng: random.Random) -> datetime:
    minute_offset = rng.randrange(MARKET_MINUTE_COUNT)
    hour = 9 + (30 + minute_offset) // 60
    minute = (30 + minute_offset) % 60
    return datetime.combine(market_date, time(hour, minute), tzinfo=EASTERN)


def _leader_entries(
    *,
    benchmark_name: str,
    universe: list[str],
    market_dates: list[date],
    market_leaders: dict[tuple[date, str], dict[str, float]],
    metric_name: str,
    top_n: int,
) -> list[EntryEvent]:
    symbols = _normalized_universe(universe)
    rows: list[EntryEvent] = []
    for market_date in sorted(market_dates):
        ranked = sorted(
            (
                (
                    symbol,
                    float(market_leaders.get((market_date, symbol), {}).get(metric_name, 0.0)),
                )
                for symbol in symbols
            ),
            key=lambda item: (-item[1], item[0]),
        )
        for symbol, metric in ranked[: max(top_n, 0)]:
            rows.append(
                EntryEvent(
                    benchmark=benchmark_name,
                    symbol=symbol,
                    market_date=market_date,
                    entry_at=datetime.combine(market_date, DEFAULT_ENTRY_TIME, tzinfo=EASTERN),
                    direction="long",
                    rank_metric=metric,
                    metadata={"metric": metric_name},
                )
            )
    return rows


def _opposite_direction(direction: Direction) -> Direction:
    if direction == "long":
        return "short"
    if direction == "short":
        return "long"
    return "cash"


def _kpis_for_entries(name: str, rows: list[EntryEvent]) -> dict[str, Any]:
    gross_pnl = sum(row.gross_pnl for row in rows)
    net_pnl = sum(row.net_pnl for row in rows)
    trade_count = len(rows)
    winning_trade_count = sum(1 for row in rows if row.net_pnl > 0)
    return {
        "status": "ready",
        "benchmark": name,
        "trade_count": trade_count,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "expectancy_per_trade": net_pnl / trade_count if trade_count else 0.0,
        "win_rate_pct": (winning_trade_count / trade_count) * 100.0 if trade_count else 0.0,
        "long_entry_count": sum(1 for row in rows if row.direction == "long"),
        "short_entry_count": sum(1 for row in rows if row.direction == "short"),
    }


def _incremental_vs_each(
    strategy_kpis: dict[str, Any],
    benchmark_kpis: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for name, kpis in benchmark_kpis.items():
        deltas: dict[str, Any] = {}
        for key, strategy_value in strategy_kpis.items():
            benchmark_value = kpis.get(key)
            if isinstance(strategy_value, (int, float)) and isinstance(benchmark_value, (int, float)):
                deltas[key] = float(strategy_value) - float(benchmark_value)
        output[name] = deltas
    return output


def _load_strategy_kpis(options: BenchmarkSuiteOptions) -> dict[str, Any]:
    if options.strategy_kpis:
        return dict(options.strategy_kpis)
    if options.strategy_kpis_path is None or not options.strategy_kpis_path.exists():
        return {}
    try:
        payload = json.loads(options.strategy_kpis_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_strategy_entries(
    *,
    strategy_trade_log: Path | None,
    strategy_run_dir: Path | None,
    strategy_bucket: str | None,
) -> list[EntryEvent]:
    trade_log = strategy_trade_log or (strategy_run_dir / "paper_trade_log.csv" if strategy_run_dir else None)
    if trade_log is None or not trade_log.exists():
        return []
    with trade_log.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    entry_rows = [row for row in rows if str(row.get("event") or "").upper() == "ENTRY"]
    if entry_rows:
        rows = entry_rows
    entries: list[EntryEvent] = []
    for row in rows:
        if strategy_bucket is not None and str(row.get("bucket") or "") != strategy_bucket:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        entry_at = _parse_datetime(str(row.get("entry_at") or row.get("opened_at") or ""))
        if not symbol or entry_at is None:
            continue
        market_date_text = str(row.get("market_date") or "").strip()
        market_date = date.fromisoformat(market_date_text) if market_date_text else entry_at.date()
        direction = "short" if str(row.get("side") or "").lower() == "short" else "long"
        entries.append(
            EntryEvent(
                benchmark="strategy",
                symbol=symbol,
                market_date=market_date,
                entry_at=entry_at,
                direction=direction,
                quantity=_safe_int(row.get("quantity"), default=1),
                entry_price=_safe_float(row.get("entry_price")),
                net_pnl=_safe_float(row.get("net_pnl")) or 0.0,
            )
        )
    return entries


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=EASTERN) if parsed.tzinfo is None else parsed


def _safe_int(value: object, *, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _load_market_dates(db_path: Path) -> list[date]:
    if not db_path.exists():
        return []
    try:
        with get_connection(db_path) as connection:
            if not _has_table(connection, "historical_minute_bars"):
                return []
            rows = connection.execute(
                """
                SELECT DISTINCT market_date
                FROM historical_minute_bars
                WHERE market_date IS NOT NULL
                ORDER BY market_date ASC
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    dates: list[date] = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(str(row["market_date"])))
        except ValueError:
            continue
    return dates


def _load_point_in_time_universe(db_path: Path, market_dates: list[date]) -> list[str]:
    if not db_path.exists() or not market_dates:
        return []
    try:
        with get_connection(db_path) as connection:
            if not _has_table(connection, "scan_runs") or not _has_table(connection, "universe"):
                return []
            symbols: set[str] = set()
            for market_date in market_dates:
                scan = connection.execute(
                    """
                    SELECT scan_id
                    FROM scan_runs
                    WHERE snapshot_role = 'point_in_time'
                      AND market_date = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (market_date.isoformat(),),
                ).fetchone()
                if scan is None:
                    continue
                rows = connection.execute(
                    """
                    SELECT symbol
                    FROM universe
                    WHERE scan_id = ?
                      AND passed_filters = 1
                    ORDER BY symbol ASC
                    """,
                    (scan["scan_id"],),
                ).fetchall()
                symbols.update(str(row["symbol"]).upper() for row in rows)
    except sqlite3.Error:
        return []
    return sorted(symbols)


def _load_market_leaders(
    db_path: Path,
    market_dates: list[date],
) -> dict[tuple[date, str], dict[str, float]]:
    if not db_path.exists() or not market_dates:
        return {}
    date_texts = [item.isoformat() for item in market_dates]
    placeholders = ",".join("?" for _ in date_texts)
    try:
        with get_connection(db_path) as connection:
            if not _has_table(connection, "historical_minute_bars"):
                return {}
            rows = connection.execute(
                f"""
                SELECT market_date, symbol,
                       MIN(open_price) AS first_open,
                       MAX(close_price) AS last_close,
                       SUM(volume) AS total_volume
                FROM historical_minute_bars
                WHERE market_date IN ({placeholders})
                  AND open_price > 0
                  AND close_price > 0
                GROUP BY market_date, symbol
                """,
                date_texts,
            ).fetchall()
    except sqlite3.Error:
        return {}
    leaders: dict[tuple[date, str], dict[str, float]] = {}
    for row in rows:
        try:
            market_date = date.fromisoformat(str(row["market_date"]))
        except ValueError:
            continue
        first_open = float(row["first_open"] or 0.0)
        last_close = float(row["last_close"] or 0.0)
        pct_change = ((last_close - first_open) / first_open) * 100.0 if first_open > 0 else 0.0
        leaders[(market_date, str(row["symbol"]).upper())] = {
            "pct_change": pct_change,
            "volume": float(row["total_volume"] or 0.0),
        }
    return leaders


def _cost_audit(db_path: Path, *, market_dates: list[date]) -> dict[str, Any]:
    base = {
        "cost_source_policy": cost_source_policy_summary(),
        "l1_source_counts": {},
        "l1_cost_eligible_source_counts": {},
        "l1_diagnostic_only_source_counts": {},
        "minute_spread_source_counts": {},
        "minute_spread_cost_eligible_source_counts": {},
        "minute_spread_diagnostic_only_source_counts": {},
        "excluded_l1_sources": [],
        "excluded_minute_spread_sources": [],
        "cost_eligible_sample_count": 0,
    }
    if not db_path.exists() or not market_dates:
        return {**base, "status": "blocked", "reason": "db_or_market_dates_missing"}
    date_texts = [item.isoformat() for item in market_dates]
    try:
        with get_connection(db_path) as connection:
            table_names = _table_names(connection)
            l1_counts = (
                _l1_source_counts(connection, date_texts)
                if "historical_l1_quotes" in table_names
                else {}
            )
            minute_counts = (
                _minute_source_counts(connection, date_texts)
                if "historical_minute_bars" in table_names
                else {}
            )
    except sqlite3.Error as exc:
        return {**base, "status": "blocked", "reason": f"sqlite_error:{exc}"}
    l1_eligible = _filter_source_counts(l1_counts, eligible=True)
    minute_eligible = _filter_source_counts(minute_counts, eligible=True)
    l1_diagnostic = _filter_source_counts(l1_counts, diagnostic=True)
    minute_diagnostic = _filter_source_counts(minute_counts, diagnostic=True)
    eligible_count = sum(l1_eligible.values()) + sum(minute_eligible.values())
    return {
        **base,
        "status": "ready" if eligible_count > 0 else "blocked",
        "reason": None if eligible_count > 0 else "cost_distribution_eligible_source_missing",
        "l1_source_counts": l1_counts,
        "l1_cost_eligible_source_counts": l1_eligible,
        "l1_diagnostic_only_source_counts": l1_diagnostic,
        "minute_spread_source_counts": minute_counts,
        "minute_spread_cost_eligible_source_counts": minute_eligible,
        "minute_spread_diagnostic_only_source_counts": minute_diagnostic,
        "excluded_l1_sources": sorted(set(l1_counts) - set(l1_eligible)),
        "excluded_minute_spread_sources": sorted(set(minute_counts) - set(minute_eligible)),
        "cost_eligible_sample_count": eligible_count,
    }


def _blocked_reason(
    *,
    cost_audit: dict[str, Any],
    market_dates: list[date],
    universe: list[str],
    require_cost_eligible: bool,
) -> str | None:
    if not market_dates:
        return "market_dates_missing"
    if not universe:
        return "point_in_time_universe_missing"
    if require_cost_eligible and int(cost_audit.get("cost_eligible_sample_count") or 0) <= 0:
        l1_count = sum(int(value or 0) for value in cost_audit.get("l1_source_counts", {}).values())
        minute_count = sum(
            int(value or 0) for value in cost_audit.get("minute_spread_source_counts", {}).values()
        )
        if l1_count or minute_count:
            return "cost_distribution_eligible_source_missing"
        return "cost_distribution_date_overlap_missing"
    return None


def _decision_grade(*, blocked_reason: str | None, cost_audit: dict[str, Any]) -> ReplayGrade:
    cost_blockers = [blocked_reason] if blocked_reason and "cost" in blocked_reason else []
    coverage_blockers = [blocked_reason] if blocked_reason and "cost" not in blocked_reason else []
    decision_grade = blocked_reason is None
    reason = "decision_grade_pass" if decision_grade else "; ".join(
        [
            *(f"cost:{item}" for item in cost_blockers),
            *(f"coverage:{item}" for item in coverage_blockers),
        ]
    )
    if not decision_grade and cost_audit.get("reason") and not cost_blockers:
        reason = f"{reason}; cost:{cost_audit['reason']}" if reason else f"cost:{cost_audit['reason']}"
    return ReplayGrade(
        decision_grade=decision_grade,
        grade_reason=reason,
        cost_source_blockers=cost_blockers,
        universe_blockers=[],
        coverage_blockers=coverage_blockers,
        stamped_at=datetime.now(timezone.utc),
    )


def _filter_source_counts(
    counts: dict[str, int],
    *,
    eligible: bool = False,
    diagnostic: bool = False,
) -> dict[str, int]:
    output: dict[str, int] = {}
    for source, count in counts.items():
        if eligible and not is_cost_eligible_source(source):
            continue
        if diagnostic and not is_diagnostic_only_source(source):
            continue
        output[source] = count
    return output


def _l1_source_counts(connection: sqlite3.Connection, market_dates: list[str]) -> dict[str, int]:
    placeholders = ",".join("?" for _ in market_dates)
    subscription_clause = ""
    if "subscription_continuous" in _table_columns(connection, "historical_l1_quotes"):
        subscription_clause = "AND subscription_continuous = 1"
    rows = connection.execute(
        f"""
        SELECT source, COUNT(*) AS row_count
        FROM historical_l1_quotes
        WHERE market_date IN ({placeholders})
          AND bid_price IS NOT NULL
          AND ask_price IS NOT NULL
          AND bid_price > 0
          AND ask_price >= bid_price
          {subscription_clause}
        GROUP BY source
        ORDER BY source ASC
        """,
        market_dates,
    ).fetchall()
    return {str(row["source"] or ""): int(row["row_count"] or 0) for row in rows}


def _minute_source_counts(connection: sqlite3.Connection, market_dates: list[str]) -> dict[str, int]:
    placeholders = ",".join("?" for _ in market_dates)
    rows = connection.execute(
        f"""
        SELECT source, COUNT(*) AS row_count
        FROM historical_minute_bars
        WHERE market_date IN ({placeholders})
          AND spread_pct IS NOT NULL
          AND spread_pct >= 0
        GROUP BY source
        ORDER BY source ASC
        """,
        market_dates,
    ).fetchall()
    return {str(row["source"] or ""): int(row["row_count"] or 0) for row in rows}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _has_table(connection: sqlite3.Connection, table_name: str) -> bool:
    return table_name in _table_names(connection)


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    for index in range(2, 1000):
        candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate a unique export directory for {requested_dir}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_entries_csv(
    path: Path,
    entries_by_benchmark: dict[str, list[EntryEvent]],
    grade: ReplayGrade,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _entry_event_to_row(entry)
        for entries in entries_by_benchmark.values()
        for entry in entries
    ]
    fieldnames = [
        "benchmark",
        "symbol",
        "market_date",
        "entry_at",
        "direction",
        "quantity",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "net_pnl",
        "strategy_symbol",
        "strategy_entry_at",
        "rank_metric",
        "metadata",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(csv_decision_grade_comment(grade) + "\n")
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _entry_event_to_row(entry: EntryEvent) -> dict[str, Any]:
    payload = asdict(entry)
    payload["market_date"] = entry.market_date.isoformat()
    payload["entry_at"] = entry.entry_at.isoformat()
    payload["strategy_entry_at"] = (
        entry.strategy_entry_at.isoformat() if entry.strategy_entry_at else ""
    )
    payload["metadata"] = json.dumps(entry.metadata, ensure_ascii=True, sort_keys=True)
    return payload
