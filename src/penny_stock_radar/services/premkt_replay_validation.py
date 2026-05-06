from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
)

DISPLAY_BUCKETS = {
    PREDICTOR_WEIGHTED_BUCKET: "predictor_weighted",
    MOMENTUM_ONLY_BUCKET: "watchlist_momentum",
    WATCHLIST_BLIND_MOMENTUM_BUCKET: "watchlist_blind_momentum",
}
CANONICAL_BUCKETS = {value: key for key, value in DISPLAY_BUCKETS.items()}
CANONICAL_BUCKETS[MOMENTUM_ONLY_BUCKET] = MOMENTUM_ONLY_BUCKET

COMPARISON_PAIRS = (
    ("predictor_weighted", "watchlist_momentum"),
    ("predictor_weighted", "watchlist_blind_momentum"),
    ("watchlist_momentum", "watchlist_blind_momentum"),
)

REQUIRED_REPORT_KEYS = (
    "created_at",
    "calibration_run_dir",
    "out_of_sample_run_dir",
    "calibration_period",
    "out_of_sample_period",
    "model_path",
    "score_mode",
    "ml_weight",
    "calibration_summary",
    "out_of_sample_summary",
    "bucket_comparison",
    "data_coverage_summary",
    "leakage_guard_summary",
    "cost_and_fill_summary",
    "decision_gate",
    "warnings",
    "next_actions",
)


@dataclass(frozen=True)
class ReplayValidationResult:
    report: dict[str, Any]
    output_path: Path | None


@dataclass
class _RunArtifacts:
    run_dir: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]
    kpi_rows: list[dict[str, str]]
    trade_rows: list[dict[str, str]]
    execution_rows: list[dict[str, str]]
    pair_rows: list[dict[str, str]]
    missing_artifacts: list[str]


class PremktReplayValidationEvaluator:
    """Evaluate calibration/OOS historical replay outputs without rerunning live paths."""

    def evaluate(
        self,
        *,
        calibration_dir: Path,
        out_of_sample_dir: Path,
        output_path: Path | None = None,
    ) -> ReplayValidationResult:
        calibration = self._load_run(Path(calibration_dir))
        out_of_sample = self._load_run(Path(out_of_sample_dir))
        warnings: list[str] = []
        warnings.extend(self._artifact_warnings("calibration", calibration))
        warnings.extend(self._artifact_warnings("out_of_sample", out_of_sample))

        calibration_period = self._period_summary(calibration)
        out_of_sample_period = self._period_summary(out_of_sample)
        calibration_buckets = self._bucket_metrics(calibration)
        out_of_sample_buckets = self._bucket_metrics(out_of_sample)
        bucket_comparison = self._bucket_comparison(
            calibration_buckets=calibration_buckets,
            out_of_sample_buckets=out_of_sample_buckets,
        )
        data_coverage = {
            "calibration": self._coverage_summary(calibration),
            "out_of_sample": self._coverage_summary(out_of_sample),
        }
        leakage_guard = {
            "calibration": self._leakage_guard_summary(calibration),
            "out_of_sample": self._leakage_guard_summary(out_of_sample),
        }
        parameter_guard = self._parameter_guard_summary(
            calibration=calibration,
            out_of_sample=out_of_sample,
            calibration_period=calibration_period,
        )
        warnings.extend(parameter_guard["warnings"])
        for scope, payload in leakage_guard.items():
            warnings.extend(f"{scope}: {message}" for message in payload["warnings"])
        cost_and_fill = {
            "calibration": self._cost_and_fill_summary(calibration),
            "out_of_sample": self._cost_and_fill_summary(out_of_sample),
        }
        warnings.extend(
            self._coverage_warnings_for_report("calibration", data_coverage["calibration"])
        )
        warnings.extend(
            self._coverage_warnings_for_report("out_of_sample", data_coverage["out_of_sample"])
        )

        decision_gate = self._decision_gate(
            calibration_period=calibration_period,
            out_of_sample_period=out_of_sample_period,
            calibration_buckets=calibration_buckets,
            out_of_sample_buckets=out_of_sample_buckets,
            data_coverage=data_coverage,
            leakage_guard=leakage_guard,
            parameter_guard=parameter_guard,
            missing_artifacts=calibration.missing_artifacts + out_of_sample.missing_artifacts,
        )
        report: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "calibration_run_dir": str(calibration.run_dir),
            "out_of_sample_run_dir": str(out_of_sample.run_dir),
            "calibration_period": calibration_period,
            "out_of_sample_period": out_of_sample_period,
            "model_path": self._first_present("model_path", out_of_sample, calibration),
            "score_mode": self._first_present("score_mode", out_of_sample, calibration),
            "ml_weight": self._first_present("ml_weight", out_of_sample, calibration),
            "calibration_summary": self._run_summary(calibration, calibration_buckets),
            "out_of_sample_summary": self._run_summary(out_of_sample, out_of_sample_buckets),
            "bucket_comparison": bucket_comparison,
            "data_coverage_summary": data_coverage,
            "leakage_guard_summary": leakage_guard,
            "cost_and_fill_summary": cost_and_fill,
            "decision_gate": decision_gate,
            "warnings": sorted(set(warnings)),
            "next_actions": self._next_actions(decision_gate["status"]),
        }

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return ReplayValidationResult(report=report, output_path=output_path)

    def _load_run(self, run_dir: Path) -> _RunArtifacts:
        missing: list[str] = []
        manifest = self._load_json(run_dir / "run_manifest.json", missing)
        summary = self._load_json(run_dir / "replay_summary.json", missing)
        kpi_rows = self._read_csv(run_dir / "paper_backtest_kpis.csv", missing)
        trade_rows = self._read_csv(run_dir / "paper_trade_log.csv", missing, allow_missing=True)
        execution_rows = self._read_csv(
            run_dir / "paper_execution_quality.csv",
            missing,
            allow_missing=True,
        )
        pair_rows = self._read_csv(
            run_dir / "paper_bucket_pair_diff.csv",
            missing,
            allow_missing=True,
        )
        return _RunArtifacts(
            run_dir=run_dir,
            manifest=manifest,
            summary=summary,
            kpi_rows=kpi_rows,
            trade_rows=trade_rows,
            execution_rows=execution_rows,
            pair_rows=pair_rows,
            missing_artifacts=missing,
        )

    def _load_json(self, path: Path, missing: list[str]) -> dict[str, Any]:
        if not path.exists() or path.stat().st_size == 0:
            missing.append(path.name)
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing.append(path.name)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_csv(
        self,
        path: Path,
        missing: list[str],
        *,
        allow_missing: bool = False,
    ) -> list[dict[str, str]]:
        if not path.exists() or path.stat().st_size == 0:
            if not allow_missing:
                missing.append(path.name)
            return []
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(_non_comment_lines(handle)))
        except OSError:
            if not allow_missing:
                missing.append(path.name)
            return []

    def _period_summary(self, artifacts: _RunArtifacts) -> dict[str, Any]:
        start = self._first_period_value("start_date", artifacts)
        end = self._first_period_value("end_date", artifacts)
        completed_dates = self._summary_list(artifacts.summary.get("completed_dates"))
        start_date = date.fromisoformat(start) if start else None
        end_date = date.fromisoformat(end) if end else None
        calendar_day_count = (
            ((end_date - start_date).days + 1)
            if start_date is not None and end_date is not None
            else 0
        )
        return {
            "start_date": start,
            "end_date": end,
            "calendar_day_count": calendar_day_count,
            "completed_date_count": len(completed_dates),
            "completed_dates": completed_dates,
            "is_at_least_three_calendar_months": (
                self._is_at_least_three_months(start_date, end_date)
                if start_date is not None and end_date is not None
                else False
            ),
        }

    def _first_period_value(self, key: str, artifacts: _RunArtifacts) -> str | None:
        value = artifacts.summary.get(key) or artifacts.manifest.get(key)
        if value:
            return str(value)
        options = artifacts.manifest.get("options")
        if isinstance(options, dict) and options.get(key):
            return str(options[key])
        return None

    def _bucket_metrics(self, artifacts: _RunArtifacts) -> dict[str, dict[str, Any]]:
        metrics: dict[str, dict[str, Any]] = {}
        summary_buckets = artifacts.summary.get("bucket_results")
        if isinstance(summary_buckets, dict):
            for bucket, values in summary_buckets.items():
                display = DISPLAY_BUCKETS.get(str(bucket), str(bucket))
                if isinstance(values, dict):
                    metrics[display] = self._normalize_metric_row(values)

        for row in artifacts.kpi_rows:
            bucket = self._display_bucket(str(row.get("bucket") or row.get("strategy_bucket") or ""))
            if bucket:
                merged = metrics.get(bucket, {})
                merged.update(self._normalize_metric_row(row))
                metrics[bucket] = merged

        derived = self._derive_trade_metrics(artifacts)
        for bucket, values in derived.items():
            merged = metrics.get(bucket, {})
            for key, value in values.items():
                if value not in (None, ""):
                    merged[key] = value
            metrics[bucket] = self._normalize_metric_row(merged)

        for bucket in DISPLAY_BUCKETS.values():
            metrics.setdefault(bucket, self._empty_metrics())
        return metrics

    def _normalize_metric_row(self, row: dict[str, Any]) -> dict[str, Any]:
        closed_count = self._number(
            row.get("closed_trade_count"),
            row.get("trade_count"),
            row.get("closed_trades"),
            default=0.0,
        )
        return {
            "total_net_pnl": round(self._number(row.get("total_net_pnl"), default=0.0), 6),
            "total_return_pct": round(self._number(row.get("total_return_pct"), default=0.0), 6),
            "closed_trade_count": int(closed_count),
            "win_rate": round(self._rate(row.get("win_rate"), row.get("win_rate_pct")), 6),
            "profit_factor": round(self._number(row.get("profit_factor"), default=0.0), 6),
            "expectancy_r": round(
                self._number(row.get("expectancy_r"), row.get("avg_r_multiple"), default=0.0),
                6,
            ),
            "max_drawdown_pct": round(self._number(row.get("max_drawdown_pct"), default=0.0), 6),
            "average_slippage_pct": self._optional_number(row.get("average_slippage_pct")),
            "transaction_cost": round(self._number(row.get("transaction_cost"), default=0.0), 6),
            "capacity_limited_count": int(
                self._number(row.get("capacity_limited_count"), default=0.0)
            ),
            "partial_fill_count": int(self._number(row.get("partial_fill_count"), default=0.0)),
        }

    def _derive_trade_metrics(self, artifacts: _RunArtifacts) -> dict[str, dict[str, Any]]:
        initial_capital = self._number(
            artifacts.manifest.get("settings_snapshot", {}).get("paper_initial_capital")
            if isinstance(artifacts.manifest.get("settings_snapshot"), dict)
            else None,
            default=0.0,
        )
        stop_loss_pct = self._number(
            artifacts.manifest.get("settings_snapshot", {}).get("paper_stop_loss_pct")
            if isinstance(artifacts.manifest.get("settings_snapshot"), dict)
            else None,
            default=5.0,
        )
        grouped: dict[str, list[dict[str, str]]] = {}
        closed_rows = [
            row
            for row in artifacts.trade_rows
            if str(row.get("event") or "").upper() == "EXIT"
            or str(row.get("status") or "").upper() == "CLOSED"
        ]
        for row in closed_rows:
            bucket = self._display_bucket(str(row.get("bucket") or row.get("strategy_bucket") or ""))
            if bucket:
                grouped.setdefault(bucket, []).append(row)

        output: dict[str, dict[str, Any]] = {}
        for bucket, rows in grouped.items():
            pnl_values = [self._number(row.get("net_pnl"), default=0.0) for row in rows]
            wins = [value for value in pnl_values if value > 0]
            losses = [value for value in pnl_values if value < 0]
            transaction_cost = sum(
                self._number(row.get("transaction_cost"), row.get("fees_paid"), default=0.0)
                for row in rows
            )
            realized_pct = [
                self._number(row.get("realized_pnl_pct"), default=0.0) for row in rows
            ]
            cumulative = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for value in pnl_values:
                cumulative += value
                peak = max(peak, cumulative)
                if initial_capital > 0:
                    max_drawdown = max(max_drawdown, ((peak - cumulative) / initial_capital) * 100.0)
            total_net_pnl = sum(pnl_values)
            output[bucket] = {
                "total_net_pnl": round(total_net_pnl, 6),
                "total_return_pct": round(
                    (total_net_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0,
                    6,
                ),
                "closed_trade_count": len(rows),
                "win_rate": round((len(wins) / len(rows)) if rows else 0.0, 6),
                "profit_factor": round((sum(wins) / abs(sum(losses))) if losses else (1.0 if wins else 0.0), 6),
                "expectancy_r": round(
                    (sum(realized_pct) / len(realized_pct) / stop_loss_pct)
                    if realized_pct and stop_loss_pct > 0
                    else 0.0,
                    6,
                ),
                "max_drawdown_pct": round(max_drawdown, 6),
                "transaction_cost": round(transaction_cost, 6),
                "capacity_limited_count": sum(
                    1 for row in rows if self._truthy(row.get("capacity_limited"))
                ),
                "partial_fill_count": sum(
                    1
                    for row in rows
                    if "PARTIAL" in str(row.get("fill_status") or "").upper()
                    or "partial_fill" in str(row.get("entry_reasons") or "").lower()
                    or "partial_fill" in str(row.get("exit_reason") or "").lower()
                ),
            }
        return output

    def _bucket_comparison(
        self,
        *,
        calibration_buckets: dict[str, dict[str, Any]],
        out_of_sample_buckets: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "calibration": self._pairwise_comparison(calibration_buckets),
            "out_of_sample": self._pairwise_comparison(out_of_sample_buckets),
        }

    def _pairwise_comparison(self, buckets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for left, right in COMPARISON_PAIRS:
            left_metrics = buckets.get(left, self._empty_metrics())
            right_metrics = buckets.get(right, self._empty_metrics())
            delta = {
                key: self._metric_delta(left_metrics, right_metrics, key)
                for key in self._empty_metrics()
            }
            rows.append(
                {
                    "left_bucket": left,
                    "right_bucket": right,
                    "left": left_metrics,
                    "right": right_metrics,
                    "delta": delta,
                }
            )
        return rows

    def _coverage_summary(self, artifacts: _RunArtifacts) -> dict[str, Any]:
        coverage_warnings = self._summary_list(artifacts.summary.get("coverage_warnings"))
        skipped_dates = self._summary_list(artifacts.summary.get("skipped_dates"))
        failed_dates = self._summary_list(artifacts.summary.get("failed_dates"))
        data_quality_notes = self._summary_list(artifacts.summary.get("data_quality_notes"))
        missing_l1_count = sum(
            1
            for item in coverage_warnings
            if "bid_ask" in json.dumps(item).lower() or "l1" in json.dumps(item).lower()
        )
        return {
            "coverage_warning_count": len(coverage_warnings),
            "coverage_warnings": coverage_warnings,
            "skipped_date_count": len(skipped_dates),
            "skipped_dates": skipped_dates,
            "failed_date_count": len(failed_dates),
            "failed_dates": failed_dates,
            "data_quality_notes": data_quality_notes,
            "l1_bid_ask_warning_count": missing_l1_count,
            "blocks_edge_judgment": bool(coverage_warnings or skipped_dates or failed_dates),
        }

    def _leakage_guard_summary(self, artifacts: _RunArtifacts) -> dict[str, Any]:
        manifest_notes = artifacts.manifest.get("leakage_guard")
        summary_notes = artifacts.summary.get("leakage_guard_notes")
        notes = [str(item) for item in self._summary_list(manifest_notes)]
        notes.extend(str(item) for item in self._summary_list(summary_notes))
        warnings: list[str] = []
        lower_notes = "\n".join(notes).lower()
        suspicious_patterns = {
            "current/latest universe fallback": ("current universe", "latest universe", "latest watchlist"),
            "cutoff 이후 feature 사용 가능성": ("after cutoff", "post-cutoff feature", "cutoff 이후"),
            "D+1 data may be used in D decision": ("d+1", "next day", "tomorrow"),
        }
        for message, patterns in suspicious_patterns.items():
            if any(pattern in lower_notes for pattern in patterns):
                warnings.append(message)
        options = artifacts.manifest.get("options")
        options = options if isinstance(options, dict) else {}
        if artifacts.manifest.get("score_mode") is None and not options.get("score_mode"):
            warnings.append("score_mode is missing from run_manifest.json")
        score_mode = str(
            artifacts.manifest.get("score_mode")
            or options.get("score_mode")
            or ""
        ).lower()
        model_path = artifacts.manifest.get("model_path") or options.get("model_path")
        if score_mode in {"ml", "blend"} and not model_path:
            warnings.append("model_path is missing from run_manifest.json for ML/blend scoring")
        if artifacts.manifest.get("local_only") is not True:
            warnings.append("local_only=false or missing; API-capable replay cannot support edge judgment")
        return {
            "note_count": len(notes),
            "notes": notes,
            "warnings": sorted(set(warnings)),
            "blocks_edge_judgment": any(
                "local_only=false" in warning
                or "current/latest" in warning
                or "cutoff" in warning
                or "D+1" in warning
                for warning in warnings
            ),
        }

    def _parameter_guard_summary(
        self,
        *,
        calibration: _RunArtifacts,
        out_of_sample: _RunArtifacts,
        calibration_period: dict[str, Any],
    ) -> dict[str, Any]:
        warnings: list[str] = []
        blocking = False
        calendar_days = int(calibration_period["calendar_day_count"])
        if calendar_days and not 21 <= calendar_days <= 45:
            warnings.append("calibration period is not approximately one month")
        for key in ("model_path", "score_mode", "ml_weight"):
            calibration_value = self._first_present(key, calibration)
            out_of_sample_value = self._first_present(key, out_of_sample)
            if calibration_value != out_of_sample_value:
                warnings.append(f"{key} differs between calibration and out-of-sample")
                blocking = True
        return {
            "warnings": warnings,
            "blocks_edge_judgment": blocking,
        }

    def _cost_and_fill_summary(self, artifacts: _RunArtifacts) -> dict[str, Any]:
        buckets = self._bucket_metrics(artifacts)
        transaction_cost = sum(row["transaction_cost"] for row in buckets.values())
        partial_fill_count = sum(row["partial_fill_count"] for row in buckets.values())
        capacity_limited_count = sum(row["capacity_limited_count"] for row in buckets.values())
        coverage = self._coverage_summary(artifacts)
        fill_model = artifacts.manifest.get("fill_model")
        slippage_model = artifacts.manifest.get("slippage_model") or fill_model
        closed_count = sum(row["closed_trade_count"] for row in buckets.values())
        fallback_warnings = [
            warning
            for warning in coverage["coverage_warnings"]
            if "fallback" in json.dumps(warning).lower()
            or "last_price" in json.dumps(warning).lower()
        ]
        return {
            "transaction_cost_deducted": transaction_cost > 0
            or bool(isinstance(fill_model, dict) and fill_model.get("min_trade_fee")),
            "transaction_cost": round(transaction_cost, 6),
            "slippage_model_used": isinstance(slippage_model, dict),
            "slippage_model": slippage_model if isinstance(slippage_model, dict) else None,
            "l1_bid_ask_coverage_present": coverage["l1_bid_ask_warning_count"] == 0,
            "partial_fill_count": partial_fill_count,
            "capacity_limited_count": capacity_limited_count,
            "last_price_fallback_warning_count": len(fallback_warnings),
            "last_price_fallback_warning_ratio": (
                round(len(fallback_warnings) / closed_count, 6) if closed_count else 0.0
            ),
        }

    def _decision_gate(
        self,
        *,
        calibration_period: dict[str, Any],
        out_of_sample_period: dict[str, Any],
        calibration_buckets: dict[str, dict[str, Any]],
        out_of_sample_buckets: dict[str, dict[str, Any]],
        data_coverage: dict[str, dict[str, Any]],
        leakage_guard: dict[str, dict[str, Any]],
        parameter_guard: dict[str, Any],
        missing_artifacts: list[str],
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if missing_artifacts:
            reasons.append("required replay artifacts are missing: " + ", ".join(sorted(set(missing_artifacts))))
            return {"status": "failed", "reasons": reasons}
        if not out_of_sample_period["is_at_least_three_calendar_months"]:
            reasons.append("out-of-sample period is shorter than three calendar months")
            return {"status": "insufficient_data", "reasons": reasons}
        if data_coverage["out_of_sample"]["blocks_edge_judgment"]:
            reasons.append("out-of-sample replay has coverage warnings, skipped dates, or failed dates")
            return {"status": "failed_data_quality", "reasons": reasons}
        if leakage_guard["out_of_sample"]["blocks_edge_judgment"]:
            reasons.append("out-of-sample replay has leakage/API guard warnings")
            return {"status": "failed_data_quality", "reasons": reasons}
        if parameter_guard["blocks_edge_judgment"]:
            reasons.append("model_path, score_mode, and ml_weight must stay fixed for OOS")
            return {"status": "failed_data_quality", "reasons": reasons}

        calibration_edge = self._predictor_edge_detected(calibration_buckets)
        out_of_sample_edge = self._predictor_edge_detected(out_of_sample_buckets)
        oos_predictor = out_of_sample_buckets["predictor_weighted"]
        if calibration_edge and not out_of_sample_edge:
            reasons.append("calibration showed predictor edge but fixed-parameter OOS did not")
            return {"status": "overfit_suspected", "reasons": reasons}
        if not out_of_sample_edge:
            reasons.append("predictor_weighted is not clearly better than comparison buckets in OOS")
            return {"status": "no_edge_detected", "reasons": reasons}
        if oos_predictor["closed_trade_count"] <= 0:
            reasons.append("out-of-sample predictor bucket has no closed trades")
            return {"status": "insufficient_data", "reasons": reasons}
        reasons.append(
            "OOS predictor bucket is ahead of comparison buckets; maximum allowed status is shadow validation"
        )
        return {"status": "promising_needs_shadow", "reasons": reasons}

    def _predictor_edge_detected(self, buckets: dict[str, dict[str, Any]]) -> bool:
        predictor = buckets["predictor_weighted"]
        comparisons = [
            buckets["watchlist_momentum"],
            buckets["watchlist_blind_momentum"],
        ]
        if predictor["closed_trade_count"] <= 0:
            return False
        if predictor["total_net_pnl"] <= 0 or predictor["total_return_pct"] <= 0:
            return False
        return all(
            predictor["total_net_pnl"] > row["total_net_pnl"]
            and predictor["total_return_pct"] > row["total_return_pct"]
            for row in comparisons
        )

    def _run_summary(
        self,
        artifacts: _RunArtifacts,
        buckets: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "run_dir": str(artifacts.run_dir),
            "source_conclusion_status": artifacts.summary.get("conclusion_status"),
            "bucket_results": buckets,
            "missing_artifacts": artifacts.missing_artifacts,
        }

    def _next_actions(self, status: str) -> list[str]:
        if status == "promising_needs_shadow":
            return [
                "Do not mark live-ready from historical replay alone.",
                "Run shadow/live paper validation with the fixed model and parameters.",
            ]
        if status == "overfit_suspected":
            return [
                "Do not reuse this OOS result after changing parameters.",
                "Select a new untouched OOS window before judging revised parameters.",
            ]
        if status in {"insufficient_data", "failed_data_quality"}:
            return [
                "Extend OOS replay to at least three months with adequate L1/minute coverage.",
                "Fix coverage/leakage warnings before interpreting edge.",
            ]
        if status == "no_edge_detected":
            return [
                "Keep this result as no-edge evidence for the tested fixed configuration.",
                "Use a fresh OOS window if calibration parameters are changed.",
            ]
        return ["Inspect missing artifacts and rerun the local-only historical replay."]

    def _artifact_warnings(self, scope: str, artifacts: _RunArtifacts) -> list[str]:
        return [
            f"{scope}: missing or unreadable artifact {name}"
            for name in sorted(set(artifacts.missing_artifacts))
        ]

    def _coverage_warnings_for_report(self, scope: str, coverage: dict[str, Any]) -> list[str]:
        if not coverage["blocks_edge_judgment"]:
            return []
        return [f"{scope}: coverage/skipped/failed dates block edge judgment"]

    def _first_present(self, key: str, *runs: _RunArtifacts) -> Any:
        for run in runs:
            value = run.summary.get(key) or run.manifest.get(key)
            if value is not None:
                return value
            options = run.manifest.get("options")
            if isinstance(options, dict) and options.get(key) is not None:
                return options[key]
        return None

    def _display_bucket(self, bucket: str) -> str:
        return DISPLAY_BUCKETS.get(bucket, bucket)

    def _empty_metrics(self) -> dict[str, Any]:
        return {
            "total_net_pnl": 0.0,
            "total_return_pct": 0.0,
            "closed_trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "max_drawdown_pct": 0.0,
            "average_slippage_pct": None,
            "transaction_cost": 0.0,
            "capacity_limited_count": 0,
            "partial_fill_count": 0,
        }

    def _metric_delta(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        key: str,
    ) -> float | int | None:
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is None or right_value is None:
            return None
        if isinstance(left_value, int) and isinstance(right_value, int):
            return left_value - right_value
        return round(float(left_value) - float(right_value), 6)

    def _summary_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _is_at_least_three_months(self, start: date, end: date) -> bool:
        threshold = self._add_months(start, 3) - timedelta(days=1)
        return end >= threshold

    def _add_months(self, value: date, months: int) -> date:
        month = value.month - 1 + months
        year = value.year + month // 12
        month = month % 12 + 1
        days_in_month = [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1]
        return date(year, month, min(value.day, days_in_month))

    def _rate(self, *values: Any) -> float:
        value = self._number(*values, default=0.0)
        return value / 100.0 if value > 1.0 else value

    def _optional_number(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return round(self._number(value, default=0.0), 6)

    def _number(self, *values: Any, default: float = 0.0) -> float:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return default

    def _truthy(self, value: Any) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _non_comment_lines(handle):
    for line in handle:
        if line.lstrip().startswith("#"):
            continue
        yield line
