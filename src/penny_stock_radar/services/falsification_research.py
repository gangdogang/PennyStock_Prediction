from __future__ import annotations

import csv
import json
import random
import sqlite3
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from ..db import get_connection
from .external_data_validator import (
    DEFAULT_POLICY_FILE,
    KIS_L1_SOURCE,
    LEGACY_COST_ELIGIBLE_EXACT_SOURCES,
    LEGACY_COST_ELIGIBLE_SOURCE_PREFIXES,
    LEGACY_DIAGNOSTIC_ONLY_EXACT_SOURCES,
    LEGACY_DIAGNOSTIC_ONLY_SOURCE_PREFIXES,
    cost_policy_sql_matchers,
    cost_source_policy_summary,
    is_cost_eligible_source,
    is_diagnostic_only_source,
)
from .paper_runtime import write_csv
from .universe_tradability_audit import audit_universe, report_to_dict


COST_ELIGIBLE_EXACT_SOURCES = LEGACY_COST_ELIGIBLE_EXACT_SOURCES
COST_ELIGIBLE_SOURCE_PREFIXES = LEGACY_COST_ELIGIBLE_SOURCE_PREFIXES
DIAGNOSTIC_ONLY_EXACT_SOURCES = LEGACY_DIAGNOSTIC_ONLY_EXACT_SOURCES
DIAGNOSTIC_ONLY_SOURCE_PREFIXES = LEGACY_DIAGNOSTIC_ONLY_SOURCE_PREFIXES


@dataclass(frozen=True, slots=True)
class FalsificationAuditOptions:
    db_path: Path
    export_root: Path = Path("data/backtest_lab/research_runs")
    run_id: str | None = None
    seed: int = 20260505
    null_sample_count: int = 1500
    fixed_stop_pct: float = 0.05
    atr_window: int = 14
    structure_window: int = 10
    live_loss_cap_usd: float = 5000.0
    hard_cap_months: int = 9
    weekly_hour_cap: int = 20
    spread_sample_limit: int = 10000
    max_pit_universe_staleness_days: int = 0
    strategy_run_dir: Path | None = None
    strategy_trade_log: Path | None = None
    strategy_bucket: str | None = None


@dataclass(frozen=True, slots=True)
class FalsificationAuditResult:
    export_dir: Path
    manifest_path: Path
    report_path: Path
    summary_path: Path
    null_baseline_path: Path
    matched_random_entry_path: Path
    status: str


class FalsificationResearchAuditor:
    """Build a falsification-first research audit from currently available local data."""

    def run(self, options: FalsificationAuditOptions) -> FalsificationAuditResult:
        run_id = options.run_id or datetime.now(timezone.utc).strftime(
            "falsification_%Y%m%d_%H%M%S"
        )
        export_dir = self._unique_export_dir(options.export_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        manifest = self._manifest(options, run_id=run_id, export_dir=export_dir)
        inventory = self._data_inventory(options.db_path)
        cost_audit = self._cost_audit(options)
        survivorship = self._survivorship_audit(options.db_path, inventory=inventory)
        universe_tradability = self._universe_tradability_audit(options)
        null_rows, null_summary = self._null_baseline(options, cost_audit=cost_audit)
        matched_rows, matched_summary = self._matched_random_entry_benchmark(
            options,
            cost_audit=cost_audit,
        )
        stop_geometry = self._stop_geometry_summary(null_rows)
        benchmark_suite = self._benchmark_suite(
            null_summary,
            matched_random_entry_summary=matched_summary,
        )
        blockers = self._blockers(
            inventory=inventory,
            cost_audit=cost_audit,
            survivorship=survivorship,
            universe_tradability=universe_tradability,
            null_summary=null_summary,
            benchmark_suite=benchmark_suite,
        )
        decision_gate = self._decision_gate(blockers=blockers, benchmark_suite=benchmark_suite)

        report: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked" if blockers else "ready_for_phase1",
            "interpretation": (
                "This report is a falsification gate. It does not search for profitable "
                "features and does not approve live or shadow trading."
            ),
            "governance": self._governance(options),
            "data_inventory": inventory,
            "cost_audit": cost_audit,
            "survivorship_audit": survivorship,
            "universe_tradability_audit": universe_tradability,
            "null_benchmark": null_summary,
            "matched_random_entry_benchmark": matched_summary,
            "benchmark_suite": benchmark_suite,
            "stop_geometry": stop_geometry,
            "decision_gate": decision_gate,
            "blockers": blockers,
            "next_allowed_action": (
                "Fix blockers before feature/setup_state tuning."
                if blockers
                else "Phase 1 stop-out label analysis may start with pre-registered features."
            ),
        }

        manifest_path = export_dir / "run_manifest.json"
        report_path = export_dir / "research_audit_report.json"
        summary_path = export_dir / "research_audit_summary.md"
        null_path = export_dir / "null_baseline_trades.csv"
        matched_path = export_dir / "matched_random_entry_trades.csv"
        _write_json(manifest_path, manifest)
        _write_json(report_path, report)
        write_csv(null_path, null_rows)
        write_csv(matched_path, matched_rows)
        summary_path.write_text(self._markdown_summary(report), encoding="utf-8")
        return FalsificationAuditResult(
            export_dir=export_dir,
            manifest_path=manifest_path,
            report_path=report_path,
            summary_path=summary_path,
            null_baseline_path=null_path,
            matched_random_entry_path=matched_path,
            status=str(report["status"]),
        )

    def _manifest(
        self,
        options: FalsificationAuditOptions,
        *,
        run_id: str,
        export_dir: Path,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "command": "run-falsification-audit",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(options.db_path),
            "export_dir": str(export_dir),
            "seed": options.seed,
            "null_sample_count": options.null_sample_count,
            "fixed_stop_pct": options.fixed_stop_pct,
            "atr_window": options.atr_window,
            "structure_window": options.structure_window,
            "spread_sample_limit": options.spread_sample_limit,
            "max_pit_universe_staleness_days": options.max_pit_universe_staleness_days,
            "strategy_run_dir": str(options.strategy_run_dir) if options.strategy_run_dir else None,
            "strategy_trade_log": str(options.strategy_trade_log) if options.strategy_trade_log else None,
            "strategy_bucket": options.strategy_bucket,
            "research_mode": "falsification_first_no_feature_tuning",
        }

    def _unique_export_dir(self, requested_dir: Path) -> Path:
        resolved = requested_dir.resolve()
        if not resolved.exists() or not any(resolved.iterdir()):
            return resolved
        for index in range(2, 1000):
            candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
            if not candidate.exists() or not any(candidate.iterdir()):
                return candidate
        raise OSError(f"Could not allocate a unique export directory for {requested_dir}")

    def _governance(self, options: FalsificationAuditOptions) -> dict[str, Any]:
        return {
            "hard_cap_months": options.hard_cap_months,
            "weekly_hour_cap": options.weekly_hour_cap,
            "live_loss_cap_usd": options.live_loss_cap_usd,
            "rules": [
                "Monthly kill/continue decision is mandatory.",
                "Do not add live capital after the live loss cap is hit.",
                "Do not run feature tuning before data/bias/cost/null benchmark blockers are cleared.",
                "Each experiment must pre-register hypothesis, dataset, parameters, and pass/fail criteria.",
            ],
            "phase_kill_criteria": {
                "phase0_data_bias_cost": [
                    "same-universe random baseline is indistinguishable from strategy within +/-5pp stop rate and net expectancy is not better",
                    "L1/spread coverage is too sparse to estimate cost-adjusted expectancy",
                    "point-in-time/survivorship audit cannot represent the historical universe",
                ],
                "phase1_stop_out_labels": [
                    "no pre-registered feature bucket improves cost-adjusted expectancy with monthly consistency",
                    "1R labels remain dominated by fixed-stop volatility rather than setup structure",
                ],
                "phase2_engine_restructure": [
                    "cooldown, spread gate, pullback-confirmed entry, and structure stop remain net negative",
                ],
                "phase3_vehicle_horizon_pivot": [
                    "multi-day catalyst hold, opposite-side diagnostic, and small-cap universe are all net negative",
                ],
            },
        }

    def _data_inventory(self, db_path: Path) -> dict[str, Any]:
        tables = {
            "historical_minute_bars": ("market_date", "symbol"),
            "historical_l1_quotes": ("market_date", "symbol"),
            "historical_halt_events": ("market_date", "symbol"),
            "historical_coverage_reports": ("market_date", None),
            "scan_runs": ("market_date", None),
            "universe": (None, "symbol"),
            "watchlist": (None, "symbol"),
            "premkt_predictions": ("market_date", "symbol"),
            "corporate_actions": ("effective_date", "symbol"),
        }
        if not db_path.exists():
            return {
                "db_path": str(db_path),
                "exists": False,
                "tables": {},
                "point_in_time_scan": {},
            }
        with get_connection(db_path) as connection:
            table_names = self._table_names(connection)
            summaries = {
                table: self._table_summary(
                    connection,
                    table,
                    market_date_column=columns[0],
                    symbol_column=columns[1],
                    exists=table in table_names,
                )
                for table, columns in tables.items()
            }
            point_in_time = self._point_in_time_summary(connection, table_names=table_names)
        return {
            "db_path": str(db_path),
            "exists": True,
            "tables": summaries,
            "point_in_time_scan": point_in_time,
        }

    def _table_names(self, connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def _table_summary(
        self,
        connection: sqlite3.Connection,
        table: str,
        *,
        market_date_column: str | None,
        symbol_column: str | None,
        exists: bool,
    ) -> dict[str, Any]:
        if not exists:
            return {"exists": False, "row_count": 0}
        row_count = int(connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        summary: dict[str, Any] = {
            "exists": True,
            "row_count": row_count,
        }
        if market_date_column is not None:
            row = connection.execute(
                f"""
                SELECT
                    COUNT(DISTINCT {market_date_column}) AS distinct_market_dates,
                    MIN({market_date_column}) AS min_market_date,
                    MAX({market_date_column}) AS max_market_date
                FROM {table}
                WHERE {market_date_column} IS NOT NULL
                """
            ).fetchone()
            summary.update(
                {
                    "distinct_market_dates": int(row["distinct_market_dates"] or 0),
                    "min_market_date": row["min_market_date"],
                    "max_market_date": row["max_market_date"],
                }
            )
        if symbol_column is not None:
            row = connection.execute(
                f"SELECT COUNT(DISTINCT {symbol_column}) AS distinct_symbols FROM {table}"
            ).fetchone()
            summary["distinct_symbols"] = int(row["distinct_symbols"] or 0)
        return summary

    def _point_in_time_summary(
        self,
        connection: sqlite3.Connection,
        *,
        table_names: set[str],
    ) -> dict[str, Any]:
        if "scan_runs" not in table_names:
            return {"exists": False, "row_count": 0}
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS count,
                COUNT(DISTINCT market_date) AS distinct_market_dates,
                MIN(market_date) AS min_market_date,
                MAX(market_date) AS max_market_date
            FROM scan_runs
            WHERE snapshot_role = 'point_in_time'
              AND market_date IS NOT NULL
            """
        ).fetchone()
        return {
            "exists": True,
            "row_count": int(row["count"] or 0),
            "distinct_market_dates": int(row["distinct_market_dates"] or 0),
            "min_market_date": row["min_market_date"],
            "max_market_date": row["max_market_date"],
        }

    def _cost_audit(self, options: FalsificationAuditOptions) -> dict[str, Any]:
        db_path = options.db_path
        if not db_path.exists():
            return _empty_cost_audit(
                cost_rule=(
                    "Only cost-eligible sources are allowed in cost distributions. "
                    "Alpaca IEX is diagnostic-only and cannot clear the cost gate."
                )
            )
        with get_connection(db_path) as connection:
            table_names = self._table_names(connection)
            l1_source_counts = (
                self._l1_spread_source_counts(connection)
                if "historical_l1_quotes" in table_names
                else {}
            )
            l1_continuous_source_counts = (
                self._l1_spread_source_counts(
                    connection,
                    subscription_continuous=True,
                )
                if "historical_l1_quotes" in table_names
                else {}
            )
            minute_spread_source_counts = (
                self._minute_spread_source_counts(connection)
                if "historical_minute_bars" in table_names
                else {}
            )
            l1_spreads = (
                self._l1_spread_values(
                    connection,
                    limit=options.spread_sample_limit,
                    seed=options.seed,
                )
                if "historical_l1_quotes" in table_names
                else []
            )
            minute_spreads = (
                self._minute_spread_values(
                    connection,
                    limit=options.spread_sample_limit,
                    seed=options.seed,
                )
                if "historical_minute_bars" in table_names
                else []
            )
        return {
            "l1_spread": _distribution(l1_spreads),
            "minute_spread": _distribution(minute_spreads),
            **_cost_source_policy_fields(
                l1_source_counts=l1_source_counts,
                l1_cost_eligible_source_counts=l1_continuous_source_counts,
                minute_spread_source_counts=minute_spread_source_counts,
            ),
            "cost_rule": (
                "round_trip_cross_spread_cost_pct approximates (ask-bid)/midpoint. "
                "Only cost-eligible sources are included. Alpaca IEX and alpaca_iex_* "
                "sources are diagnostic-only and cannot clear the cost gate."
            ),
        }

    def _cost_audit_for_market_dates(
        self,
        connection: sqlite3.Connection,
        *,
        table_names: set[str],
        market_dates: list[str],
        options: FalsificationAuditOptions,
    ) -> dict[str, Any]:
        if not market_dates:
            return _empty_cost_audit(cost_rule="Cost samples restricted to strategy market_date overlap.")
        l1_source_counts = (
            self._l1_spread_source_counts(connection, market_dates=market_dates)
            if "historical_l1_quotes" in table_names
            else {}
        )
        l1_continuous_source_counts = (
            self._l1_spread_source_counts(
                connection,
                market_dates=market_dates,
                subscription_continuous=True,
            )
            if "historical_l1_quotes" in table_names
            else {}
        )
        minute_spread_source_counts = (
            self._minute_spread_source_counts(connection, market_dates=market_dates)
            if "historical_minute_bars" in table_names
            else {}
        )
        l1_spreads = (
            self._l1_spread_values(
                connection,
                limit=options.spread_sample_limit,
                seed=options.seed,
                market_dates=market_dates,
            )
            if "historical_l1_quotes" in table_names
            else []
        )
        minute_spreads = (
            self._minute_spread_values(
                connection,
                limit=options.spread_sample_limit,
                seed=options.seed,
                market_dates=market_dates,
            )
            if "historical_minute_bars" in table_names
            else []
        )
        return {
            "l1_spread": _distribution(l1_spreads),
            "minute_spread": _distribution(minute_spreads),
            **_cost_source_policy_fields(
                l1_source_counts=l1_source_counts,
                l1_cost_eligible_source_counts=l1_continuous_source_counts,
                minute_spread_source_counts=minute_spread_source_counts,
            ),
            "cost_rule": (
                "Cost samples restricted to strategy market_date overlap and cost-eligible sources. "
                "Diagnostic-only sources cannot satisfy matched random-entry cost overlap."
            ),
        }

    def _l1_spread_source_counts(
        self,
        connection: sqlite3.Connection,
        *,
        market_dates: list[str] | None = None,
        subscription_continuous: bool | None = None,
    ) -> dict[str, int]:
        date_clause, date_params = _market_date_filter_sql(market_dates)
        subscription_clause = _l1_subscription_continuous_filter_sql(
            connection,
            subscription_continuous=subscription_continuous,
        )
        rows = connection.execute(
            f"""
            SELECT source, COUNT(*) AS row_count
            FROM historical_l1_quotes
            WHERE bid_price IS NOT NULL
              AND ask_price IS NOT NULL
              AND bid_price > 0
              AND ask_price >= bid_price
              {date_clause}
              {subscription_clause}
            GROUP BY source
            ORDER BY source ASC
            """,
            date_params,
        ).fetchall()
        return {str(row["source"] or ""): int(row["row_count"] or 0) for row in rows}

    def _minute_spread_source_counts(
        self,
        connection: sqlite3.Connection,
        *,
        market_dates: list[str] | None = None,
    ) -> dict[str, int]:
        date_clause, date_params = _market_date_filter_sql(market_dates)
        rows = connection.execute(
            f"""
            SELECT source, COUNT(*) AS row_count
            FROM historical_minute_bars
            WHERE spread_pct IS NOT NULL
              AND spread_pct >= 0
              {date_clause}
            GROUP BY source
            ORDER BY source ASC
            """,
            date_params,
        ).fetchall()
        return {str(row["source"] or ""): int(row["row_count"] or 0) for row in rows}

    def _l1_spread_values(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int,
        seed: int,
        market_dates: list[str] | None = None,
    ) -> list[float]:
        date_clause, date_params = _market_date_filter_sql(market_dates)
        source_clause, source_params = _cost_eligible_source_filter_sql("source")
        subscription_clause = _l1_subscription_continuous_filter_sql(
            connection,
            subscription_continuous=True,
        )
        rows = connection.execute(
            f"""
            WITH eligible AS (
                SELECT
                    bid_price,
                    ask_price,
                    ROW_NUMBER() OVER (ORDER BY market_date ASC, symbol ASC, quote_at ASC) AS rn,
                    COUNT(*) OVER () AS total
                FROM historical_l1_quotes
                WHERE bid_price IS NOT NULL
                  AND ask_price IS NOT NULL
                  AND bid_price > 0
                  AND ask_price >= bid_price
                  {date_clause}
                  {subscription_clause}
                  AND {source_clause}
            )
            SELECT bid_price, ask_price
            FROM eligible
            WHERE total <= ?
               OR ((rn + ?) % CAST(MAX(1, total / ?) AS INTEGER)) = 0
            ORDER BY rn ASC
            LIMIT ?
            """
            ,
            (*date_params, *source_params, limit, seed, limit, limit),
        ).fetchall()
        spreads: list[float] = []
        for row in rows:
            bid = float(row["bid_price"])
            ask = float(row["ask_price"])
            midpoint = (bid + ask) / 2
            if midpoint > 0:
                spreads.append((ask - bid) / midpoint)
        return spreads

    def _minute_spread_values(
        self,
        connection: sqlite3.Connection,
        *,
        limit: int,
        seed: int,
        market_dates: list[str] | None = None,
    ) -> list[float]:
        date_clause, date_params = _market_date_filter_sql(market_dates)
        source_clause, source_params = _cost_eligible_source_filter_sql("source")
        rows = connection.execute(
            f"""
            WITH eligible AS (
                SELECT
                    spread_pct,
                    ROW_NUMBER() OVER (ORDER BY market_date ASC, symbol ASC, bar_at ASC) AS rn,
                    COUNT(*) OVER () AS total
                FROM historical_minute_bars
                WHERE spread_pct IS NOT NULL
                  AND spread_pct >= 0
                  {date_clause}
                  AND {source_clause}
            )
            SELECT spread_pct
            FROM eligible
            WHERE total <= ?
               OR ((rn + ?) % CAST(MAX(1, total / ?) AS INTEGER)) = 0
            ORDER BY rn ASC
            LIMIT ?
            """
            ,
            (*date_params, *source_params, limit, seed, limit, limit),
        ).fetchall()
        return [float(row["spread_pct"]) for row in rows]

    def _survivorship_audit(
        self,
        db_path: Path,
        *,
        inventory: dict[str, Any],
    ) -> dict[str, Any]:
        tables = inventory.get("tables", {})
        pit = inventory.get("point_in_time_scan", {})
        warnings: list[str] = []
        if not bool(pit.get("row_count")):
            warnings.append("No point-in-time scan_runs found; survivorship/adverse-selection audit is blocked.")
        corporate_actions = tables.get("corporate_actions", {})
        if not bool(corporate_actions.get("exists")):
            warnings.append("No corporate_actions table found for delisting/reverse-split/ticker-change handling.")
        elif not bool(corporate_actions.get("row_count")):
            warnings.append("No corporate_actions rows found for delisting/reverse-split/ticker-change handling.")
        else:
            minute_table = tables.get("historical_minute_bars", {})
            minute_min = minute_table.get("min_market_date")
            minute_max = minute_table.get("max_market_date")
            action_min = corporate_actions.get("min_market_date")
            action_max = corporate_actions.get("max_market_date")
            if (
                minute_min
                and minute_max
                and (
                    not action_min
                    or not action_max
                    or str(action_min) > str(minute_min)
                    or str(action_max) < str(minute_max)
                )
            ):
                warnings.append(
                    "corporate_actions rows do not span the historical minute-bar date range; "
                    "current-only FINRA rows cannot clear survivorship/corporate-action risk."
                )
        return {
            "status": "blocked" if warnings else "review_required",
            "warnings": warnings,
            "required_next_checks": [
                "Confirm delisted/merged/reverse-split names are included in historical universe.",
                "Compare current-universe replay against point-in-time universe replay.",
                "Document ticker-change handling before any OOS claim.",
            ],
        }

    def _universe_tradability_audit(self, options: FalsificationAuditOptions) -> dict[str, Any]:
        universe_args: dict[str, Any] = {
            "enable_yfinance": False,
        }
        if options.strategy_run_dir is not None:
            universe_source = "replay_log"
            universe_args["replay_dir"] = options.strategy_run_dir
        elif options.strategy_trade_log is not None:
            universe_source = "replay_log"
            universe_args["trade_log"] = options.strategy_trade_log
        else:
            universe_source = "scanner_universe"
            universe_args["passed_only"] = False
        try:
            report = audit_universe(options.db_path, universe_source, universe_args)  # type: ignore[arg-type]
        except (OSError, sqlite3.Error, ValueError) as exc:
            return {
                "total_symbols": 0,
                "decision_blocker": False,
                "decision_grade": True,
                "grade_reason": f"universe_tradability_audit_unavailable:{exc}",
            }
        payload = report_to_dict(report)
        payload.pop("by_symbol", None)
        return payload

    def _has_table(self, db_path: Path, table_name: str) -> bool:
        if not db_path.exists():
            return False
        with get_connection(db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            return row is not None

    def _null_baseline(
        self,
        options: FalsificationAuditOptions,
        *,
        cost_audit: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not options.db_path.exists():
            return [], {"status": "blocked", "reason": "db_missing"}
        with get_connection(options.db_path) as connection:
            table_names = self._table_names(connection)
            if "historical_minute_bars" not in table_names:
                return [], {"status": "blocked", "reason": "historical_minute_bars_missing"}
            eligible_by_date = self._eligible_symbols_by_market_date(
                connection,
                table_names=table_names,
                options=options,
            )
            if not eligible_by_date:
                return [], {
                    "status": "blocked",
                    "reason": "point_in_time_universe_missing",
                    "interpretation": (
                        "Null baseline is blocked because same-universe sampling requires "
                        "point-in-time scan_runs and passed universe rows."
                    ),
                }
            group_rows = connection.execute(
                """
                SELECT market_date, symbol, COUNT(*) AS bar_count
                FROM historical_minute_bars
                WHERE open_price > 0
                  AND high_price > 0
                  AND low_price > 0
                  AND close_price > 0
                GROUP BY market_date, symbol
                HAVING COUNT(*) > 1
                ORDER BY market_date ASC, symbol ASC
                """
            ).fetchall()

            groups = [
                (
                    str(row["market_date"]),
                    str(row["symbol"]).upper(),
                    int(row["bar_count"]),
                )
                for row in group_rows
                if str(row["symbol"]).upper() in eligible_by_date.get(str(row["market_date"]), set())
            ]
            if not groups:
                return [], {"status": "blocked", "reason": "no_point_in_time_bar_overlap"}

            candidate_count = sum(max(bar_count - 1, 0) for _, _, bar_count in groups)
            if candidate_count <= 0:
                return [], {"status": "blocked", "reason": "no_entry_candidates"}

            rng = random.Random(options.seed)
            sample_size = min(options.null_sample_count, candidate_count)
            sampled = self._sample_group_entries(
                rng=rng,
                groups=groups,
                sample_size=sample_size,
            )
            bars_cache: dict[tuple[str, str], list[sqlite3.Row]] = {}
            fallback_cost = _percentile_source(cost_audit)
            output_rows: list[dict[str, Any]] = []
            for sample_index, (market_date, symbol, entry_index) in enumerate(sampled, start=1):
                key = (market_date, symbol)
                if key not in bars_cache:
                    bars_cache[key] = connection.execute(
                        """
                        SELECT symbol, market_date, market_phase, bar_at, open_price,
                               high_price, low_price, close_price, volume, spread_pct, source
                        FROM historical_minute_bars
                        WHERE market_date = ?
                          AND symbol = ?
                          AND open_price > 0
                          AND high_price > 0
                          AND low_price > 0
                          AND close_price > 0
                        ORDER BY bar_at ASC
                        """,
                        (market_date, symbol),
                    ).fetchall()
                group = bars_cache[key]
                if entry_index >= len(group) - 1:
                    continue
                entry = group[entry_index]
                output_rows.extend(
                    self._simulate_geometry_rows(
                        sample_index=sample_index,
                        group=group,
                        entry_index=entry_index,
                        entry=entry,
                        options=options,
                        fallback_cost=fallback_cost,
                    )
                )
        return output_rows, self._null_summary(output_rows, candidate_count=candidate_count)

    def _matched_random_entry_benchmark(
        self,
        options: FalsificationAuditOptions,
        *,
        cost_audit: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        trade_log_path = self._strategy_trade_log_path(options)
        if trade_log_path is None:
            return [], {
                "status": "blocked",
                "reason": "strategy_trade_log_not_configured",
                "interpretation": (
                    "Pass --strategy-trade-log or --strategy-run-dir to build a matched-entry "
                    "same-universe random benchmark."
                ),
            }
        if not trade_log_path.exists():
            return [], {
                "status": "blocked",
                "reason": "strategy_trade_log_missing",
                "trade_log_path": str(trade_log_path),
            }
        schedules = self._strategy_entry_schedule(
            trade_log_path,
            bucket=options.strategy_bucket,
        )
        if not schedules:
            return [], {
                "status": "blocked",
                "reason": "strategy_entry_schedule_empty",
                "trade_log_path": str(trade_log_path),
                "strategy_bucket": options.strategy_bucket,
            }
        if _cost_sample_count(cost_audit) <= 0:
            reason = (
                "cost_distribution_eligible_source_missing"
                if _source_count_total(cost_audit.get("l1_source_counts", {}))
                or _source_count_total(cost_audit.get("minute_spread_source_counts", {}))
                else "cost_distribution_missing"
            )
            return [], {
                "status": "blocked",
                "reason": reason,
                "trade_log_path": str(trade_log_path),
            }
        if not options.db_path.exists():
            return [], {"status": "blocked", "reason": "db_missing"}
        with get_connection(options.db_path) as connection:
            table_names = self._table_names(connection)
            if "historical_minute_bars" not in table_names:
                return [], {"status": "blocked", "reason": "historical_minute_bars_missing"}
            strategy_dates = sorted({str(schedule["market_date"]) for schedule in schedules})
            strategy_cost_audit = self._cost_audit_for_market_dates(
                connection,
                table_names=table_names,
                market_dates=strategy_dates,
                options=options,
            )
            if _cost_sample_count(strategy_cost_audit) <= 0:
                reason = (
                    "cost_distribution_eligible_source_missing"
                    if _source_count_total(strategy_cost_audit.get("l1_source_counts", {}))
                    or _source_count_total(strategy_cost_audit.get("minute_spread_source_counts", {}))
                    else "cost_distribution_date_overlap_missing"
                )
                return [], {
                    "status": "blocked",
                    "reason": reason,
                    "trade_log_path": str(trade_log_path),
                    "strategy_market_date_count": len(strategy_dates),
                    "strategy_market_dates_preview": strategy_dates[:10],
                    "total_cost_sample_count": _cost_sample_count(cost_audit),
                    "strategy_cost_audit": strategy_cost_audit,
                }
            eligible_by_date = self._eligible_symbols_by_market_date(
                connection,
                table_names=table_names,
                options=options,
            )
            if not eligible_by_date:
                return [], {
                    "status": "blocked",
                    "reason": "point_in_time_universe_missing",
                    "trade_log_path": str(trade_log_path),
                }
            rng = random.Random(options.seed + 17)
            sampled_schedules = self._sample_strategy_schedules(
                rng=rng,
                schedules=schedules,
                sample_size=min(options.null_sample_count, len(schedules)),
            )
            fallback_cost = _percentile_source(strategy_cost_audit)
            bars_cache: dict[tuple[str, str], list[sqlite3.Row]] = {}
            output_rows: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            for sample_index, schedule in enumerate(sampled_schedules, start=1):
                market_date = str(schedule["market_date"])
                eligible_symbols = sorted(eligible_by_date.get(market_date, set()))
                original_symbol = str(schedule.get("strategy_symbol") or "").upper()
                replacement_candidates = [
                    symbol for symbol in eligible_symbols if symbol != original_symbol
                ]
                if not replacement_candidates:
                    skipped.append(
                        self._matched_skip_row(
                            sample_index,
                            schedule,
                            reason="no_replacement_candidates",
                        )
                    )
                    continue
                replacement_symbols = replacement_candidates[:]
                rng.shuffle(replacement_symbols)
                selected: tuple[str, list[sqlite3.Row], int] | None = None
                for replacement_symbol in replacement_symbols:
                    key = (market_date, replacement_symbol)
                    if key not in bars_cache:
                        bars_cache[key] = self._load_symbol_bars(
                            connection,
                            market_date=market_date,
                            symbol=replacement_symbol,
                        )
                    group = bars_cache[key]
                    entry_index = self._entry_index_at_same_minute(
                        group,
                        str(schedule["entry_at"]),
                    )
                    if entry_index is not None and entry_index < len(group) - 1:
                        selected = (replacement_symbol, group, entry_index)
                        break
                if selected is None:
                    skipped.append(
                        self._matched_skip_row(
                            sample_index,
                            schedule,
                            reason="no_timestamp_bar_overlap",
                        )
                    )
                    continue
                replacement_symbol, group, entry_index = selected
                entry = group[entry_index]
                rows = self._simulate_geometry_rows(
                    sample_index=sample_index,
                    group=group,
                    entry_index=entry_index,
                    entry=entry,
                    options=options,
                    fallback_cost=fallback_cost,
                )
                for row in rows:
                    row.update(
                        {
                            "benchmark": "same_universe_random_entry",
                            "trade_log_path": str(trade_log_path),
                            "strategy_bucket": schedule.get("bucket"),
                            "strategy_symbol": original_symbol,
                            "strategy_entry_at": schedule["entry_at"],
                            "replacement_symbol": replacement_symbol,
                        }
                    )
                output_rows.extend(rows)
        all_rows = [*output_rows, *skipped]
        return all_rows, self._matched_random_entry_summary(
            rows=all_rows,
            strategy_entry_count=len(schedules),
            sampled_entry_count=len(sampled_schedules),
            trade_log_path=trade_log_path,
            strategy_bucket=options.strategy_bucket,
        )

    def _strategy_trade_log_path(self, options: FalsificationAuditOptions) -> Path | None:
        if options.strategy_trade_log is not None:
            return options.strategy_trade_log
        if options.strategy_run_dir is not None:
            return options.strategy_run_dir / "paper_trade_log.csv"
        return None

    def _strategy_entry_schedule(
        self,
        trade_log_path: Path,
        *,
        bucket: str | None,
    ) -> list[dict[str, str]]:
        with trade_log_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        entry_rows = [
            row
            for row in rows
            if str(row.get("event") or "").upper() == "ENTRY"
        ]
        if entry_rows:
            rows = entry_rows
        schedules: list[dict[str, str]] = []
        for row in rows:
            row_bucket = str(row.get("bucket") or "")
            if bucket is not None and row_bucket != bucket:
                continue
            event = str(row.get("event") or "").upper()
            status = str(row.get("status") or "").upper()
            if event and event not in {"ENTRY", "EXIT"}:
                continue
            if event == "EXIT" and status and status != "CLOSED":
                continue
            if not event and status and status != "CLOSED":
                continue
            entry_at = str(row.get("entry_at") or row.get("opened_at") or "")
            symbol = str(row.get("symbol") or "").upper()
            market_date = str(row.get("market_date") or "")
            if not market_date:
                market_date = _market_date_from_timestamp(entry_at)
            if not entry_at or not symbol or not market_date:
                continue
            schedules.append(
                {
                    "bucket": row_bucket,
                    "strategy_symbol": symbol,
                    "market_date": market_date,
                    "entry_at": entry_at,
                }
            )
        return schedules

    def _sample_strategy_schedules(
        self,
        *,
        rng: random.Random,
        schedules: list[dict[str, str]],
        sample_size: int,
    ) -> list[dict[str, str]]:
        if sample_size >= len(schedules):
            return schedules
        indexes = sorted(rng.sample(range(len(schedules)), sample_size))
        return [schedules[index] for index in indexes]

    def _load_symbol_bars(
        self,
        connection: sqlite3.Connection,
        *,
        market_date: str,
        symbol: str,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT symbol, market_date, market_phase, bar_at, open_price,
                   high_price, low_price, close_price, volume, spread_pct, source
            FROM historical_minute_bars
            WHERE market_date = ?
              AND symbol = ?
              AND open_price > 0
              AND high_price > 0
              AND low_price > 0
              AND close_price > 0
            ORDER BY bar_at ASC
            """,
            (market_date, symbol),
        ).fetchall()

    def _entry_index_at_same_minute(
        self,
        group: list[sqlite3.Row],
        entry_at: str,
    ) -> int | None:
        target = _parse_timestamp(entry_at)
        if target is None:
            return None
        target_minute = target.replace(second=0, microsecond=0)
        for index, row in enumerate(group):
            bar_at = _parse_timestamp(str(row["bar_at"]))
            if bar_at is None:
                continue
            if bar_at.replace(second=0, microsecond=0) == target_minute:
                return index
        return None

    def _matched_skip_row(
        self,
        sample_index: int,
        schedule: dict[str, str],
        *,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "sample_index": sample_index,
            "benchmark": "same_universe_random_entry",
            "geometry": "matched_entry",
            "status": "skipped",
            "skip_reason": reason,
            "market_date": schedule.get("market_date", ""),
            "strategy_bucket": schedule.get("bucket", ""),
            "strategy_symbol": schedule.get("strategy_symbol", ""),
            "strategy_entry_at": schedule.get("entry_at", ""),
        }

    def _matched_random_entry_summary(
        self,
        *,
        rows: list[dict[str, Any]],
        strategy_entry_count: int,
        sampled_entry_count: int,
        trade_log_path: Path,
        strategy_bucket: str | None,
    ) -> dict[str, Any]:
        completed = [row for row in rows if str(row.get("status", "")).startswith("completed")]
        skipped = [row for row in rows if row.get("status") == "skipped"]
        by_geometry: dict[str, dict[str, Any]] = {}
        for geometry in sorted({str(row["geometry"]) for row in completed}):
            group = [row for row in completed if row["geometry"] == geometry]
            by_geometry[geometry] = _metric_summary(group)
        summary: dict[str, Any] = {
            "status": "ready" if completed else "blocked",
            "trade_log_path": str(trade_log_path),
            "strategy_bucket": strategy_bucket,
            "strategy_entry_count": strategy_entry_count,
            "sampled_entry_count": sampled_entry_count,
            "completed_path_count": len(completed),
            "skipped_schedule_count": len(skipped),
            "skip_reasons": _count_by(skipped, "skip_reason"),
            "by_geometry": by_geometry,
            "interpretation": (
                "This benchmark preserves strategy entry timing but replaces the symbol with a "
                "random exact-PIT universe member. It is a null benchmark, not a strategy."
            ),
        }
        if not completed:
            summary["reason"] = "no_completed_matched_random_entries"
        return summary

    def _eligible_symbols_by_market_date(
        self,
        connection: sqlite3.Connection,
        *,
        table_names: set[str],
        options: FalsificationAuditOptions,
    ) -> dict[str, set[str]]:
        if "scan_runs" not in table_names or "universe" not in table_names:
            return {}
        dates = [
            str(row["market_date"])
            for row in connection.execute(
                """
                SELECT DISTINCT market_date
                FROM historical_minute_bars
                WHERE market_date IS NOT NULL
                ORDER BY market_date ASC
                """
            ).fetchall()
        ]
        eligible: dict[str, set[str]] = {}
        for market_date in dates:
            scan_row = connection.execute(
                """
                SELECT scan_id, market_date
                FROM scan_runs
                WHERE snapshot_role = 'point_in_time'
                  AND market_date IS NOT NULL
                  AND market_date <= ?
                ORDER BY market_date DESC, created_at DESC
                LIMIT 1
                """,
                (market_date,),
            ).fetchone()
            if scan_row is None:
                continue
            staleness_days = _date_gap_days(str(scan_row["market_date"]), market_date)
            if staleness_days is None or staleness_days > options.max_pit_universe_staleness_days:
                continue
            rows = connection.execute(
                """
                SELECT symbol
                FROM universe
                WHERE scan_id = ?
                  AND passed_filters = 1
                """,
                (scan_row["scan_id"],),
            ).fetchall()
            symbols = {str(row["symbol"]).upper() for row in rows}
            if symbols:
                eligible[market_date] = symbols
        return eligible

    def _sample_group_entries(
        self,
        *,
        rng: random.Random,
        groups: list[tuple[str, str, int]],
        sample_size: int,
    ) -> list[tuple[str, str, int]]:
        cumulative: list[int] = []
        running_total = 0
        for _, _, bar_count in groups:
            running_total += max(bar_count - 1, 0)
            cumulative.append(running_total)
        sampled: list[tuple[str, str, int]] = []
        seen: set[tuple[str, str, int]] = set()
        max_attempts = max(sample_size * 20, 100)
        attempts = 0
        while len(sampled) < sample_size and attempts < max_attempts:
            attempts += 1
            draw = rng.randrange(running_total)
            group_index = bisect_left(cumulative, draw + 1)
            market_date, symbol, bar_count = groups[group_index]
            entry_index = rng.randrange(max(bar_count - 1, 1))
            key = (market_date, symbol, entry_index)
            if key in seen:
                continue
            seen.add(key)
            sampled.append(key)
        return sampled

    def _simulate_geometry_rows(
        self,
        *,
        sample_index: int,
        group: list[sqlite3.Row],
        entry_index: int,
        entry: sqlite3.Row,
        options: FalsificationAuditOptions,
        fallback_cost: float | None,
    ) -> list[dict[str, Any]]:
        stop_specs = [
            ("fixed_pct", options.fixed_stop_pct, True),
            ("atr_normalized", self._atr_stop_pct(group, entry_index, options), False),
            ("structure_stop", self._structure_stop_pct(group, entry_index, options), False),
        ]
        rows: list[dict[str, Any]] = []
        entry_price = float(entry["close_price"])
        entry_spread = _none_or_float(entry["spread_pct"])
        entry_spread_source = str(entry["source"] or "") if "source" in entry.keys() else ""
        cost_pct = (
            entry_spread
            if entry_spread is not None and _is_cost_eligible_source(entry_spread_source)
            else fallback_cost
        )
        for geometry, stop_pct, enough_data in stop_specs:
            if stop_pct is None or stop_pct <= 0:
                rows.append(
                    {
                        "sample_index": sample_index,
                        "geometry": geometry,
                        "status": "skipped_insufficient_geometry",
                        "symbol": str(entry["symbol"]).upper(),
                        "market_date": str(entry["market_date"]),
                        "entry_at": str(entry["bar_at"]),
                    }
                )
                continue
            result = self._simulate_path(
                group=group,
                entry_index=entry_index,
                entry_price=entry_price,
                stop_pct=stop_pct,
                cost_pct=cost_pct,
            )
            rows.append(
                {
                    "sample_index": sample_index,
                    "geometry": geometry,
                    "status": "completed" if enough_data else "completed_fallback",
                    "symbol": str(entry["symbol"]).upper(),
                    "market_date": str(entry["market_date"]),
                    "market_phase": str(entry["market_phase"]),
                    "entry_at": str(entry["bar_at"]),
                    "entry_price": entry_price,
                    "stop_pct": stop_pct,
                    "spread_cost_pct": cost_pct,
                    "spread_cost_source": (
                        entry_spread_source
                        if entry_spread is not None and _is_cost_eligible_source(entry_spread_source)
                        else "fallback_cost_distribution"
                    ),
                    **result,
                }
            )
        return rows

    def _atr_stop_pct(
        self,
        group: list[sqlite3.Row],
        entry_index: int,
        options: FalsificationAuditOptions,
    ) -> float | None:
        start = max(0, entry_index - options.atr_window)
        prior = group[start:entry_index]
        if len(prior) < max(3, min(options.atr_window, 5)):
            return None
        ranges = []
        for row in prior:
            close = float(row["close_price"])
            if close > 0:
                ranges.append((float(row["high_price"]) - float(row["low_price"])) / close)
        return mean(ranges) if ranges else None

    def _structure_stop_pct(
        self,
        group: list[sqlite3.Row],
        entry_index: int,
        options: FalsificationAuditOptions,
    ) -> float | None:
        start = max(0, entry_index - options.structure_window)
        prior = group[start:entry_index]
        if len(prior) < 2:
            return None
        entry_price = float(group[entry_index]["close_price"])
        prior_low = min(float(row["low_price"]) for row in prior)
        if entry_price <= 0 or prior_low >= entry_price:
            return None
        return (entry_price - prior_low) / entry_price

    def _simulate_path(
        self,
        *,
        group: list[sqlite3.Row],
        entry_index: int,
        entry_price: float,
        stop_pct: float,
        cost_pct: float | None,
    ) -> dict[str, Any]:
        stop_price = entry_price * (1.0 - stop_pct)
        target_price = entry_price * (1.0 + stop_pct)
        future = group[entry_index + 1 :]
        best_r = 0.0
        worst_r = 0.0
        exit_reason = "session_end"
        exit_price = float(future[-1]["close_price"]) if future else entry_price
        bars_to_exit = len(future)
        reached_1r = False
        stopped = False
        ambiguous_stop_first = False
        for offset, row in enumerate(future, start=1):
            high = float(row["high_price"])
            low = float(row["low_price"])
            best_r = max(best_r, (high - entry_price) / (entry_price * stop_pct))
            worst_r = min(worst_r, (low - entry_price) / (entry_price * stop_pct))
            stop_hit = low <= stop_price
            target_hit = high >= target_price
            if stop_hit:
                ambiguous_stop_first = target_hit
                exit_reason = "ambiguous_stop_first" if ambiguous_stop_first else "stop_before_1r"
                exit_price = stop_price
                bars_to_exit = offset
                stopped = True
                reached_1r = False
                break
            if target_hit:
                exit_reason = "reached_1r"
                exit_price = target_price
                bars_to_exit = offset
                reached_1r = True
                break
        gross_return = (exit_price - entry_price) / entry_price
        net_return = gross_return - (cost_pct or 0.0)
        return {
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "bars_to_exit": bars_to_exit,
            "reached_1r": reached_1r,
            "stop_before_1r": stopped and not reached_1r,
            "ambiguous_stop_first": ambiguous_stop_first,
            "quick_stop_3m": stopped and bars_to_exit <= 3,
            "max_r_multiple": best_r,
            "min_r_multiple": worst_r,
            "gross_return_pct": gross_return * 100.0,
            "net_return_pct": net_return * 100.0,
        }

    def _null_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        candidate_count: int,
    ) -> dict[str, Any]:
        completed = [row for row in rows if str(row.get("status", "")).startswith("completed")]
        by_geometry: dict[str, dict[str, Any]] = {}
        for geometry in sorted({str(row["geometry"]) for row in completed}):
            group = [row for row in completed if row["geometry"] == geometry]
            by_geometry[geometry] = _metric_summary(group)
        return {
            "status": "ready" if completed else "blocked",
            "candidate_entry_count": candidate_count,
            "sampled_entry_count": len({int(row["sample_index"]) for row in completed}),
            "completed_path_count": len(completed),
            "by_geometry": by_geometry,
            "interpretation": (
                "This is a same-historical-bars random-time long baseline. It is a null "
                "benchmark, not a trading strategy."
            ),
        }

    def _benchmark_suite(
        self,
        null_summary: dict[str, Any],
        *,
        matched_random_entry_summary: dict[str, Any],
    ) -> dict[str, Any]:
        benchmarks = {
            "cash_no_trade": {
                "status": "ready",
                "avg_net_return_pct": 0.0,
                "interpretation": "Zero-return benchmark. Any live-capital hypothesis must beat cash after costs.",
            },
            "same_universe_random_time": null_summary,
            "same_universe_random_entry": matched_random_entry_summary,
            "naive_top_gainer_volume_leader": {
                "status": "not_implemented",
                "reason": "requires point-in-time market-activity leaderboard history",
            },
            "opposite_side_diagnostic": {
                "status": "not_implemented",
                "reason": "requires matched long-path inversion plus borrow/SSR caveat labeling",
                "interpretation": (
                    "Diagnostic only. Penny-stock shorting constraints mean this is not a live-strategy "
                    "approval even if expectancy flips positive."
                ),
            },
        }
        missing = [
            name
            for name, payload in benchmarks.items()
            if payload.get("status") not in {"ready", "completed"}
        ]
        return {
            "status": "ready" if not missing else "blocked",
            "benchmarks": benchmarks,
            "missing_or_blocked_benchmarks": missing,
            "interpretation": (
                "The full benchmark suite is required before strategy-vs-null edge claims. "
                "Current implementation can run same-universe random-time and matched-entry "
                "nulls when exact PIT and replay trade-log inputs are available."
            ),
        }

    def _stop_geometry_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        completed = [row for row in rows if str(row.get("status", "")).startswith("completed")]
        by_geometry = {
            geometry: _metric_summary([row for row in completed if row["geometry"] == geometry])
            for geometry in sorted({str(row["geometry"]) for row in completed})
        }
        return {
            "by_geometry": by_geometry,
            "warning": (
                "Fixed-percent 1R labels are treated as suspect until ATR/structure/spread-adjusted "
                "results are available and comparable."
            ),
        }

    def _decision_gate(
        self,
        *,
        blockers: list[str],
        benchmark_suite: dict[str, Any],
    ) -> dict[str, Any]:
        if blockers:
            return {
                "decision": "BLOCKED",
                "edge_judgment_allowed": False,
                "reason": "Data/bias/cost/null benchmark blockers must be cleared before edge claims.",
                "allowed_next_actions": [
                    "fix_point_in_time_universe",
                    "expand_minute_bar_history",
                    "expand_l1_or_minute_spread_coverage",
                    "add_survivorship_corporate_actions_inventory",
                    "complete_required_benchmark_suite",
                ],
                "forbidden_next_actions": [
                    "entry_label_tuning",
                    "setup_state_tuning",
                    "score_cutoff_tuning",
                    "stop_or_sizing_tuning",
                    "shadow_or_live_approval",
                ],
            }
        if benchmark_suite.get("status") != "ready":
            return {
                "decision": "BLOCKED",
                "edge_judgment_allowed": False,
                "reason": "Required benchmark suite is incomplete.",
            }
        return {
            "decision": "PASS",
            "edge_judgment_allowed": False,
            "reason": (
                "Phase 0 data/bias/cost gate passed. This is permission to start pre-registered "
                "Phase 1 stop-out analysis, not an edge or live-trading approval."
            ),
            "allowed_next_actions": [
                "build_full_trade_path_dataset",
                "run_pre_registered_univariate_stop_out_analysis",
            ],
        }

    def _blockers(
        self,
        *,
        inventory: dict[str, Any],
        cost_audit: dict[str, Any],
        survivorship: dict[str, Any],
        universe_tradability: dict[str, Any],
        null_summary: dict[str, Any],
        benchmark_suite: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        tables = inventory.get("tables", {})
        minute_dates = int(tables.get("historical_minute_bars", {}).get("distinct_market_dates") or 0)
        l1_count = int(tables.get("historical_l1_quotes", {}).get("row_count") or 0)
        pit_count = int(inventory.get("point_in_time_scan", {}).get("row_count") or 0)
        spread_count = int(cost_audit["l1_spread"]["count"]) + int(cost_audit["minute_spread"]["count"])
        if minute_dates < 126:
            blockers.append("Need roughly 6+ months of historical minute bars before stable feature conclusions.")
        if l1_count == 0 and int(cost_audit["minute_spread"]["count"]) == 0:
            blockers.append("No L1 or minute spread distribution is available; net expectancy would be under-costed.")
        elif spread_count < 1000:
            blockers.append("Spread sample count is below 1000; cost-adjusted expectancy is not stable enough.")
        if pit_count == 0:
            blockers.append("No point-in-time universe scan is available; survivorship/adverse-selection is unresolved.")
        if survivorship.get("status") == "blocked":
            blockers.extend(str(item) for item in survivorship.get("warnings", []))
        if bool(universe_tradability.get("decision_blocker")):
            blockers.append("universe_kis_untradable_pct_high")
        if null_summary.get("status") != "ready":
            blockers.append(f"Null baseline is not ready: {null_summary.get('reason', 'unknown')}")
        missing_benchmarks = benchmark_suite.get("missing_or_blocked_benchmarks", [])
        if missing_benchmarks:
            blockers.append(
                "Required benchmark suite is incomplete: "
                + ", ".join(str(item) for item in missing_benchmarks)
            )
        return sorted(set(blockers))

    def _markdown_summary(self, report: dict[str, Any]) -> str:
        blockers = report.get("blockers", [])
        lines = [
            "# Falsification Research Audit",
            "",
            f"- Status: `{report['status']}`",
            f"- Decision gate: `{report.get('decision_gate', {}).get('decision')}`",
            f"- Created at: `{report['created_at']}`",
            f"- Next allowed action: {report['next_allowed_action']}",
            "",
            "## Blockers",
        ]
        if blockers:
            lines.extend(f"- {blocker}" for blocker in blockers)
        else:
            lines.append("- None")
        lines.extend(["", "## Null Benchmark"])
        by_geometry = report.get("null_benchmark", {}).get("by_geometry", {})
        if not by_geometry:
            lines.append("- No completed null benchmark paths.")
        else:
            for geometry, metrics in by_geometry.items():
                lines.append(
                    "- "
                    f"`{geometry}`: trades={metrics['trade_count']}, "
                    f"stop_before_1r={metrics['stop_before_1r_rate_pct']:.2f}%, "
                    f"ambiguous={metrics['ambiguous_stop_first_rate_pct']:.2f}%, "
                    f"reached_1r={metrics['reached_1r_rate_pct']:.2f}%, "
                    f"net_expectancy={metrics['avg_net_return_pct']:.2f}%"
                )
        lines.extend(["", "## Matched Random Entry"])
        matched = report.get("matched_random_entry_benchmark", {})
        matched_by_geometry = matched.get("by_geometry", {})
        if not matched_by_geometry:
            lines.append(f"- No completed matched-entry paths: {matched.get('reason', matched.get('status'))}")
        else:
            for geometry, metrics in matched_by_geometry.items():
                lines.append(
                    "- "
                    f"`{geometry}`: trades={metrics['trade_count']}, "
                    f"stop_before_1r={metrics['stop_before_1r_rate_pct']:.2f}%, "
                    f"ambiguous={metrics['ambiguous_stop_first_rate_pct']:.2f}%, "
                    f"reached_1r={metrics['reached_1r_rate_pct']:.2f}%, "
                    f"net_expectancy={metrics['avg_net_return_pct']:.2f}%"
                )
        lines.extend(["", "## Benchmark Suite"])
        missing = report.get("benchmark_suite", {}).get("missing_or_blocked_benchmarks", [])
        if missing:
            lines.append("- Missing/blocked: " + ", ".join(str(item) for item in missing))
        else:
            lines.append("- Required benchmarks ready")
        lines.extend(["", "## Governance"])
        governance = report.get("governance", {})
        lines.append(
            f"- Hard cap: {governance.get('hard_cap_months')} months; "
            f"weekly cap: {governance.get('weekly_hour_cap')} hours; "
            f"live loss cap: ${governance.get('live_loss_cap_usd')}"
        )
        return "\n".join(lines) + "\n"


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return _empty_distribution()
    sorted_values = sorted(values)
    return {
        "count": len(sorted_values),
        "p50_pct": _percentile(sorted_values, 0.50) * 100.0,
        "p75_pct": _percentile(sorted_values, 0.75) * 100.0,
        "p90_pct": _percentile(sorted_values, 0.90) * 100.0,
        "p95_pct": _percentile(sorted_values, 0.95) * 100.0,
        "mean_pct": mean(sorted_values) * 100.0,
        "median_pct": median(sorted_values) * 100.0,
    }


def _empty_cost_audit(*, cost_rule: str) -> dict[str, Any]:
    return {
        "l1_spread": _empty_distribution(),
        "minute_spread": _empty_distribution(),
        **_cost_source_policy_fields(
            l1_source_counts={},
            l1_cost_eligible_source_counts={},
            minute_spread_source_counts={},
        ),
        "cost_rule": cost_rule,
    }


def _cost_source_policy_fields(
    *,
    l1_source_counts: dict[str, int],
    l1_cost_eligible_source_counts: dict[str, int] | None = None,
    minute_spread_source_counts: dict[str, int],
) -> dict[str, Any]:
    l1_cost_eligible = _filter_source_counts(
        l1_cost_eligible_source_counts or l1_source_counts,
        _is_cost_eligible_source,
    )
    minute_cost_eligible = _filter_source_counts(minute_spread_source_counts, _is_cost_eligible_source)
    l1_diagnostic = _filter_source_counts(l1_source_counts, _is_diagnostic_only_source)
    minute_diagnostic = _filter_source_counts(minute_spread_source_counts, _is_diagnostic_only_source)
    return {
        "cost_source_policy": cost_source_policy_summary(DEFAULT_POLICY_FILE),
        "l1_source_counts": dict(sorted(l1_source_counts.items())),
        "l1_cost_eligible_source_counts": l1_cost_eligible,
        "l1_diagnostic_only_source_counts": l1_diagnostic,
        "minute_spread_source_counts": dict(sorted(minute_spread_source_counts.items())),
        "minute_spread_cost_eligible_source_counts": minute_cost_eligible,
        "minute_spread_diagnostic_only_source_counts": minute_diagnostic,
        "excluded_l1_sources": _excluded_sources(l1_source_counts),
        "excluded_minute_spread_sources": _excluded_sources(minute_spread_source_counts),
    }


def _filter_source_counts(
    counts: dict[str, int],
    predicate,
) -> dict[str, int]:
    return {
        source: count
        for source, count in sorted(counts.items())
        if predicate(source)
    }


def _excluded_sources(counts: dict[str, int]) -> list[str]:
    return sorted(source for source in counts if not _is_cost_eligible_source(source))


def _empty_distribution() -> dict[str, Any]:
    return {
        "count": 0,
        "p50_pct": None,
        "p75_pct": None,
        "p90_pct": None,
        "p95_pct": None,
        "mean_pct": None,
        "median_pct": None,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _percentile_source(cost_audit: dict[str, Any]) -> float | None:
    for key in ("l1_spread", "minute_spread"):
        value = cost_audit.get(key, {}).get("p50_pct")
        if value is not None:
            return float(value) / 100.0
    return None


def _cost_sample_count(cost_audit: dict[str, Any]) -> int:
    return int(cost_audit.get("l1_spread", {}).get("count") or 0) + int(
        cost_audit.get("minute_spread", {}).get("count") or 0
    )


def _market_date_filter_sql(market_dates: list[str] | None) -> tuple[str, tuple[str, ...]]:
    if not market_dates:
        return "", ()
    dates = tuple(sorted(set(market_dates)))
    placeholders = ", ".join("?" for _ in dates)
    return f"AND market_date IN ({placeholders})", dates


def _l1_subscription_continuous_filter_sql(
    connection: sqlite3.Connection,
    *,
    subscription_continuous: bool | None,
) -> str:
    if subscription_continuous is None:
        return ""
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(historical_l1_quotes)").fetchall()
    }
    if "subscription_continuous" not in columns:
        return ""
    value = 1 if subscription_continuous else 0
    return f"AND COALESCE(subscription_continuous, 1) = {value}"


def _cost_eligible_source_filter_sql(column_name: str) -> tuple[str, tuple[str, ...]]:
    exact_sources, prefixes = cost_policy_sql_matchers(DEFAULT_POLICY_FILE)
    clauses: list[str] = []
    params: list[str] = []
    if exact_sources:
        placeholders = ", ".join("?" for _ in exact_sources)
        clauses.append(f"{column_name} IN ({placeholders})")
        params.extend(exact_sources)
    for prefix in prefixes:
        clauses.append(f"{column_name} LIKE ?")
        params.append(f"{prefix}%")
    if not clauses:
        return "(1 = 0)", ()
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def _is_cost_eligible_source(source: str | None) -> bool:
    return is_cost_eligible_source(source, policy_file=DEFAULT_POLICY_FILE)


def _is_diagnostic_only_source(source: str | None) -> bool:
    return is_diagnostic_only_source(source, policy_file=DEFAULT_POLICY_FILE)


def _source_count_total(counts: Any) -> int:
    if not isinstance(counts, dict):
        return 0
    total = 0
    for value in counts.values():
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def _none_or_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "reached_1r_rate_pct": 0.0,
            "stop_before_1r_rate_pct": 0.0,
            "quick_stop_3m_rate_pct": 0.0,
            "ambiguous_stop_first_rate_pct": 0.0,
            "avg_max_r_multiple": 0.0,
            "avg_min_r_multiple": 0.0,
            "avg_gross_return_pct": 0.0,
            "avg_net_return_pct": 0.0,
        }
    count = len(rows)
    return {
        "trade_count": count,
        "reached_1r_rate_pct": _rate(rows, "reached_1r"),
        "stop_before_1r_rate_pct": _rate(rows, "stop_before_1r"),
        "quick_stop_3m_rate_pct": _rate(rows, "quick_stop_3m"),
        "ambiguous_stop_first_rate_pct": _rate(rows, "ambiguous_stop_first"),
        "avg_max_r_multiple": mean(float(row.get("max_r_multiple") or 0.0) for row in rows),
        "avg_min_r_multiple": mean(float(row.get("min_r_multiple") or 0.0) for row in rows),
        "avg_gross_return_pct": mean(float(row.get("gross_return_pct") or 0.0) for row in rows),
        "avg_net_return_pct": mean(float(row.get("net_return_pct") or 0.0) for row in rows),
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if bool(row.get(key))) / len(rows) * 100.0


def _date_gap_days(start: str, end: str) -> int | None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        return None
    return (end_date - start_date).days


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _market_date_from_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value)
    return parsed.date().isoformat() if parsed is not None else ""


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
