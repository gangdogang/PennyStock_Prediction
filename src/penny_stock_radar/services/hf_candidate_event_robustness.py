from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HF_CANDIDATE_EVENT_ROOT = Path(
    "data/backtest_lab/candidate_events/hf_cryptospartan_alpaca_bars_1m"
)
DEFAULT_HF_CANDIDATE_EVENT_ROBUSTNESS_ROOT = Path(
    "data/backtest_lab/candidate_event_robustness/hf_cryptospartan_alpaca_bars_1m"
)


class HfCandidateEventRobustnessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HfCandidateEventRobustnessOptions:
    input_root: Path = DEFAULT_HF_CANDIDATE_EVENT_ROOT
    output_root: Path = DEFAULT_HF_CANDIDATE_EVENT_ROBUSTNESS_ROOT
    run_id: str | None = None
    min_total_events: int = 1000
    min_active_years: int = 3
    max_top10_ticker_pct: float = 50.0
    min_remaining_pct_after_top10: float = 40.0
    min_remaining_events_after_top10: int = 500
    top_n: int = 25
    exclusion_levels: tuple[int, ...] = (0, 1, 5, 10)
    dataset_name: str = "cryptospartan_stocks_bars_1m"


@dataclass(frozen=True, slots=True)
class HfCandidateEventRobustnessResult:
    export_dir: Path
    summary_json_path: Path
    summary_md_path: Path
    csv_paths: dict[str, Path]
    report: dict[str, Any]


class HfCandidateEventRobustnessAuditor:
    def audit(
        self,
        options: HfCandidateEventRobustnessOptions,
    ) -> HfCandidateEventRobustnessResult:
        events, input_files = _load_candidate_events(options.input_root)
        if not events:
            raise HfCandidateEventRobustnessError(
                f"No candidate_events.csv rows found under {options.input_root}"
            )

        run_id = options.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        top_tickers = _top_tickers(events, top_n=max(options.top_n, 10))
        exclusion_levels = tuple(sorted({max(int(level), 0) for level in options.exclusion_levels}))
        if 0 not in exclusion_levels:
            exclusion_levels = (0, *exclusion_levels)
        if 10 not in exclusion_levels:
            exclusion_levels = (*exclusion_levels, 10)

        cohorts = [
            _cohort_summary(
                events,
                top_tickers=top_tickers,
                exclude_top_n=level,
            )
            for level in exclusion_levels
        ]
        cohort_by_name = {cohort["cohort"]: cohort for cohort in cohorts}

        top_ticker_rows = [
            {
                "rank": index + 1,
                "ticker": row["ticker"],
                "event_count": row["event_count"],
                "pct_of_events": row["pct_of_events"],
            }
            for index, row in enumerate(top_tickers[: options.top_n])
        ]
        year_rows = _breakdown_rows(events, cohorts, top_tickers, "event_year", "year")
        event_time_rows = _breakdown_rows(
            events,
            cohorts,
            top_tickers,
            "event_time_et",
            "event_time_et",
        )
        time_bucket_rows = _breakdown_rows(
            events,
            cohorts,
            top_tickers,
            "time_bucket",
            "time_bucket",
        )
        forward_rows = _forward_outcome_rows(events, cohorts, top_tickers)

        gate_status, gate_reasons = _robustness_gate(
            total_events=len(events),
            active_years=len(_unique_values(events, "event_year")),
            all_cohort=cohort_by_name["all"],
            ex_top10_cohort=cohort_by_name.get("ex_top_10", cohort_by_name["all"]),
            options=options,
        )
        report = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "dataset": options.dataset_name,
                "scope": "hf_candidate_event_robustness_gross_only",
                "decision_grade": False,
                "cost_grade": "none",
                "cost_grade_reason": (
                    "Candidate event robustness uses 1m OHLCV-derived event rows only; "
                    "there is no bid/ask, NBBO, L2, fill, news, float, halt, or KIS tradability evidence."
                ),
                "pass_meaning": (
                    "PASS only means event distribution survives top-ticker removal well enough "
                    "for further falsification. It does not approve setup backtests or live trading."
                ),
            },
            "input": {
                "input_root": str(options.input_root),
                "input_file_count": len(input_files),
                "input_files": [str(path) for path in input_files],
            },
            "thresholds": {
                "min_total_events": options.min_total_events,
                "min_active_years": options.min_active_years,
                "max_top10_ticker_pct": options.max_top10_ticker_pct,
                "min_remaining_pct_after_top10": options.min_remaining_pct_after_top10,
                "min_remaining_events_after_top10": options.min_remaining_events_after_top10,
                "exclusion_levels": list(exclusion_levels),
            },
            "event_count": len(events),
            "active_year_count": len(_unique_values(events, "event_year")),
            "active_month_count": len(_unique_values(events, "market_month")),
            "top_tickers": top_ticker_rows,
            "cohorts": cohorts,
            "candidate_event_robustness_gate": {
                "status": gate_status,
                "reasons": gate_reasons,
            },
        }

        csv_paths = {
            "cohort_summary": export_dir / "cohort_summary.csv",
            "top_tickers": export_dir / "top_tickers.csv",
            "year_summary": export_dir / "year_summary.csv",
            "event_time_summary": export_dir / "event_time_summary.csv",
            "time_bucket_summary": export_dir / "time_bucket_summary.csv",
            "forward_outcome_summary": export_dir / "forward_outcome_summary.csv",
        }
        _write_csv(csv_paths["cohort_summary"], cohorts)
        _write_csv(csv_paths["top_tickers"], top_ticker_rows)
        _write_csv(csv_paths["year_summary"], year_rows)
        _write_csv(csv_paths["event_time_summary"], event_time_rows)
        _write_csv(csv_paths["time_bucket_summary"], time_bucket_rows)
        _write_csv(csv_paths["forward_outcome_summary"], forward_rows)

        summary_json_path = export_dir / "candidate_event_robustness_summary.json"
        summary_md_path = export_dir / "candidate_event_robustness_summary.md"
        summary_json_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_markdown_report(report), encoding="utf-8")

        return HfCandidateEventRobustnessResult(
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
        files = sorted(input_root.glob("*/candidate_events.csv"))
    events: list[dict[str, str]] = []
    for path in files:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ticker = (row.get("ticker") or "").strip().upper()
                market_date = (row.get("market_date") or "").strip()
                if not ticker or not market_date:
                    continue
                row = {key: (value or "").strip() for key, value in row.items()}
                row["ticker"] = ticker
                row["source_run_id"] = path.parent.name
                row["event_year"] = market_date[:4]
                row["market_month"] = row.get("market_month") or market_date[:7]
                row["event_time_et"] = row.get("event_time_et") or "UNKNOWN"
                row["time_bucket"] = row.get("time_bucket") or "UNKNOWN"
                events.append(row)
    return events, files


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


def _cohort_summary(
    events: list[dict[str, str]],
    *,
    top_tickers: list[dict[str, Any]],
    exclude_top_n: int,
) -> dict[str, Any]:
    cohort_events = _cohort_events(events, top_tickers, exclude_top_n)
    total = len(events)
    cohort_total = len(cohort_events)
    cohort_top_tickers = _top_tickers(cohort_events, top_n=10)
    top1_pct = float(cohort_top_tickers[0]["pct_of_events"]) if cohort_top_tickers else 0.0
    top10_pct = round(sum(float(row["pct_of_events"]) for row in cohort_top_tickers[:10]), 6)
    max_year_pct = _max_group_pct(cohort_events, "event_year")
    max_event_time_pct = _max_group_pct(cohort_events, "event_time_et")
    max_time_bucket_pct = _max_group_pct(cohort_events, "time_bucket")
    return {
        "cohort": "all" if exclude_top_n == 0 else f"ex_top_{exclude_top_n}",
        "exclude_top_n": exclude_top_n,
        "event_count": cohort_total,
        "remaining_pct": _pct(cohort_total, total),
        "ticker_count": len(_unique_values(cohort_events, "ticker")),
        "active_year_count": len(_unique_values(cohort_events, "event_year")),
        "active_month_count": len(_unique_values(cohort_events, "market_month")),
        "top1_ticker_pct": top1_pct,
        "top10_ticker_pct": top10_pct,
        "max_year_pct": max_year_pct,
        "max_event_time_pct": max_event_time_pct,
        "max_time_bucket_pct": max_time_bucket_pct,
    }


def _cohort_events(
    events: list[dict[str, str]],
    top_tickers: list[dict[str, Any]],
    exclude_top_n: int,
) -> list[dict[str, str]]:
    excluded = {row["ticker"] for row in top_tickers[:exclude_top_n]}
    if not excluded:
        return list(events)
    return [event for event in events if event["ticker"] not in excluded]


def _breakdown_rows(
    events: list[dict[str, str]],
    cohorts: list[dict[str, Any]],
    top_tickers: list[dict[str, Any]],
    source_column: str,
    output_column: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        cohort_events = _cohort_events(events, top_tickers, int(cohort["exclude_top_n"]))
        total = len(cohort_events)
        counts = Counter(event.get(source_column, "UNKNOWN") or "UNKNOWN" for event in cohort_events)
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            rows.append(
                {
                    "cohort": cohort["cohort"],
                    output_column: value,
                    "event_count": count,
                    "pct_of_cohort": _pct(count, total),
                }
            )
    return rows


def _forward_outcome_rows(
    events: list[dict[str, str]],
    cohorts: list[dict[str, Any]],
    top_tickers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outcome_columns = _forward_outcome_columns(events)
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        cohort_events = _cohort_events(events, top_tickers, int(cohort["exclude_top_n"]))
        for column in outcome_columns:
            values = [_to_float(event.get(column)) for event in cohort_events]
            clean_values = [value for value in values if value is not None]
            window, metric = _parse_forward_column(column)
            rows.append(
                {
                    "cohort": cohort["cohort"],
                    "window_minutes": window,
                    "metric": metric,
                    "sample_count": len(clean_values),
                    "mean": _round(_mean(clean_values)),
                    "median": _round(_quantile(clean_values, 0.5)),
                    "p10": _round(_quantile(clean_values, 0.1)),
                    "p90": _round(_quantile(clean_values, 0.9)),
                    "gt_zero_rate_pct": _pct(
                        sum(1 for value in clean_values if value > 0),
                        len(clean_values),
                    ),
                }
            )
    return rows


def _forward_outcome_columns(events: list[dict[str, str]]) -> list[str]:
    columns: set[str] = set()
    for event in events:
        for key in event:
            if key.startswith("forward_") and key.endswith(
                ("return_pct", "max_up_pct", "max_down_pct")
            ):
                columns.add(key)
    return sorted(columns, key=_forward_sort_key)


def _parse_forward_column(column: str) -> tuple[int, str]:
    parts = column.split("_", 2)
    window = int(parts[1].removesuffix("m"))
    return window, parts[2]


def _forward_sort_key(column: str) -> tuple[int, str]:
    window, metric = _parse_forward_column(column)
    return window, metric


def _robustness_gate(
    *,
    total_events: int,
    active_years: int,
    all_cohort: dict[str, Any],
    ex_top10_cohort: dict[str, Any],
    options: HfCandidateEventRobustnessOptions,
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    failures: list[str] = []
    if total_events < options.min_total_events:
        blockers.append(f"event_count_lt_{options.min_total_events}: {total_events}")
    if active_years < options.min_active_years:
        blockers.append(f"active_year_count_lt_{options.min_active_years}: {active_years}")
    if float(all_cohort["top10_ticker_pct"]) > options.max_top10_ticker_pct:
        failures.append(
            f"top10_ticker_pct_gt_{options.max_top10_ticker_pct}: "
            f"{all_cohort['top10_ticker_pct']}"
        )
    if float(ex_top10_cohort["remaining_pct"]) < options.min_remaining_pct_after_top10:
        failures.append(
            f"ex_top10_remaining_pct_lt_{options.min_remaining_pct_after_top10}: "
            f"{ex_top10_cohort['remaining_pct']}"
        )
    if int(ex_top10_cohort["event_count"]) < options.min_remaining_events_after_top10:
        blockers.append(
            f"ex_top10_event_count_lt_{options.min_remaining_events_after_top10}: "
            f"{ex_top10_cohort['event_count']}"
        )
    if failures:
        return "FAIL", failures + blockers
    if blockers:
        return "BLOCKED", blockers
    return "PASS", ["candidate_event_distribution_survives_top_ticker_removal"]


def _markdown_report(report: dict[str, Any]) -> str:
    metadata = report.get("metadata", {})
    gate = report.get("candidate_event_robustness_gate", {})
    thresholds = report.get("thresholds", {})
    lines = [
        "# HF Candidate-Event Robustness Audit",
        "",
        f"- Dataset: `{metadata.get('dataset')}`",
        f"- Scope: `{metadata.get('scope')}`",
        f"- Decision grade: `{metadata.get('decision_grade')}`",
        f"- Cost grade: `{metadata.get('cost_grade')}`",
        f"- Gate: `{gate.get('status')}`",
        f"- Events: {report.get('event_count', 0)}",
        f"- Active years: {report.get('active_year_count', 0)}",
        f"- Active months: {report.get('active_month_count', 0)}",
        "",
        "## Thresholds",
        "",
        f"- Max top10 ticker %: {thresholds.get('max_top10_ticker_pct')}",
        f"- Min remaining % after top10 removal: {thresholds.get('min_remaining_pct_after_top10')}",
        f"- Min remaining events after top10 removal: {thresholds.get('min_remaining_events_after_top10')}",
        "",
        "## Cohorts",
        "",
    ]
    for cohort in report.get("cohorts", []):
        lines.append(
            "- "
            f"{cohort.get('cohort')}: events={cohort.get('event_count')} "
            f"remaining_pct={cohort.get('remaining_pct')} "
            f"top10_pct={cohort.get('top10_ticker_pct')}"
        )
    lines.extend(["", "## Gate Reasons", ""])
    for reason in gate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- This is a gross OHLCV robustness audit only.",
            "- Passing this audit does not approve setup backtests, fills, sizing, or live trading.",
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


def _unique_values(events: list[dict[str, str]], column: str) -> set[str]:
    return {event.get(column, "") for event in events if event.get(column, "")}


def _max_group_pct(events: list[dict[str, str]], column: str) -> float:
    total = len(events)
    if total <= 0:
        return 0.0
    counts = Counter(event.get(column, "UNKNOWN") or "UNKNOWN" for event in events)
    return _pct(max(counts.values(), default=0), total)


def _pct(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 6)


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
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


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)
