from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..config import ResolvedDataPath, resolve_hf_stocks_1m_path
from .hf_1m_bars import (
    DATASET_NAME,
    _collect_lazy,
    _collect_schema,
    _event_time_bucket,
    _load_polars,
    _minute_to_hhmm,
    _resolve_column_mapping,
    _with_audit_columns,
)
from .hf_candidate_event_robustness import DEFAULT_HF_CANDIDATE_EVENT_ROOT

DEFAULT_HF_EVENT_RANDOM_BENCHMARK_ROOT = Path(
    "data/backtest_lab/event_random_benchmarks/hf_cryptospartan_alpaca_bars_1m"
)
DEFAULT_RANDOM_MODES = ("same_time_bucket", "any_regular_time")
DEFAULT_FORWARD_WINDOWS = (30, 60, 120)

BUCKET_MINUTE_RANGES = {
    "open_fakeout_risk": (570, 584),
    "real_money_morning": (585, 630),
    "late_morning_dead_zone": (631, 720),
    "afternoon_runner_window": (840, 930),
    "closing_ramp_dump_window": (931, 959),
    "other_event_window": (585, 930),
    "UNKNOWN": (585, 930),
}


class HfEventRandomBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HfEventRandomBenchmarkOptions:
    parquet_path: Path | None = None
    candidate_event_root: Path = DEFAULT_HF_CANDIDATE_EVENT_ROOT
    output_root: Path = DEFAULT_HF_EVENT_RANDOM_BENCHMARK_ROOT
    run_id: str | None = None
    seed: int = 20260507
    random_modes: tuple[str, ...] = DEFAULT_RANDOM_MODES
    forward_windows_minutes: tuple[int, ...] = DEFAULT_FORWARD_WINDOWS
    max_event_staleness_minutes: int = 2
    primary_random_mode: str = "same_time_bucket"
    primary_cohort: str = "ex_top_10"
    primary_window_minutes: int = 120
    min_primary_sample_count: int = 500
    min_mean_edge_pct: float = 0.10
    min_median_edge_pct: float = 0.0
    min_win_rate_edge_pct: float = 2.0
    top_n: int = 25


@dataclass(frozen=True, slots=True)
class HfEventRandomBenchmarkResult:
    export_dir: Path
    summary_json_path: Path
    summary_md_path: Path
    csv_paths: dict[str, Path]
    report: dict[str, Any]


class HfEventRandomBenchmarkRunner:
    def run(self, options: HfEventRandomBenchmarkOptions) -> HfEventRandomBenchmarkResult:
        candidate_events, input_files = _load_candidate_events(options.candidate_event_root)
        if not candidate_events:
            raise HfEventRandomBenchmarkError(
                f"No candidate_events.csv rows found under {options.candidate_event_root}"
            )
        forward_windows = tuple(
            sorted({int(window) for window in options.forward_windows_minutes if int(window) > 0})
        )
        if not forward_windows:
            raise HfEventRandomBenchmarkError("At least one positive forward window is required.")
        random_modes = tuple(dict.fromkeys(options.random_modes or DEFAULT_RANDOM_MODES))
        _validate_random_modes(random_modes)

        resolved = resolve_hf_stocks_1m_path(options.parquet_path)
        parquet_path = resolved.path
        if not parquet_path.exists():
            raise HfEventRandomBenchmarkError(
                "HF 1m bars parquet not found: "
                f"{parquet_path}. Set PSR_HF_STOCKS_1M_PATH to the parquet file, "
                "or set PSR_DATA_ROOT to the external data root containing "
                "raw/huggingface/cryptospartan_stocks_bars_1m/stocks_bars_1m.parquet."
            )

        run_id = options.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        top_tickers = _top_tickers(candidate_events, top_n=max(options.top_n, 10))
        cohorts = {
            "all": list(candidate_events),
            "ex_top_10": _exclude_top_tickers(candidate_events, top_tickers, 10),
        }
        random_plan = _build_random_plan(candidate_events, options=options, random_modes=random_modes)
        random_outcomes = _compute_random_outcomes(
            random_plan,
            parquet_path=parquet_path,
            forward_windows=forward_windows,
            max_event_staleness_minutes=options.max_event_staleness_minutes,
        )
        random_by_key = {
            (int(row["candidate_row_id"]), str(row["benchmark_mode"])): row
            for row in random_outcomes
        }
        comparison_rows = _comparison_rows(
            cohorts=cohorts,
            random_modes=random_modes,
            random_by_key=random_by_key,
            forward_windows=forward_windows,
        )
        gate_status, gate_reasons = _benchmark_gate(comparison_rows, options=options)

        top_ticker_rows = [
            {
                "rank": index + 1,
                "ticker": row["ticker"],
                "event_count": row["event_count"],
                "pct_of_events": row["pct_of_events"],
            }
            for index, row in enumerate(top_tickers[: options.top_n])
        ]
        random_quality_rows = _random_quality_rows(random_outcomes, random_modes=random_modes)
        stat = parquet_path.stat()
        report = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "dataset": DATASET_NAME,
                "scope": "hf_event_same_ticker_day_random_time_benchmark_gross_only",
                "decision_grade": False,
                "cost_grade": "none",
                "cost_grade_reason": (
                    "Benchmark uses 1m OHLCV bars only; no bid/ask, NBBO, L2, fill, "
                    "news, float, halt, or KIS tradability evidence."
                ),
                "benchmark_meaning": (
                    "Conditional same ticker/day random-time null. Passing this benchmark "
                    "does not approve setup backtests or live trading."
                ),
            },
            "input": {
                "candidate_event_root": str(options.candidate_event_root),
                "candidate_file_count": len(input_files),
                "candidate_files": [str(path) for path in input_files],
                "parquet_path": str(parquet_path),
                "parquet_size_bytes": stat.st_size,
                "parquet_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "path_resolution": _path_resolution_payload(resolved),
            },
            "thresholds": {
                "seed": options.seed,
                "random_modes": list(random_modes),
                "forward_windows_minutes": list(forward_windows),
                "max_event_staleness_minutes": options.max_event_staleness_minutes,
                "primary_random_mode": options.primary_random_mode,
                "primary_cohort": options.primary_cohort,
                "primary_window_minutes": options.primary_window_minutes,
                "min_primary_sample_count": options.min_primary_sample_count,
                "min_mean_edge_pct": options.min_mean_edge_pct,
                "min_median_edge_pct": options.min_median_edge_pct,
                "min_win_rate_edge_pct": options.min_win_rate_edge_pct,
            },
            "candidate_event_count": len(candidate_events),
            "random_control_count": len(random_outcomes),
            "top_tickers": top_ticker_rows,
            "random_quality": random_quality_rows,
            "benchmark_gate": {
                "status": gate_status,
                "reasons": gate_reasons,
            },
        }

        csv_paths = {
            "comparison_summary": export_dir / "comparison_summary.csv",
            "random_events": export_dir / "random_events.csv",
            "random_quality": export_dir / "random_quality.csv",
            "top_tickers": export_dir / "top_tickers.csv",
        }
        report["primary_comparisons"] = _primary_comparison_rows(
            comparison_rows,
            primary_cohort=options.primary_cohort,
            primary_random_mode=options.primary_random_mode,
        )
        report["csv_outputs"] = {name: str(path) for name, path in csv_paths.items()}
        _write_csv(csv_paths["comparison_summary"], comparison_rows)
        _write_csv(csv_paths["random_events"], random_outcomes)
        _write_csv(csv_paths["random_quality"], random_quality_rows)
        _write_csv(csv_paths["top_tickers"], top_ticker_rows)

        summary_json_path = export_dir / "hf_event_random_benchmark_summary.json"
        summary_md_path = export_dir / "hf_event_random_benchmark_summary.md"
        summary_json_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(
            _markdown_report(report, comparison_rows),
            encoding="utf-8",
        )
        return HfEventRandomBenchmarkResult(
            export_dir=export_dir,
            summary_json_path=summary_json_path,
            summary_md_path=summary_md_path,
            csv_paths=csv_paths,
            report=report,
        )


def _load_candidate_events(input_root: Path) -> tuple[list[dict[str, str]], list[Path]]:
    if input_root.is_file():
        files = [input_root]
    else:
        files = []
        root_file = input_root / "candidate_events.csv"
        if root_file.exists():
            files.append(root_file)
        files.extend(sorted(input_root.glob("*/candidate_events.csv")))
    events: list[dict[str, str]] = []
    for path in files:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ticker = (row.get("ticker") or "").strip().upper()
                market_date = (row.get("market_date") or "").strip()
                if not ticker or not market_date:
                    continue
                cleaned = {key: (value or "").strip() for key, value in row.items()}
                cleaned["ticker"] = ticker
                cleaned["market_date"] = market_date
                cleaned["source_run_id"] = path.parent.name
                cleaned["candidate_row_id"] = str(len(events))
                cleaned["market_month"] = cleaned.get("market_month") or market_date[:7]
                cleaned["event_time_et"] = cleaned.get("event_time_et") or "UNKNOWN"
                cleaned["time_bucket"] = cleaned.get("time_bucket") or "UNKNOWN"
                events.append(cleaned)
    return events, files


def _build_random_plan(
    candidate_events: list[dict[str, str]],
    *,
    options: HfEventRandomBenchmarkOptions,
    random_modes: tuple[str, ...],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for event in candidate_events:
        market_date = _parse_date(event["market_date"])
        for mode in random_modes:
            minute = _random_minute_for_event(event, mode=mode, seed=options.seed)
            plan.append(
                {
                    "sample_key": len(plan),
                    "candidate_row_id": int(event["candidate_row_id"]),
                    "benchmark_mode": mode,
                    "ticker": event["ticker"],
                    "market_date": market_date,
                    "random_event_minute": minute,
                    "random_event_time_et": _minute_to_hhmm(minute),
                    "random_time_bucket": _event_time_bucket(minute),
                }
            )
    return plan


def _compute_random_outcomes(
    random_plan: list[dict[str, Any]],
    *,
    parquet_path: Path,
    forward_windows: tuple[int, ...],
    max_event_staleness_minutes: int,
) -> list[dict[str, Any]]:
    if not random_plan:
        return []
    pl = _load_polars()
    plan_df = pl.DataFrame(random_plan)
    lf = pl.scan_parquet(str(parquet_path))
    schema = _collect_schema(lf)
    mapping = _resolve_column_mapping(list(schema.keys()))
    auditable = _with_audit_columns(lf, schema, mapping, pl)
    tickers = sorted({row["ticker"] for row in random_plan})
    dates = [row["market_date"] for row in random_plan]
    filtered = auditable.filter(
        pl.col("__psr_ticker").is_in(tickers)
        & (pl.col("__psr_et_date") >= min(dates))
        & (pl.col("__psr_et_date") <= max(dates))
        & pl.col("__psr_et_minute").is_between(570, 959, closed="both")
    )
    joined = plan_df.lazy().join(
        filtered,
        left_on=["ticker", "market_date"],
        right_on=["__psr_ticker", "__psr_et_date"],
        how="left",
    )
    minute = pl.col("__psr_et_minute")
    regular = minute.is_between(570, 959, closed="both")
    to_event = regular & (minute <= pl.col("random_event_minute"))
    aggregations = [
        pl.col("__psr_et_minute").filter(to_event).max().alias("latest_bar_minute_to_event"),
        pl.col("__psr_close")
        .filter(to_event)
        .sort_by(pl.col("__psr_ts").filter(to_event))
        .last()
        .alias("random_event_price"),
    ]
    for window in forward_windows:
        forward = regular & (
            minute > pl.col("random_event_minute")
        ) & (minute <= pl.col("random_event_minute") + window)
        aggregations.extend(
            [
                forward.cast(pl.Int64).sum().alias(f"forward_{window}m_row_count"),
                pl.col("__psr_close")
                .filter(forward)
                .sort_by(pl.col("__psr_ts").filter(forward))
                .last()
                .alias(f"forward_{window}m_close_price"),
                pl.col("__psr_high").filter(forward).max().alias(f"forward_{window}m_high_price"),
                pl.col("__psr_low").filter(forward).min().alias(f"forward_{window}m_low_price"),
            ]
        )
    grouped = joined.group_by(
        [
            "sample_key",
            "candidate_row_id",
            "benchmark_mode",
            "ticker",
            "market_date",
            "random_event_minute",
            "random_event_time_et",
            "random_time_bucket",
        ]
    ).agg(aggregations)
    outcome_exprs = [
        (
            pl.col("random_event_minute") - pl.col("latest_bar_minute_to_event")
        ).alias("random_event_staleness_minutes"),
    ]
    for window in forward_windows:
        outcome_exprs.extend(
            [
                (
                    ((pl.col(f"forward_{window}m_close_price") / pl.col("random_event_price")) - 1.0)
                    * 100.0
                ).alias(f"forward_{window}m_return_pct"),
                (
                    ((pl.col(f"forward_{window}m_high_price") / pl.col("random_event_price")) - 1.0)
                    * 100.0
                ).alias(f"forward_{window}m_max_up_pct"),
                (
                    ((pl.col(f"forward_{window}m_low_price") / pl.col("random_event_price")) - 1.0)
                    * 100.0
                ).alias(f"forward_{window}m_max_down_pct"),
            ]
        )
    result = grouped.with_columns(outcome_exprs).with_columns(
        [
            (
                pl.col("random_event_price").is_not_null()
                & (
                    pl.col("random_event_staleness_minutes")
                    <= max_event_staleness_minutes
                )
            )
            .fill_null(False)
            .alias("random_control_valid"),
            pl.lit(False).alias("decision_grade"),
            pl.lit("none").alias("cost_grade"),
        ]
    )
    return _collect_lazy(
        result.sort(["benchmark_mode", "candidate_row_id", "sample_key"])
    ).to_dicts()


def _comparison_rows(
    *,
    cohorts: dict[str, list[dict[str, str]]],
    random_modes: tuple[str, ...],
    random_by_key: dict[tuple[int, str], dict[str, Any]],
    forward_windows: tuple[int, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = ("return_pct", "max_up_pct", "max_down_pct")
    for cohort_name, cohort_events in cohorts.items():
        for mode in random_modes:
            for window in forward_windows:
                for metric in metrics:
                    candidate_values: list[float] = []
                    random_values: list[float] = []
                    missing_random = 0
                    for event in cohort_events:
                        random_row = random_by_key.get((int(event["candidate_row_id"]), mode))
                        if random_row is None or not bool(random_row.get("random_control_valid")):
                            missing_random += 1
                            continue
                        candidate_value = _to_float(event.get(f"forward_{window}m_{metric}"))
                        random_value = _to_float(random_row.get(f"forward_{window}m_{metric}"))
                        if candidate_value is None or random_value is None:
                            continue
                        candidate_values.append(candidate_value)
                        random_values.append(random_value)
                    candidate_stats = _stats(candidate_values)
                    random_stats = _stats(random_values)
                    rows.append(
                        {
                            "cohort": cohort_name,
                            "benchmark_mode": mode,
                            "window_minutes": window,
                            "metric": metric,
                            "paired_sample_count": len(candidate_values),
                            "candidate_mean": candidate_stats["mean"],
                            "random_mean": random_stats["mean"],
                            "mean_edge_pct": _round_none(
                                _subtract(candidate_stats["mean"], random_stats["mean"])
                            ),
                            "candidate_median": candidate_stats["median"],
                            "random_median": random_stats["median"],
                            "median_edge_pct": _round_none(
                                _subtract(candidate_stats["median"], random_stats["median"])
                            ),
                            "candidate_gt_zero_rate_pct": candidate_stats["gt_zero_rate_pct"],
                            "random_gt_zero_rate_pct": random_stats["gt_zero_rate_pct"],
                            "win_rate_edge_pct": _round_none(
                                _subtract(
                                    candidate_stats["gt_zero_rate_pct"],
                                    random_stats["gt_zero_rate_pct"],
                                )
                            ),
                            "candidate_p10": candidate_stats["p10"],
                            "random_p10": random_stats["p10"],
                            "candidate_p90": candidate_stats["p90"],
                            "random_p90": random_stats["p90"],
                            "missing_random_control_count": missing_random,
                        }
                    )
    return rows


def _benchmark_gate(
    comparison_rows: list[dict[str, Any]],
    *,
    options: HfEventRandomBenchmarkOptions,
) -> tuple[str, list[str]]:
    primary = next(
        (
            row
            for row in comparison_rows
            if row["cohort"] == options.primary_cohort
            and row["benchmark_mode"] == options.primary_random_mode
            and int(row["window_minutes"]) == options.primary_window_minutes
            and row["metric"] == "return_pct"
        ),
        None,
    )
    if primary is None:
        return "BLOCKED", ["primary_random_benchmark_comparison_missing"]
    blockers: list[str] = []
    failures: list[str] = []
    sample_count = int(primary["paired_sample_count"] or 0)
    if sample_count < options.min_primary_sample_count:
        blockers.append(
            f"primary_paired_sample_count_lt_{options.min_primary_sample_count}: {sample_count}"
        )
    mean_edge = float(primary["mean_edge_pct"] or 0.0)
    median_edge = float(primary["median_edge_pct"] or 0.0)
    win_rate_edge = float(primary["win_rate_edge_pct"] or 0.0)
    if mean_edge < options.min_mean_edge_pct:
        failures.append(f"mean_edge_pct_lt_{options.min_mean_edge_pct}: {mean_edge}")
    if median_edge < options.min_median_edge_pct:
        failures.append(f"median_edge_pct_lt_{options.min_median_edge_pct}: {median_edge}")
    if win_rate_edge < options.min_win_rate_edge_pct:
        failures.append(f"win_rate_edge_pct_lt_{options.min_win_rate_edge_pct}: {win_rate_edge}")
    if failures:
        return "FAIL", failures + blockers
    if blockers:
        return "BLOCKED", blockers
    return "PASS", ["candidate_event_beats_same_ticker_day_random_time"]


def _primary_comparison_rows(
    comparison_rows: list[dict[str, Any]],
    *,
    primary_cohort: str,
    primary_random_mode: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in comparison_rows
        if row["cohort"] == primary_cohort
        and row["benchmark_mode"] == primary_random_mode
        and row["metric"] == "return_pct"
    ]


def _random_quality_rows(
    random_outcomes: list[dict[str, Any]],
    *,
    random_modes: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows = []
    for mode in random_modes:
        mode_rows = [row for row in random_outcomes if row["benchmark_mode"] == mode]
        valid_count = sum(1 for row in mode_rows if bool(row.get("random_control_valid")))
        rows.append(
            {
                "benchmark_mode": mode,
                "random_control_count": len(mode_rows),
                "valid_random_control_count": valid_count,
                "valid_random_control_pct": _pct(valid_count, len(mode_rows)),
            }
        )
    return rows


def _random_minute_for_event(event: dict[str, str], *, mode: str, seed: int) -> int:
    if mode == "same_time_bucket":
        start, end = BUCKET_MINUTE_RANGES.get(
            event.get("time_bucket") or "UNKNOWN",
            BUCKET_MINUTE_RANGES["UNKNOWN"],
        )
    elif mode == "any_regular_time":
        start, end = 585, 930
    else:
        raise HfEventRandomBenchmarkError(f"Unsupported random benchmark mode: {mode}")
    key = "|".join(
        [
            str(seed),
            mode,
            event["ticker"],
            event["market_date"],
            event.get("source_run_id", ""),
            event.get("event_time_et", ""),
            event["candidate_row_id"],
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    offset = int(digest[:16], 16) % (end - start + 1)
    return start + offset


def _validate_random_modes(random_modes: tuple[str, ...]) -> None:
    allowed = {"same_time_bucket", "any_regular_time"}
    unsupported = [mode for mode in random_modes if mode not in allowed]
    if unsupported:
        raise HfEventRandomBenchmarkError(
            f"Unsupported random benchmark modes: {', '.join(unsupported)}"
        )


def _top_tickers(events: list[dict[str, str]], *, top_n: int) -> list[dict[str, Any]]:
    total = len(events)
    counts = Counter(event["ticker"] for event in events)
    rows = []
    for ticker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top_n]:
        rows.append(
            {
                "ticker": ticker,
                "event_count": count,
                "pct_of_events": _pct(count, total),
            }
        )
    return rows


def _exclude_top_tickers(
    events: list[dict[str, str]],
    top_tickers: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, str]]:
    excluded = {row["ticker"] for row in top_tickers[:top_n]}
    return [event for event in events if event["ticker"] not in excluded]


def _stats(values: list[float]) -> dict[str, float | None]:
    return {
        "mean": _round_none(_mean(values)),
        "median": _round_none(_quantile(values, 0.5)),
        "p10": _round_none(_quantile(values, 0.1)),
        "p90": _round_none(_quantile(values, 0.9)),
        "gt_zero_rate_pct": _pct(sum(1 for value in values if value > 0), len(values)),
    }


def _markdown_report(report: dict[str, Any], comparison_rows: list[dict[str, Any]]) -> str:
    metadata = report.get("metadata", {})
    gate = report.get("benchmark_gate", {})
    thresholds = report.get("thresholds", {})
    primary_rows = [
        row
        for row in comparison_rows
        if row["metric"] == "return_pct"
        and row["cohort"] == thresholds.get("primary_cohort")
        and row["benchmark_mode"] == thresholds.get("primary_random_mode")
    ]
    lines = [
        "# HF Event Random Benchmark",
        "",
        f"- Dataset: `{metadata.get('dataset')}`",
        f"- Scope: `{metadata.get('scope')}`",
        f"- Decision grade: `{metadata.get('decision_grade')}`",
        f"- Cost grade: `{metadata.get('cost_grade')}`",
        f"- Gate: `{gate.get('status')}`",
        f"- Candidate events: {report.get('candidate_event_count', 0)}",
        f"- Random controls: {report.get('random_control_count', 0)}",
        "",
        "## Primary Comparisons",
        "",
    ]
    for row in primary_rows:
        lines.append(
            "- "
            f"{row['window_minutes']}m: sample={row['paired_sample_count']} "
            f"candidate_mean={row['candidate_mean']} random_mean={row['random_mean']} "
            f"mean_edge={row['mean_edge_pct']} "
            f"win_rate_edge={row['win_rate_edge_pct']}"
        )
    lines.extend(["", "## Gate Reasons", ""])
    for reason in gate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- This is a conditional same ticker/day random-time benchmark.",
            "- It is gross OHLCV-only and does not resolve cost/fill/live tradability.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    suffix = 2
    while True:
        candidate = resolved.with_name(f"{resolved.name}_{suffix}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
        suffix += 1


def _path_resolution_payload(resolved: ResolvedDataPath) -> dict[str, str | None]:
    return {
        "source": resolved.source,
        "data_root": str(resolved.data_root) if resolved.data_root is not None else None,
        "path": str(resolved.path),
    }


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _round_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def _pct(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 6)
