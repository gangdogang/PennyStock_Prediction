from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile

from ..config import AppSettings
from ..db import (
    fetch_latest_paper_strategy_runs,
    fetch_latest_paper_trading_run,
    fetch_latest_premkt_predictions,
    fetch_paper_orders,
    fetch_paper_positions,
    fetch_paper_run_snapshots,
    fetch_paper_trading_run_by_id,
)
from ..models import PaperOrder, PaperPosition, PaperTradingRun
from .paper_bucket_policy import bucket_uses_predictor_weight
from .trading_support import predicted_rank_band


@dataclass(frozen=True)
class PaperReportPaths:
    strategy_comparison: Path
    bucket_comparison: Path
    bucket_trade_diff: Path
    bucket_pair_diff: Path
    cohort_summary: Path
    execution_quality: Path
    trade_log: Path
    backtest_kpis: Path
    regime_split: Path
    predictor_kpis: Path
    run_manifest: Path
    performance_gate: Path


def paper_report_paths(export_dir: Path) -> PaperReportPaths:
    base = Path(export_dir)
    return PaperReportPaths(
        strategy_comparison=base / "paper_strategy_comparison.csv",
        bucket_comparison=base / "paper_bucket_comparison.csv",
        bucket_trade_diff=base / "paper_bucket_trade_diff.csv",
        bucket_pair_diff=base / "paper_bucket_pair_diff.csv",
        cohort_summary=base / "paper_cohort_summary.csv",
        execution_quality=base / "paper_execution_quality.csv",
        trade_log=base / "paper_trade_log.csv",
        backtest_kpis=base / "paper_backtest_kpis.csv",
        regime_split=base / "paper_regime_split.csv",
        predictor_kpis=base / "paper_predictor_kpis.csv",
        run_manifest=base / "run_manifest.json",
        performance_gate=base / "paper_performance_gate.json",
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def read_csv_header(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or [])


def archive_paper_performance_export(
    export_dir: Path,
    *,
    output_path: Path | None = None,
    archive_prefix: str = "paper_trading",
) -> Path:
    base = Path(export_dir)
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = base.parent / "paper_trading_archives" / f"paper_performance_{timestamp}.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_resolved = output_path.resolve()
    with ZipFile(output_resolved, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(base.iterdir()):
            if not path.is_file() or path.resolve() == output_resolved:
                continue
            archive.write(path, f"{archive_prefix}/{path.name}")
    return output_resolved


class PaperReportingService:
    def __init__(
        self,
        *,
        settings: AppSettings,
        export_dir: Path,
        write_csv: Callable[[Path, list[dict[str, object]]], None],
        strategy_labels: dict[str, str],
        bucket_labels: dict[str, str],
        primary_strategy_name: str,
        predictor_weighted_bucket: str,
        momentum_only_bucket: str,
        watchlist_blind_momentum_bucket: str,
        baseline_pct_strategy: str,
    ) -> None:
        self.settings = settings
        self.export_dir = export_dir
        self.paths = paper_report_paths(export_dir)
        self._write_csv = write_csv
        self.strategy_labels = strategy_labels
        self.bucket_labels = bucket_labels
        self.primary_strategy_name = primary_strategy_name
        self.predictor_weighted_bucket = predictor_weighted_bucket
        self.momentum_only_bucket = momentum_only_bucket
        self.watchlist_blind_momentum_bucket = watchlist_blind_momentum_bucket
        self.baseline_pct_strategy = baseline_pct_strategy
        self.comparison_buckets = (
            self.predictor_weighted_bucket,
            self.momentum_only_bucket,
            self.watchlist_blind_momentum_bucket,
        )
        self.pair_diff_bucket_pairs = (
            (self.predictor_weighted_bucket, self.momentum_only_bucket),
            (self.momentum_only_bucket, self.watchlist_blind_momentum_bucket),
            (self.predictor_weighted_bucket, self.watchlist_blind_momentum_bucket),
        )

    def export_strategy_comparison(self) -> Path:
        runs = fetch_latest_paper_strategy_runs(self.settings.database_path, limit=10)
        rows: list[dict[str, object]] = []
        for run in runs:
            rows.append(
                {
                    "strategy_name": run.strategy_name,
                    "strategy_label": self.strategy_labels.get(run.strategy_name, run.strategy_name),
                    "bucket": run.bucket,
                    "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                    "status": run.status,
                    "equity": run.equity,
                    "realized_pnl": run.realized_pnl,
                    "unrealized_pnl": run.unrealized_pnl,
                    "total_return_pct": run.total_return_pct,
                    "closed_trade_count": run.closed_trade_count,
                    "win_rate": run.win_rate,
                    "profit_factor": run.profit_factor,
                    "reward_risk_ratio": run.reward_risk_ratio,
                    "max_drawdown_pct": run.max_drawdown_pct,
                    "updated_at": run.updated_at.isoformat(),
                }
            )
        self._write_csv(self.paths.strategy_comparison, rows)
        return self.paths.strategy_comparison.resolve()

    def export_bucket_comparison(self) -> Path:
        runs = [
            fetch_latest_paper_trading_run(
                self.settings.database_path,
                self.primary_strategy_name,
                bucket=bucket,
            )
            for bucket in self.comparison_buckets
        ]
        rows = [
            {
                "strategy_name": run.strategy_name,
                "bucket": run.bucket,
                "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                "status": run.status,
                "equity": run.equity,
                "cash_balance": run.cash_balance,
                "realized_pnl": run.realized_pnl,
                "unrealized_pnl": run.unrealized_pnl,
                "total_return_pct": run.total_return_pct,
                "closed_trade_count": run.closed_trade_count,
                "win_rate": run.win_rate,
                "profit_factor": run.profit_factor,
                "reward_risk_ratio": run.reward_risk_ratio,
                "max_drawdown_pct": run.max_drawdown_pct,
                "updated_at": run.updated_at.isoformat(),
            }
            for run in runs
            if run is not None
        ]
        self._write_csv(self.paths.bucket_comparison, rows)
        return self.paths.bucket_comparison.resolve()

    def export_bucket_trade_diff(self) -> Path:
        predictor_run = fetch_latest_paper_trading_run(
            self.settings.database_path,
            self.primary_strategy_name,
            bucket=self.predictor_weighted_bucket,
        )
        momentum_run = fetch_latest_paper_trading_run(
            self.settings.database_path,
            self.primary_strategy_name,
            bucket=self.momentum_only_bucket,
        )
        rows: list[dict[str, object]] = []
        if predictor_run is not None and momentum_run is not None:
            predictor_positions = fetch_paper_positions(
                self.settings.database_path,
                predictor_run.run_id,
            )
            momentum_positions = fetch_paper_positions(
                self.settings.database_path,
                momentum_run.run_id,
            )
            rows = self._trade_diff_rows(
                predictor_positions=predictor_positions,
                momentum_positions=momentum_positions,
            )
        self._write_csv(self.paths.bucket_trade_diff, rows)
        return self.paths.bucket_trade_diff.resolve()

    def export_bucket_pair_diff(self) -> Path:
        rows: list[dict[str, object]] = []
        position_cache: dict[str, list[PaperPosition]] = {}
        for left_bucket, right_bucket in self.pair_diff_bucket_pairs:
            left_run = fetch_latest_paper_trading_run(
                self.settings.database_path,
                self.primary_strategy_name,
                bucket=left_bucket,
            )
            right_run = fetch_latest_paper_trading_run(
                self.settings.database_path,
                self.primary_strategy_name,
                bucket=right_bucket,
            )
            if left_run is None or right_run is None:
                continue
            for run in (left_run, right_run):
                if run.run_id not in position_cache:
                    position_cache[run.run_id] = fetch_paper_positions(
                        self.settings.database_path,
                        run.run_id,
                    )
            rows.extend(
                self._pair_trade_diff_rows(
                    left_bucket=left_bucket,
                    right_bucket=right_bucket,
                    left_positions=position_cache[left_run.run_id],
                    right_positions=position_cache[right_run.run_id],
                )
            )
        self._write_csv(self.paths.bucket_pair_diff, rows)
        return self.paths.bucket_pair_diff.resolve()

    def export_cohort_summary(self, run_id: str | None = None) -> Path:
        runs: list[PaperTradingRun] = []
        if run_id is not None:
            run = fetch_paper_trading_run_by_id(
                self.settings.database_path,
                run_id,
            )
            if run is not None:
                runs.append(run)
        else:
            for bucket in self.comparison_buckets:
                run = fetch_latest_paper_trading_run(
                    self.settings.database_path,
                    self.primary_strategy_name,
                    bucket=bucket,
                )
                if run is not None:
                    runs.append(run)
        grouped: dict[tuple[str, str, str, str, str, str, str, str], dict[str, object]] = {}
        for run in runs:
            positions = fetch_paper_positions(self.settings.database_path, run.run_id)
            for row in positions:
                holding_bucket = self._holding_bucket(row)
                key = (
                    run.bucket,
                    row.strategy_bucket or "unknown",
                    row.entry_label or "unknown",
                    predicted_rank_band(row.watchlist_rank_at_entry),
                    row.entry_phase,
                    holding_bucket,
                    row.exit_reason or ("open" if row.status == "OPEN" else "unknown"),
                    row.day_regime or "unknown",
                )
                if key not in grouped:
                    grouped[key] = {
                        "portfolio_bucket": key[0],
                        "portfolio_bucket_label": self.bucket_labels.get(key[0], key[0]),
                        "strategy_bucket": key[1],
                        "entry_label": key[2],
                        "predicted_rank_band": key[3],
                        "phase": key[4],
                        "holding_bucket": key[5],
                        "exit_reason": key[6],
                        "day_regime": key[7],
                        "trade_count": 0,
                        "closed_trade_count": 0,
                        "total_pnl": 0.0,
                        "average_pnl": 0.0,
                    }
                grouped[key]["trade_count"] = int(grouped[key]["trade_count"]) + 1
                if row.status == "CLOSED":
                    grouped[key]["closed_trade_count"] = int(grouped[key]["closed_trade_count"]) + 1
                grouped[key]["total_pnl"] = float(grouped[key]["total_pnl"]) + float(row.total_pnl)

        rows: list[dict[str, object]] = []
        for item in grouped.values():
            trade_count = int(item["trade_count"])
            total_pnl = float(item["total_pnl"])
            item["average_pnl"] = total_pnl / trade_count if trade_count > 0 else 0.0
            rows.append(item)
        rows.sort(
            key=lambda item: (
                str(item["portfolio_bucket"]),
                str(item["strategy_bucket"]),
                str(item["phase"]),
                str(item["day_regime"]),
                str(item["entry_label"]),
                str(item["exit_reason"]),
            )
        )
        self._write_csv(self.paths.cohort_summary, rows)
        return self.paths.cohort_summary.resolve()

    def export_execution_quality(self) -> Path:
        rows: list[dict[str, object]] = []
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        for run in self._latest_runs():
            orders = fetch_paper_orders(self.settings.database_path, run.run_id)
            for order in orders:
                if order.intent not in {"ENTRY", "ADD", "EXIT", "EXIT_PARTIAL", "TRIM"}:
                    continue
                side = "buy" if order.action == "BUY" else "sell"
                key = (run.strategy_name, run.bucket, order.market_phase)
                if key not in grouped:
                    grouped[key] = {
                        "strategy_name": run.strategy_name,
                        "strategy_label": self.strategy_labels.get(run.strategy_name, run.strategy_name),
                        "bucket": run.bucket,
                        "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                        "market_phase": order.market_phase,
                        "order_count": 0,
                        "buy_order_count": 0,
                        "sell_order_count": 0,
                        "partial_fill_count": 0,
                        "avg_buy_slippage_pct": 0.0,
                        "avg_sell_slippage_pct": 0.0,
                        "avg_slippage_pct": 0.0,
                        "total_transaction_cost": 0.0,
                    }
                item = grouped[key]
                item["order_count"] = int(item["order_count"]) + 1
                if order.fill_status == "PARTIAL":
                    item["partial_fill_count"] = int(item["partial_fill_count"]) + 1
                if side == "buy":
                    item["buy_order_count"] = int(item["buy_order_count"]) + 1
                else:
                    item["sell_order_count"] = int(item["sell_order_count"]) + 1
                item["total_transaction_cost"] = float(item["total_transaction_cost"]) + float(order.transaction_cost)
                item.setdefault("_buy_slippages", []).append(float(order.fill_slippage_pct or 0.0) if side == "buy" else None)
                item.setdefault("_sell_slippages", []).append(float(order.fill_slippage_pct or 0.0) if side == "sell" else None)
                item.setdefault("_all_slippages", []).append(float(order.fill_slippage_pct or 0.0))

        for item in grouped.values():
            buy_slippages = [value for value in item.pop("_buy_slippages", []) if value is not None]
            sell_slippages = [value for value in item.pop("_sell_slippages", []) if value is not None]
            all_slippages = [value for value in item.pop("_all_slippages", []) if value is not None]
            item["avg_buy_slippage_pct"] = sum(buy_slippages) / len(buy_slippages) if buy_slippages else 0.0
            item["avg_sell_slippage_pct"] = sum(sell_slippages) / len(sell_slippages) if sell_slippages else 0.0
            item["avg_slippage_pct"] = sum(all_slippages) / len(all_slippages) if all_slippages else 0.0
            rows.append(item)
        rows.sort(key=lambda item: (str(item["strategy_name"]), str(item["bucket"]), str(item["market_phase"])))
        self._write_csv(self.paths.execution_quality, rows)
        return self.paths.execution_quality.resolve()

    def export_trade_log(self) -> Path:
        rows: list[dict[str, object]] = []
        prediction_lookup = self._prediction_lookup()
        for run in self._latest_runs():
            positions = fetch_paper_positions(self.settings.database_path, run.run_id)
            orders = fetch_paper_orders(self.settings.database_path, run.run_id)
            orders_by_position: dict[str, list[PaperOrder]] = {}
            for order in orders:
                if not order.position_id:
                    continue
                orders_by_position.setdefault(order.position_id, []).append(order)
            for position in positions:
                position_orders = orders_by_position.get(position.position_id, [])
                position_orders.sort(key=lambda item: item.created_at)
                buy_orders = [row for row in position_orders if row.action == "BUY"]
                sell_orders = [row for row in position_orders if row.action == "SELL"]
                entry_order = buy_orders[0] if buy_orders else None
                prediction = prediction_lookup.get(position.symbol.upper(), {})
                uses_predictor = bucket_uses_predictor_weight(run.bucket)
                if uses_predictor:
                    predictor_score = self._reason_value(position.entry_reasons, "predictor_score")
                    predictor_weight = self._reason_value(position.entry_reasons, "predictor_weight")
                    if predictor_score is None:
                        predictor_score = float(prediction.get("score", 0.0) or 0.0)
                    if predictor_weight is None:
                        predictor_weight = self._predictor_weight_from_score(
                            predictor_score if predictor_score > 0.0 else None
                        )
                    prediction_source = str(prediction.get("source", "none"))
                    predicted = prediction_source == "premkt_prediction"
                else:
                    predictor_score = 0.0
                    predictor_weight = 0.0
                    prediction_source = "disabled"
                    predicted = False
                top_theme = ""
                themes = prediction.get("themes", []) if uses_predictor else []
                if themes:
                    top_theme = str(themes[0])
                hold_minutes = max(
                    ((position.closed_at or position.updated_at) - position.opened_at).total_seconds() / 60.0,
                    0.0,
                )
                planned_stop_price = (
                    entry_order.planned_stop_price
                    if entry_order is not None and entry_order.planned_stop_price is not None
                    else position.planned_stop_price
                )
                entry_price = entry_order.price if entry_order is not None else position.average_entry_price
                initial_quantity = entry_order.quantity if entry_order is not None else position.quantity
                planned_risk_dollars = max((entry_price or 0.0) - (planned_stop_price or 0.0), 0.0) * max(initial_quantity, 0)
                stop_slippages = [
                    ((order.planned_stop_price - order.price) / order.planned_stop_price) * 100.0
                    for order in sell_orders
                    if order.planned_stop_price is not None
                    and order.price < order.planned_stop_price
                    and any(tag in order.reasons for tag in ("stop_loss", "trailing_stop"))
                ]
                net_pnl = float(position.total_pnl)
                gross_pnl = net_pnl + float(position.fees_paid_total)
                rows.append(
                    {
                        "run_id": run.run_id,
                        "strategy_name": run.strategy_name,
                        "strategy_label": self.strategy_labels.get(run.strategy_name, run.strategy_name),
                        "bucket": run.bucket,
                        "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                        "symbol": position.symbol,
                        "status": position.status,
                        "predicted": predicted,
                        "predictor_score": predictor_score,
                        "predictor_weight": predictor_weight,
                        "prediction_generated_at": prediction.get("generated_at", ""),
                        "prediction_cutoff_at": prediction.get("cutoff_at", ""),
                        "prediction_source": prediction_source,
                        "top_theme": top_theme,
                        "entry_phase": position.entry_phase,
                        "entry_label": position.entry_label or "",
                        "strategy_bucket": position.strategy_bucket,
                        "day_regime": position.day_regime or "unknown",
                        "opened_at": position.opened_at.isoformat(),
                        "closed_at": position.closed_at.isoformat() if position.closed_at else "",
                        "hold_minutes": hold_minutes,
                        "hold_days": hold_minutes / (60.0 * 24.0),
                        "quantity": position.quantity,
                        "entry_price": entry_price,
                        "fill_reference_price": position.fill_reference_price,
                        "planned_stop_price": planned_stop_price,
                        "planned_risk_dollars": planned_risk_dollars,
                        "gross_pnl": gross_pnl,
                        "net_pnl": net_pnl,
                        "transaction_cost": float(position.fees_paid_total),
                        "r_multiple": net_pnl / planned_risk_dollars if planned_risk_dollars > 0 else None,
                        "stop_exit_slippage_pct": sum(stop_slippages) / len(stop_slippages) if stop_slippages else None,
                        "exit_reason": position.exit_reason or "",
                    }
                )
        rows.sort(key=lambda item: (str(item["strategy_name"]), str(item["bucket"]), str(item["opened_at"]), str(item["symbol"])))
        self._write_csv(self.paths.trade_log, rows)
        return self.paths.trade_log.resolve()

    def export_backtest_kpis(self) -> Path:
        trade_rows = self._load_trade_log_rows()
        rows: list[dict[str, object]] = []
        grouped = self._group_trade_rows(trade_rows)
        for run in self._latest_runs():
            run_rows = grouped.get(run.run_id, [])
            closed_rows = [row for row in run_rows if row["status"] == "CLOSED"]
            net_pnls = [float(row["net_pnl"]) for row in closed_rows]
            r_values = [float(row["r_multiple"]) for row in closed_rows if row["r_multiple"] not in {None, ""}]
            hold_minutes = [float(row["hold_minutes"]) for row in closed_rows]
            stop_slippage = [float(row["stop_exit_slippage_pct"]) for row in closed_rows if row["stop_exit_slippage_pct"] not in {None, ""}]
            sharpe = self._trade_sharpe(net_pnls)
            sharpe_ci_low, sharpe_ci_high = self._bootstrap_ci(net_pnls, self._trade_sharpe)
            mdd_ci_low, mdd_ci_high = self._monte_carlo_mdd_ci(
                initial_capital=run.initial_capital,
                net_pnls=net_pnls,
            )
            theme_concentration = self._theme_concentration_pct(run_rows)
            rows.append(
                {
                    "strategy_name": run.strategy_name,
                    "strategy_label": self.strategy_labels.get(run.strategy_name, run.strategy_name),
                    "bucket": run.bucket,
                    "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                    "closed_trade_count": run.closed_trade_count,
                    "win_rate": run.win_rate,
                    "profit_factor": run.profit_factor,
                    "expectancy_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
                    "max_drawdown_pct": run.max_drawdown_pct,
                    "mdd_ci_low": mdd_ci_low,
                    "mdd_ci_high": mdd_ci_high,
                    "sharpe_ratio": sharpe,
                    "sharpe_ci_low": sharpe_ci_low,
                    "sharpe_ci_high": sharpe_ci_high,
                    "avg_hold_minutes": (sum(hold_minutes) / len(hold_minutes)) if hold_minutes else 0.0,
                    "avg_hold_days": ((sum(hold_minutes) / len(hold_minutes)) / (60.0 * 24.0)) if hold_minutes else 0.0,
                    "one_winner_dependency_pct": self._one_winner_dependency_pct(net_pnls),
                    "avg_stop_slippage_pct": (sum(stop_slippage) / len(stop_slippage)) if stop_slippage else 0.0,
                    "time_under_water_minutes": self._time_under_water_minutes(run),
                    "theme_concentration_pct": theme_concentration,
                    "total_transaction_cost": run.total_transaction_cost,
                    "gross_return_pct": ((run.equity + run.total_transaction_cost - run.initial_capital) / run.initial_capital) * 100.0 if run.initial_capital > 0 else 0.0,
                    "cost_adjusted_return_pct": run.total_return_pct,
                    "excess_sharpe_vs_baseline": 0.0,
                }
            )
        benchmark_sharpe = next(
            (float(row["sharpe_ratio"]) for row in rows if row["strategy_name"] == self.baseline_pct_strategy),
            0.0,
        )
        for row in rows:
            row["excess_sharpe_vs_baseline"] = float(row["sharpe_ratio"]) - benchmark_sharpe
        rows.sort(key=lambda item: (str(item["strategy_name"]), str(item["bucket"])))
        self._write_csv(self.paths.backtest_kpis, rows)
        return self.paths.backtest_kpis.resolve()

    def export_regime_split(self) -> Path:
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        for row in self._load_trade_log_rows():
            regime_bucket = self._regime_bucket(str(row["day_regime"]))
            key = (str(row["strategy_name"]), str(row["bucket"]), regime_bucket)
            if key not in grouped:
                grouped[key] = {
                    "strategy_name": key[0],
                    "strategy_label": self.strategy_labels.get(key[0], key[0]),
                    "bucket": key[1],
                    "bucket_label": self.bucket_labels.get(key[1], key[1]),
                    "regime_bucket": key[2],
                    "trade_count": 0,
                    "closed_trade_count": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                    "average_pnl": 0.0,
                    "expectancy_r": 0.0,
                    "avg_hold_minutes": 0.0,
                }
            item = grouped[key]
            item["trade_count"] = int(item["trade_count"]) + 1
            if row["status"] == "CLOSED":
                item["closed_trade_count"] = int(item["closed_trade_count"]) + 1
            item["total_pnl"] = float(item["total_pnl"]) + float(row["net_pnl"])
            item.setdefault("_wins", []).append(float(row["net_pnl"]) > 0)
            if row["r_multiple"] not in {None, ""}:
                item.setdefault("_r_values", []).append(float(row["r_multiple"]))
            item.setdefault("_holds", []).append(float(row["hold_minutes"]))
        rows: list[dict[str, object]] = []
        for item in grouped.values():
            trade_count = int(item["trade_count"])
            wins = [value for value in item.pop("_wins", []) if value]
            r_values = item.pop("_r_values", [])
            holds = item.pop("_holds", [])
            item["win_rate"] = (len(wins) / trade_count) * 100.0 if trade_count > 0 else 0.0
            item["average_pnl"] = float(item["total_pnl"]) / trade_count if trade_count > 0 else 0.0
            item["expectancy_r"] = sum(r_values) / len(r_values) if r_values else 0.0
            item["avg_hold_minutes"] = sum(holds) / len(holds) if holds else 0.0
            rows.append(item)
        rows.sort(key=lambda item: (str(item["strategy_name"]), str(item["bucket"]), str(item["regime_bucket"])))
        self._write_csv(self.paths.regime_split, rows)
        return self.paths.regime_split.resolve()

    def export_predictor_kpis(self) -> Path:
        trade_rows = self._load_trade_log_rows()
        prediction_lookup = self._prediction_lookup()
        candidate_count = len(prediction_lookup)
        rows: list[dict[str, object]] = []
        grouped = self._group_trade_rows(trade_rows)
        for run in self._latest_runs():
            run_rows = grouped.get(run.run_id, [])
            predicted_rows = [row for row in run_rows if self._csv_bool(row.get("predicted"))]
            closed_predicted = [row for row in predicted_rows if row["status"] == "CLOSED"]
            triggered_symbols = {str(row["symbol"]) for row in predicted_rows}
            day1_r = [float(row["r_multiple"]) for row in closed_predicted if row["r_multiple"] not in {None, ""} and float(row["hold_days"]) < 1.0]
            day2_r = [float(row["r_multiple"]) for row in closed_predicted if row["r_multiple"] not in {None, ""} and 1.0 <= float(row["hold_days"]) < 2.0]
            day3_r = [float(row["r_multiple"]) for row in closed_predicted if row["r_multiple"] not in {None, ""} and float(row["hold_days"]) >= 2.0]
            winning_predicted = [row for row in closed_predicted if float(row["net_pnl"]) > 0]
            rows.append(
                {
                    "strategy_name": run.strategy_name,
                    "strategy_label": self.strategy_labels.get(run.strategy_name, run.strategy_name),
                    "bucket": run.bucket,
                    "bucket_label": self.bucket_labels.get(run.bucket, run.bucket),
                    "candidate_count": candidate_count,
                    "triggered_candidate_count": len(triggered_symbols),
                    "candidate_survival_rate_pct": (len(triggered_symbols) / candidate_count) * 100.0 if candidate_count > 0 else 0.0,
                    "predicted_trade_count": len(predicted_rows),
                    "closed_predicted_trade_count": len(closed_predicted),
                    "predictor_hit_rate_pct": (len(winning_predicted) / len(closed_predicted)) * 100.0 if closed_predicted else 0.0,
                    "edge_decay_day1_avg_r": sum(day1_r) / len(day1_r) if day1_r else 0.0,
                    "edge_decay_day2_avg_r": sum(day2_r) / len(day2_r) if day2_r else 0.0,
                    "edge_decay_day3_avg_r": sum(day3_r) / len(day3_r) if day3_r else 0.0,
                }
            )
        rows.sort(key=lambda item: (str(item["strategy_name"]), str(item["bucket"])))
        self._write_csv(self.paths.predictor_kpis, rows)
        return self.paths.predictor_kpis.resolve()

    def export_run_manifest(self) -> Path:
        trade_rows = self._load_trade_log_rows()
        opened_at = sorted(str(row["opened_at"]) for row in trade_rows if row.get("opened_at"))
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "export_dir": str(self.export_dir.resolve()),
            "database_path": str(self.settings.database_path.resolve()),
            "git_sha": self._git_sha(),
            "settings_hash": self._settings_hash(),
            "trade_period": {
                "first_opened_at": opened_at[0] if opened_at else None,
                "last_opened_at": opened_at[-1] if opened_at else None,
            },
            "runs": [
                {
                    "run_id": run.run_id,
                    "strategy_name": run.strategy_name,
                    "bucket": run.bucket,
                    "status": run.status,
                    "last_market_date": run.last_market_date,
                    "closed_trade_count": run.closed_trade_count,
                    "total_return_pct": run.total_return_pct,
                    "updated_at": run.updated_at.isoformat(),
                }
                for run in self._latest_runs()
            ],
            "predictor_effect": {
                "paper_predictor_weight_k1": self.settings.paper_predictor_weight_k1,
                "paper_predictor_weight_k2": self.settings.paper_predictor_weight_k2,
                "enabled": (
                    self.settings.paper_predictor_weight_k1 != 0.0
                    or self.settings.paper_predictor_weight_k2 != 0.0
                ),
            },
            "artifacts": {
                "paper_backtest_kpis": self.paths.backtest_kpis.name,
                "paper_trade_log": self.paths.trade_log.name,
                "paper_bucket_trade_diff": self.paths.bucket_trade_diff.name,
                "paper_bucket_pair_diff": self.paths.bucket_pair_diff.name,
                "paper_predictor_kpis": self.paths.predictor_kpis.name,
                "paper_execution_quality": self.paths.execution_quality.name,
            },
        }
        self.paths.run_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.paths.run_manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.paths.run_manifest.resolve()

    def export_performance_review_gate(self) -> Path:
        payload = self.evaluate_performance_review_gate()
        self.paths.performance_gate.parent.mkdir(parents=True, exist_ok=True)
        self.paths.performance_gate.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.paths.performance_gate.resolve()

    def evaluate_performance_review_gate(self) -> dict[str, object]:
        trade_rows = self._load_trade_log_rows()
        predictor_rows = read_csv_rows(self.paths.predictor_kpis)
        bucket_rows = read_csv_rows(self.paths.bucket_comparison)
        diff_rows = read_csv_rows(self.paths.bucket_trade_diff)
        pair_diff_rows = read_csv_rows(self.paths.bucket_pair_diff)
        failures: list[str] = []
        warnings: list[str] = []
        required_artifacts = {
            "run_manifest.json": self.paths.run_manifest,
            "paper_backtest_kpis.csv": self.paths.backtest_kpis,
            "paper_trade_log.csv": self.paths.trade_log,
            "paper_bucket_trade_diff.csv": self.paths.bucket_trade_diff,
            "paper_bucket_pair_diff.csv": self.paths.bucket_pair_diff,
            "paper_predictor_kpis.csv": self.paths.predictor_kpis,
            "paper_execution_quality.csv": self.paths.execution_quality,
            "paper_bucket_comparison.csv": self.paths.bucket_comparison,
        }
        for artifact_name, artifact_path in required_artifacts.items():
            if not artifact_path.exists() or artifact_path.stat().st_size == 0:
                failures.append(f"{artifact_name} is missing or empty")

        closed_rows = [row for row in trade_rows if row.get("status") == "CLOSED"]
        required_trade_columns = {
            "predictor_score",
            "predictor_weight",
            "prediction_generated_at",
            "prediction_cutoff_at",
            "prediction_source",
        }
        if closed_rows:
            present_trade_columns = set(read_csv_header(self.paths.trade_log))
            missing_columns = sorted(required_trade_columns - present_trade_columns)
            if missing_columns:
                failures.append(
                    "paper_trade_log.csv is missing lineage columns: "
                    + ", ".join(missing_columns)
                )
        missing_lineage = [
            row
            for row in closed_rows
            if str(row.get("predictor_score") or "").strip() == ""
            or str(row.get("predictor_weight") or "").strip() == ""
        ]
        if missing_lineage:
            failures.append(
                f"closed trades with blank predictor_score/predictor_weight: {len(missing_lineage)}"
            )

        inconsistent_candidate_rows = [
            row
            for row in predictor_rows
            if self._float_value(row.get("candidate_count")) == 0.0
            and self._float_value(row.get("triggered_candidate_count")) > 0.0
        ]
        if inconsistent_candidate_rows:
            failures.append(
                "candidate_count is 0 while triggered_candidate_count is positive"
            )

        if bucket_rows:
            present_buckets = {
                str(row.get("bucket"))
                for row in bucket_rows
                if row.get("strategy_name") == self.primary_strategy_name
            }
            missing_buckets = [
                bucket
                for bucket in self.comparison_buckets
                if bucket not in present_buckets
            ]
            if missing_buckets:
                failures.append(
                    "adaptive bucket comparison is missing buckets: "
                    + ", ".join(missing_buckets)
                )
        else:
            failures.append("paper_bucket_comparison.csv is missing or empty")

        predictor_closed = [
            row
            for row in closed_rows
            if row.get("strategy_name") == self.primary_strategy_name
            and row.get("bucket") == self.predictor_weighted_bucket
        ]
        momentum_closed = [
            row
            for row in closed_rows
            if row.get("strategy_name") == self.primary_strategy_name
            and row.get("bucket") == self.momentum_only_bucket
        ]
        if predictor_closed and momentum_closed and diff_rows:
            nonzero_diff = [
                row
                for row in diff_rows
                if abs(self._float_value(row.get("pnl_diff"))) > 1e-9
                or str(row.get("predictor_status") or "") != str(row.get("momentum_status") or "")
            ]
            if not nonzero_diff:
                if self._has_primary_predictor_evidence(predictor_rows, predictor_closed):
                    failures.append(
                        "predictor candidates exist but predictor_weighted and momentum_only closed trades are identical"
                    )
                else:
                    warnings.append(
                        "predictor_weighted and momentum_only closed trades are identical"
                    )

        blind_trade_rows = [
            row
            for row in trade_rows
            if row.get("strategy_name") == self.primary_strategy_name
            and row.get("bucket") == self.watchlist_blind_momentum_bucket
        ]
        if not blind_trade_rows:
            failures.append("watchlist_blind_momentum has zero trades")

        if not self.paths.bucket_pair_diff.exists() or not pair_diff_rows:
            if "paper_bucket_pair_diff.csv is missing or empty" not in failures:
                failures.append("paper_bucket_pair_diff.csv is missing or empty")
        else:
            expected_pairs = {
                (left_bucket, right_bucket)
                for left_bucket, right_bucket in self.pair_diff_bucket_pairs
            }
            present_pairs = {
                (str(row.get("left_bucket")), str(row.get("right_bucket")))
                for row in pair_diff_rows
            }
            missing_pairs = sorted(expected_pairs - present_pairs)
            if missing_pairs:
                failures.append(
                    "paper_bucket_pair_diff.csv is missing pairs: "
                    + ", ".join(f"{left} vs {right}" for left, right in missing_pairs)
                )
            for left_bucket, right_bucket in sorted(expected_pairs & present_pairs):
                rows_for_pair = [
                    row
                    for row in pair_diff_rows
                    if row.get("left_bucket") == left_bucket
                    and row.get("right_bucket") == right_bucket
                ]
                if not rows_for_pair:
                    continue
                nonzero_or_status_diff = [
                    row
                    for row in rows_for_pair
                    if abs(self._float_value(row.get("pnl_diff"))) > 1e-9
                    or str(row.get("left_status") or "") != str(row.get("right_status") or "")
                ]
                if not nonzero_or_status_diff:
                    warnings.append(f"{left_bucket} and {right_bucket} pair diff is identical")

        if not self.paths.run_manifest.exists():
            if "run_manifest.json is missing or empty" not in failures:
                failures.append("run_manifest.json is missing")

        return {
            "status": "fail" if failures else "pass",
            "failures": failures,
            "warnings": warnings,
            "warning_count": len(warnings),
            "required_buckets": list(self.comparison_buckets),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "closed_trade_count": len(closed_rows),
            "trade_log_path": str(self.paths.trade_log),
            "predictor_kpis_path": str(self.paths.predictor_kpis),
            "bucket_trade_diff_path": str(self.paths.bucket_trade_diff),
            "bucket_pair_diff_path": str(self.paths.bucket_pair_diff),
            "run_manifest_path": str(self.paths.run_manifest),
        }

    def _latest_runs(self) -> list[PaperTradingRun]:
        return fetch_latest_paper_strategy_runs(self.settings.database_path, limit=10)

    def _load_trade_log_rows(self) -> list[dict[str, object]]:
        path = self.paths.trade_log
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _group_trade_rows(self, trade_rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in trade_rows:
            grouped.setdefault(str(row["run_id"]), []).append(row)
        return grouped

    def _prediction_lookup(self) -> dict[str, dict[str, object]]:
        rows = fetch_latest_premkt_predictions(
            self.settings.database_path,
            limit=1000,
            prefer_reportable=False,
        )
        lookup: dict[str, dict[str, object]] = {}
        for row in rows:
            raw_themes = row["themes"] if "themes" in row.keys() else "[]"
            try:
                themes = json.loads(raw_themes) if raw_themes else []
            except Exception:
                themes = []
            lookup[str(row["symbol"]).upper()] = {
                "score": float(row["score"]),
                "themes": themes,
                "generated_at": str(row["generated_at"]) if "generated_at" in row.keys() else "",
                "cutoff_at": (
                    str(row["cutoff_at"])
                    if "cutoff_at" in row.keys() and row["cutoff_at"]
                    else str(row["generated_at"]) if "generated_at" in row.keys() else ""
                ),
                "source": (
                    str(row["source"])
                    if "source" in row.keys() and row["source"]
                    else "premkt_prediction"
                ),
            }
        return lookup

    def _has_primary_predictor_evidence(
        self,
        predictor_rows: list[dict[str, str]],
        predictor_closed: list[dict[str, object]],
    ) -> bool:
        evidence_counts = [
            max(
                self._float_value(row.get("candidate_count")),
                self._float_value(row.get("triggered_candidate_count")),
                self._float_value(row.get("predicted_trade_count")),
                self._float_value(row.get("closed_predicted_trade_count")),
            )
            for row in predictor_rows
            if row.get("strategy_name") == self.primary_strategy_name
            and row.get("bucket") == self.predictor_weighted_bucket
        ]
        return any(value > 0.0 for value in evidence_counts) or any(
            self._csv_bool(row.get("predicted"))
            for row in predictor_closed
        )

    def _reason_value(self, reasons: list[str], prefix: str) -> float | None:
        needle = f"{prefix}:"
        for reason in reasons:
            if reason.startswith(needle):
                try:
                    return float(reason[len(needle) :])
                except ValueError:
                    return None
        return None

    def _predictor_weight_from_score(self, score: float | None) -> float:
        if score is None or score <= 40.0:
            return 0.0
        if score >= 80.0:
            return 1.0
        return (float(score) - 40.0) / 40.0

    def _csv_bool(self, value: object) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _float_value(self, value: object) -> float:
        try:
            if value in {None, ""}:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _settings_hash(self) -> str:
        payload = json.dumps(
            self.settings.model_dump(mode="json"),
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _git_sha(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[3],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return ""
        return result.stdout.strip()

    def _regime_bucket(self, regime: str) -> str:
        normalized = regime.strip().lower()
        if normalized == "hot":
            return "trend"
        if normalized == "cold":
            return "chop"
        return "chop"

    def _theme_concentration_pct(self, trade_rows: list[dict[str, object]]) -> float:
        themes = [str(row["top_theme"]) for row in trade_rows if str(row["top_theme"])]
        if not themes:
            return 0.0
        counts = Counter(themes)
        return (counts.most_common(1)[0][1] / len(themes)) * 100.0

    def _one_winner_dependency_pct(self, net_pnls: list[float]) -> float:
        if not net_pnls:
            return 0.0
        total = sum(net_pnls)
        top_win = max(net_pnls)
        if top_win <= 0:
            return 0.0
        basis = abs(total) if abs(total) > 1e-9 else abs(top_win)
        return (top_win / basis) * 100.0 if basis > 0 else 0.0

    def _time_under_water_minutes(self, run: PaperTradingRun) -> float:
        snapshots = fetch_paper_run_snapshots(self.settings.database_path, run.run_id)
        if not snapshots:
            return 0.0
        peak = -math.inf
        underwater_start: datetime | None = None
        max_minutes = 0.0
        for row in snapshots:
            if row.equity >= peak:
                peak = row.equity
                underwater_start = None
                continue
            if underwater_start is None:
                underwater_start = row.created_at
            max_minutes = max(
                max_minutes,
                (row.created_at - underwater_start).total_seconds() / 60.0,
            )
        return max_minutes

    def _trade_sharpe(self, net_pnls: list[float]) -> float:
        if len(net_pnls) < 2:
            return 0.0
        mean = sum(net_pnls) / len(net_pnls)
        variance = sum((value - mean) ** 2 for value in net_pnls) / (len(net_pnls) - 1)
        std = math.sqrt(variance)
        if std <= 0:
            return 0.0
        return (mean / std) * math.sqrt(len(net_pnls))

    def _bootstrap_ci(
        self,
        values: list[float],
        metric_fn: Callable[[list[float]], float],
        *,
        samples: int = 200,
    ) -> tuple[float, float]:
        if not values:
            return (0.0, 0.0)
        rng = random.Random(0)
        metrics: list[float] = []
        for _ in range(samples):
            sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
            metrics.append(metric_fn(sample))
        metrics.sort()
        low_index = max(int(samples * 0.05) - 1, 0)
        high_index = min(int(samples * 0.95), samples - 1)
        return metrics[low_index], metrics[high_index]

    def _monte_carlo_mdd_ci(
        self,
        *,
        initial_capital: float,
        net_pnls: list[float],
        samples: int = 200,
    ) -> tuple[float, float]:
        if not net_pnls:
            return (0.0, 0.0)
        rng = random.Random(0)
        drawdowns: list[float] = []
        for _ in range(samples):
            sample = list(net_pnls)
            rng.shuffle(sample)
            drawdowns.append(self._mdd_from_pnls(initial_capital=initial_capital, net_pnls=sample))
        drawdowns.sort()
        low_index = max(int(samples * 0.05) - 1, 0)
        high_index = min(int(samples * 0.95), samples - 1)
        return drawdowns[low_index], drawdowns[high_index]

    def _mdd_from_pnls(self, *, initial_capital: float, net_pnls: list[float]) -> float:
        equity = initial_capital
        peak = initial_capital
        max_drawdown = 0.0
        for pnl in net_pnls:
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, ((peak - equity) / peak) * 100.0)
        return max_drawdown

    def _holding_bucket(self, position: PaperPosition) -> str:
        if position.closed_at is None:
            return "open"
        held_minutes = max((position.closed_at - position.opened_at).total_seconds() / 60.0, 0.0)
        if held_minutes < 30:
            return "<30m"
        if held_minutes < 120:
            return "30-120m"
        return ">120m"

    def _trade_diff_rows(
        self,
        *,
        predictor_positions: list[PaperPosition],
        momentum_positions: list[PaperPosition],
    ) -> list[dict[str, object]]:
        left_grouped = self._positions_by_symbol_occurrence(predictor_positions)
        right_grouped = self._positions_by_symbol_occurrence(momentum_positions)
        rows: list[dict[str, object]] = []
        for symbol in sorted(set(left_grouped) | set(right_grouped)):
            left_rows = left_grouped.get(symbol, [])
            right_rows = right_grouped.get(symbol, [])
            trade_count = max(len(left_rows), len(right_rows))
            for index in range(trade_count):
                left = left_rows[index] if index < len(left_rows) else None
                right = right_rows[index] if index < len(right_rows) else None
                left_pnl = left.total_pnl if left is not None else None
                right_pnl = right.total_pnl if right is not None else None
                rows.append(
                    {
                        "symbol": symbol,
                        "trade_index": index + 1,
                        "predictor_status": left.status if left is not None else "",
                        "predictor_entry_label": left.entry_label if left is not None else "",
                        "predictor_entry_at": left.opened_at.isoformat() if left is not None else "",
                        "predictor_exit_reason": left.exit_reason if left is not None else "",
                        "predictor_total_pnl": left_pnl,
                        "predictor_strategy_bucket": left.strategy_bucket if left is not None else "",
                        "momentum_status": right.status if right is not None else "",
                        "momentum_entry_label": right.entry_label if right is not None else "",
                        "momentum_entry_at": right.opened_at.isoformat() if right is not None else "",
                        "momentum_exit_reason": right.exit_reason if right is not None else "",
                        "momentum_total_pnl": right_pnl,
                        "momentum_strategy_bucket": right.strategy_bucket if right is not None else "",
                        "pnl_diff": ((left_pnl or 0.0) - (right_pnl or 0.0)) if left_pnl is not None or right_pnl is not None else None,
                    }
                )
        return rows

    def _pair_trade_diff_rows(
        self,
        *,
        left_bucket: str,
        right_bucket: str,
        left_positions: list[PaperPosition],
        right_positions: list[PaperPosition],
    ) -> list[dict[str, object]]:
        left_grouped = self._positions_by_symbol_occurrence(left_positions)
        right_grouped = self._positions_by_symbol_occurrence(right_positions)
        rows: list[dict[str, object]] = []
        for symbol in sorted(set(left_grouped) | set(right_grouped)):
            left_rows = left_grouped.get(symbol, [])
            right_rows = right_grouped.get(symbol, [])
            trade_count = max(len(left_rows), len(right_rows))
            for index in range(trade_count):
                left = left_rows[index] if index < len(left_rows) else None
                right = right_rows[index] if index < len(right_rows) else None
                left_pnl = left.total_pnl if left is not None else None
                right_pnl = right.total_pnl if right is not None else None
                rows.append(
                    {
                        "left_bucket": left_bucket,
                        "left_bucket_label": self.bucket_labels.get(left_bucket, left_bucket),
                        "right_bucket": right_bucket,
                        "right_bucket_label": self.bucket_labels.get(right_bucket, right_bucket),
                        "symbol": symbol,
                        "trade_index": index + 1,
                        "left_status": left.status if left is not None else "",
                        "left_entry_label": left.entry_label if left is not None else "",
                        "left_entry_at": left.opened_at.isoformat() if left is not None else "",
                        "left_exit_reason": left.exit_reason if left is not None else "",
                        "left_total_pnl": left_pnl,
                        "left_strategy_bucket": left.strategy_bucket if left is not None else "",
                        "right_status": right.status if right is not None else "",
                        "right_entry_label": right.entry_label if right is not None else "",
                        "right_entry_at": right.opened_at.isoformat() if right is not None else "",
                        "right_exit_reason": right.exit_reason if right is not None else "",
                        "right_total_pnl": right_pnl,
                        "right_strategy_bucket": right.strategy_bucket if right is not None else "",
                        "pnl_diff": ((left_pnl or 0.0) - (right_pnl or 0.0)) if left_pnl is not None or right_pnl is not None else None,
                    }
                )
        return rows

    def _positions_by_symbol_occurrence(
        self,
        positions: list[PaperPosition],
    ) -> dict[str, list[PaperPosition]]:
        grouped: dict[str, list[PaperPosition]] = {}
        for row in sorted(positions, key=lambda item: (item.symbol, item.opened_at, item.position_id)):
            grouped.setdefault(row.symbol, []).append(row)
        return grouped
