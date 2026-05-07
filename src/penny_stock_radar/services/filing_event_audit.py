from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

DEFAULT_OUTPUT_ROOT = Path("data/backtest_lab/filing_event_audits/mito_ohlcv_1m")

DEFAULT_DIMENSIONS = ("filing_group", "catalyst_tier")


class FilingEventAuditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FilingEventAuditOptions:
    candidate_event_root: Path
    classified_filings_csv: Path
    random_events_csv: Path | None = None
    output_root: Path = DEFAULT_OUTPUT_ROOT
    run_id: str | None = None
    max_filing_age_days: int = 5
    primary_random_mode: str = "same_time_bucket"
    primary_window_minutes: int = 120
    min_sample_count: int = 100
    min_mean_edge_pct: float = 0.10
    min_win_rate_edge_pct: float = 2.0
    min_ex_trap_improvement_pct: float = 0.10
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS


@dataclass(frozen=True, slots=True)
class FilingEventAuditResult:
    export_dir: Path
    summary_json_path: Path
    summary_md_path: Path
    csv_paths: dict[str, Path]
    report: dict[str, Any]


class FilingEventAuditor:
    def audit(self, options: FilingEventAuditOptions) -> FilingEventAuditResult:
        candidate_events, candidate_files = _load_candidate_events(options.candidate_event_root)
        if not candidate_events:
            raise FilingEventAuditError(
                f"No candidate event rows found at {options.candidate_event_root}"
            )
        classified_filings = _load_classified_filings(options.classified_filings_csv)
        if not classified_filings:
            raise FilingEventAuditError(
                f"No classified filing rows found at {options.classified_filings_csv}"
            )
        random_events = (
            _load_random_events(options.random_events_csv) if options.random_events_csv else []
        )

        filing_index = _build_filing_index(classified_filings)
        enriched_events = [
            _attach_filing_context(event, filing_index, max_age_days=options.max_filing_age_days)
            for event in candidate_events
        ]
        forward_windows = _forward_windows_from_rows(enriched_events, random_events)
        if not forward_windows:
            raise FilingEventAuditError("No forward_*m_return_pct columns found.")

        random_modes = tuple(
            sorted({row["benchmark_mode"] for row in random_events if row.get("benchmark_mode")})
        )
        random_by_key = {
            (int(row["candidate_row_id"]), row["benchmark_mode"]): row
            for row in random_events
            if _is_int(row.get("candidate_row_id")) and row.get("benchmark_mode")
        }
        tier_rows = _tier_summary_rows(
            enriched_events,
            random_modes=random_modes,
            random_by_key=random_by_key,
            forward_windows=forward_windows,
            dimensions=options.dimensions,
        )
        avoid_long_rows = _avoid_long_rows(enriched_events, forward_windows=forward_windows)
        primary = _primary_diagnostics(
            tier_rows,
            avoid_long_rows,
            options=options,
            random_controls_present=bool(random_events),
        )
        gate = _audit_gate(primary, options=options, random_controls_present=bool(random_events))

        run_id = options.run_id or datetime.now(timezone.utc).strftime(
            "filing_event_audit_%Y%m%d_%H%M%S"
        )
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        csv_paths = {
            "candidate_events_with_filing_tiers": export_dir
            / "candidate_events_with_filing_tiers.csv",
            "filing_tier_event_summary": export_dir / "filing_tier_event_summary.csv",
            "avoid_long_summary": export_dir / "avoid_long_summary.csv",
        }
        _write_csv(csv_paths["candidate_events_with_filing_tiers"], enriched_events)
        _write_csv(csv_paths["filing_tier_event_summary"], tier_rows)
        _write_csv(csv_paths["avoid_long_summary"], avoid_long_rows)

        report = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "scope": "filing_tier_event_join_diagnostic_only",
                "decision_grade": False,
                "cost_grade": "none",
                "pass_meaning": (
                    "A positive diagnostic only means filing tiers explain gross OHLCV event "
                    "outcomes better than the paired random controls. It does not approve "
                    "a strategy, setup, or live trading."
                ),
            },
            "input": {
                "candidate_event_root": str(options.candidate_event_root),
                "candidate_event_files": [str(path) for path in candidate_files],
                "candidate_event_count": len(enriched_events),
                "classified_filings_csv": str(options.classified_filings_csv),
                "classified_filing_count": len(classified_filings),
                "random_events_csv": str(options.random_events_csv) if options.random_events_csv else None,
                "random_event_count": len(random_events),
            },
            "thresholds": {
                "max_filing_age_days": options.max_filing_age_days,
                "primary_random_mode": options.primary_random_mode,
                "primary_window_minutes": options.primary_window_minutes,
                "min_sample_count": options.min_sample_count,
                "min_mean_edge_pct": options.min_mean_edge_pct,
                "min_win_rate_edge_pct": options.min_win_rate_edge_pct,
                "min_ex_trap_improvement_pct": options.min_ex_trap_improvement_pct,
            },
            "filing_group_counts": _count_rows(enriched_events, "filing_group"),
            "catalyst_tier_counts": _count_rows(enriched_events, "catalyst_tier"),
            "matched_filing_count": sum(
                1 for row in enriched_events if row.get("filing_group") != "no_recent_filing"
            ),
            "forward_windows_minutes": list(forward_windows),
            "primary_diagnostics": primary,
            "filing_event_gate": gate,
            "csv_outputs": {key: str(path) for key, path in csv_paths.items()},
        }
        summary_json_path = export_dir / "filing_event_audit_summary.json"
        summary_md_path = export_dir / "filing_event_audit_summary.md"
        summary_json_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_markdown_summary(report), encoding="utf-8")
        return FilingEventAuditResult(
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
            for row in csv.DictReader(handle):
                ticker = (row.get("ticker") or row.get("symbol") or "").strip().upper()
                market_date = (row.get("market_date") or "").strip()
                if not ticker or not market_date:
                    continue
                cleaned = {key: (value or "").strip() for key, value in row.items()}
                cleaned["ticker"] = ticker
                cleaned["symbol"] = ticker
                cleaned["market_date"] = market_date
                cleaned["market_month"] = cleaned.get("market_month") or market_date[:7]
                cleaned["event_time_et"] = cleaned.get("event_time_et") or "UNKNOWN"
                cleaned["time_bucket"] = cleaned.get("time_bucket") or "UNKNOWN"
                cleaned["source_run_id"] = path.parent.name
                cleaned["candidate_row_id"] = str(len(events))
                events.append(cleaned)
    return events, files


def _load_classified_filings(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
            if (row.get("symbol") or "").strip()
        ]


def _load_random_events(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
            if (row.get("candidate_row_id") or "").strip()
            and (row.get("benchmark_mode") or "").strip()
        ]


def _build_filing_index(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = (row.get("symbol") or "").upper()
        filing_date = _parse_date(row.get("filing_date") or row.get("eligible_for_market_date"))
        if not symbol or filing_date is None:
            continue
        enriched = dict(row)
        enriched["_filing_date_obj"] = filing_date
        indexed.setdefault(symbol, []).append(enriched)
    for symbol_rows in indexed.values():
        symbol_rows.sort(key=lambda item: item["_filing_date_obj"])
    return indexed


def _attach_filing_context(
    event: dict[str, str],
    filing_index: dict[str, list[dict[str, Any]]],
    *,
    max_age_days: int,
) -> dict[str, str]:
    symbol = (event.get("ticker") or event.get("symbol") or "").upper()
    event_date = _parse_date(event.get("market_date"))
    match = None
    age_days = None
    if event_date is not None:
        for filing in filing_index.get(symbol, []):
            filing_date = filing["_filing_date_obj"]
            delta = (event_date - filing_date).days
            if delta < 0:
                break
            if delta <= max_age_days:
                match = filing
                age_days = delta
    output = dict(event)
    if match is None:
        output.update(
            {
                "filing_group": "no_recent_filing",
                "catalyst_tier": "none",
                "catalyst_direction": "none",
                "trap_flag": "false",
                "filing_age_days": "",
                "filing_form": "",
                "filing_date": "",
                "filing_accession_number": "",
                "filing_classification_reasons": "",
            }
        )
        return output
    tier = str(match.get("catalyst_tier") or "unknown")
    trap_flag = str(match.get("trap_flag") or "").lower() == "true" or tier == "trap"
    filing_group = "trap" if trap_flag else tier
    output.update(
        {
            "filing_group": filing_group,
            "catalyst_tier": tier,
            "catalyst_direction": str(match.get("catalyst_direction") or ""),
            "trap_flag": str(trap_flag).lower(),
            "filing_age_days": str(age_days if age_days is not None else ""),
            "filing_form": str(match.get("form") or ""),
            "filing_date": str(match.get("filing_date") or ""),
            "filing_accession_number": str(match.get("accession_number") or ""),
            "filing_classification_reasons": str(match.get("classification_reasons") or ""),
        }
    )
    return output


def _forward_windows_from_rows(
    candidate_events: list[dict[str, str]],
    random_events: list[dict[str, str]],
) -> tuple[int, ...]:
    windows: set[int] = set()
    for rows in (candidate_events, random_events):
        for row in rows[:100]:
            for key in row:
                if key.startswith("forward_") and key.endswith("m_return_pct"):
                    raw = key.removeprefix("forward_").removesuffix("m_return_pct")
                    if raw.isdigit():
                        windows.add(int(raw))
    return tuple(sorted(windows))


def _tier_summary_rows(
    events: list[dict[str, str]],
    *,
    random_modes: tuple[str, ...],
    random_by_key: dict[tuple[int, str], dict[str, str]],
    forward_windows: tuple[int, ...],
    dimensions: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    modes = random_modes or ("candidate_only",)
    for dimension in dimensions:
        grouped = _group_by(events, dimension)
        for value, group_events in grouped.items():
            for mode in modes:
                for window in forward_windows:
                    rows.append(
                        {
                            "dimension": dimension,
                            "dimension_value": value,
                            "benchmark_mode": mode,
                            "window_minutes": window,
                            **_comparison_for_events(
                                group_events,
                                random_by_key=random_by_key,
                                benchmark_mode=mode,
                                window=window,
                            ),
                        }
                    )
    return rows


def _avoid_long_rows(
    events: list[dict[str, str]],
    *,
    forward_windows: tuple[int, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cohorts = {
        "all": events,
        "ex_trap": [row for row in events if row.get("filing_group") != "trap"],
        "trap_only": [row for row in events if row.get("filing_group") == "trap"],
        "tier_1_only": [row for row in events if row.get("filing_group") == "tier_1"],
        "no_recent_filing": [
            row for row in events if row.get("filing_group") == "no_recent_filing"
        ],
    }
    all_stats_by_window: dict[int, dict[str, Any]] = {}
    for window in forward_windows:
        all_stats_by_window[window] = _stats(
            [
                value
                for row in cohorts["all"]
                if (value := _to_float(row.get(f"forward_{window}m_return_pct"))) is not None
            ]
        )
    for cohort, cohort_rows in cohorts.items():
        for window in forward_windows:
            stats = _stats(
                [
                    value
                    for row in cohort_rows
                    if (value := _to_float(row.get(f"forward_{window}m_return_pct"))) is not None
                ]
            )
            rows.append(
                {
                    "cohort": cohort,
                    "window_minutes": window,
                    "sample_count": stats["count"],
                    "mean_return_pct": stats["mean"],
                    "median_return_pct": stats["median"],
                    "gt_zero_rate_pct": stats["gt_zero_rate_pct"],
                    "mean_improvement_vs_all_pct": _subtract(
                        stats["mean"], all_stats_by_window[window]["mean"]
                    ),
                }
            )
    return rows


def _comparison_for_events(
    events: list[dict[str, str]],
    *,
    random_by_key: dict[tuple[int, str], dict[str, str]],
    benchmark_mode: str,
    window: int,
) -> dict[str, Any]:
    candidate_values: list[float] = []
    random_values: list[float] = []
    missing_random = 0
    for event in events:
        candidate_value = _to_float(event.get(f"forward_{window}m_return_pct"))
        if candidate_value is None:
            continue
        candidate_values.append(candidate_value)
        if benchmark_mode == "candidate_only":
            continue
        random_row = random_by_key.get((int(event["candidate_row_id"]), benchmark_mode))
        if random_row is None or str(random_row.get("random_control_valid")).lower() != "true":
            missing_random += 1
            continue
        random_value = _to_float(random_row.get(f"forward_{window}m_return_pct"))
        if random_value is not None:
            random_values.append(random_value)
    candidate_stats = _stats(candidate_values)
    random_stats = _stats(random_values)
    return {
        "candidate_sample_count": candidate_stats["count"],
        "paired_random_sample_count": random_stats["count"],
        "candidate_mean_return_pct": candidate_stats["mean"],
        "random_mean_return_pct": random_stats["mean"],
        "mean_edge_pct": _subtract(candidate_stats["mean"], random_stats["mean"]),
        "candidate_median_return_pct": candidate_stats["median"],
        "random_median_return_pct": random_stats["median"],
        "median_edge_pct": _subtract(candidate_stats["median"], random_stats["median"]),
        "candidate_gt_zero_rate_pct": candidate_stats["gt_zero_rate_pct"],
        "random_gt_zero_rate_pct": random_stats["gt_zero_rate_pct"],
        "win_rate_edge_pct": _subtract(
            candidate_stats["gt_zero_rate_pct"], random_stats["gt_zero_rate_pct"]
        ),
        "missing_random_control_count": missing_random,
    }


def _primary_diagnostics(
    tier_rows: list[dict[str, Any]],
    avoid_long_rows: list[dict[str, Any]],
    *,
    options: FilingEventAuditOptions,
    random_controls_present: bool,
) -> dict[str, Any]:
    tier_1_row = _find_primary_tier_row(
        tier_rows,
        dimension="filing_group",
        value="tier_1",
        benchmark_mode=options.primary_random_mode if random_controls_present else "candidate_only",
        window=options.primary_window_minutes,
    )
    trap_row = _find_primary_tier_row(
        tier_rows,
        dimension="filing_group",
        value="trap",
        benchmark_mode=options.primary_random_mode if random_controls_present else "candidate_only",
        window=options.primary_window_minutes,
    )
    ex_trap_row = _find_avoid_long_row(
        avoid_long_rows,
        cohort="ex_trap",
        window=options.primary_window_minutes,
    )
    return {
        "tier_1_primary_row": tier_1_row,
        "trap_primary_row": trap_row,
        "ex_trap_primary_row": ex_trap_row,
    }


def _audit_gate(
    primary: dict[str, Any],
    *,
    options: FilingEventAuditOptions,
    random_controls_present: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    status = "FAIL"
    tier_1 = primary.get("tier_1_primary_row") or {}
    ex_trap = primary.get("ex_trap_primary_row") or {}

    if not random_controls_present:
        reasons.append("random_controls_missing_candidate_only_inventory")
    tier_1_count = int(tier_1.get("paired_random_sample_count") or tier_1.get("candidate_sample_count") or 0)
    tier_1_mean_edge = tier_1.get("mean_edge_pct")
    tier_1_win_edge = tier_1.get("win_rate_edge_pct")
    tier_1_ok = (
        random_controls_present
        and tier_1_count >= options.min_sample_count
        and _number_or_floor(tier_1_mean_edge) >= options.min_mean_edge_pct
        and _number_or_floor(tier_1_win_edge) >= options.min_win_rate_edge_pct
    )
    if tier_1_ok:
        reasons.append("tier_1_candidate_beats_random_thresholds")
    else:
        reasons.append("tier_1_candidate_not_validated_vs_random")

    ex_trap_count = int(ex_trap.get("sample_count") or 0)
    ex_trap_improvement = ex_trap.get("mean_improvement_vs_all_pct")
    ex_trap_ok = (
        ex_trap_count >= options.min_sample_count
        and _number_or_floor(ex_trap_improvement) >= options.min_ex_trap_improvement_pct
    )
    if ex_trap_ok:
        reasons.append("ex_trap_mean_improves_candidate_universe")
    else:
        reasons.append("trap_avoid_long_not_validated")

    if tier_1_ok or ex_trap_ok:
        status = "PROMISING_DIAGNOSTIC"
    return {"status": status, "reasons": reasons}


def _find_primary_tier_row(
    rows: list[dict[str, Any]],
    *,
    dimension: str,
    value: str,
    benchmark_mode: str,
    window: int,
) -> dict[str, Any] | None:
    for row in rows:
        if (
            row.get("dimension") == dimension
            and row.get("dimension_value") == value
            and row.get("benchmark_mode") == benchmark_mode
            and int(row.get("window_minutes") or 0) == window
        ):
            return row
    return None


def _find_avoid_long_row(
    rows: list[dict[str, Any]],
    *,
    cohort: str,
    window: int,
) -> dict[str, Any] | None:
    for row in rows:
        if row.get("cohort") == cohort and int(row.get("window_minutes") or 0) == window:
            return row
    return None


def _group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        value = str(row.get(key) or "UNKNOWN")
        grouped.setdefault(value, []).append(row)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "gt_zero_rate_pct": None}
    return {
        "count": len(values),
        "mean": _round(sum(values) / len(values)),
        "median": _round(float(median(values))),
        "gt_zero_rate_pct": _round(100.0 * sum(1 for value in values if value > 0.0) / len(values)),
    }


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _subtract(left: object, right: object) -> float | None:
    left_float = _to_float(left)
    right_float = _to_float(right)
    if left_float is None or right_float is None:
        return None
    return _round(left_float - right_float)


def _number_or_floor(value: object) -> float:
    number = _to_float(value)
    return number if number is not None else -1_000_000.0


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _is_int(value: object) -> bool:
    try:
        int(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _count_rows(rows: list[dict[str, str]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "UNKNOWN") for row in rows).items()))


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
        for row in rows:
            writer.writerow(row)


def _unique_export_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_rerun_{index}")
        if not candidate.exists():
            return candidate
    raise FilingEventAuditError(f"Could not allocate unique output directory under {path.parent}")


def _markdown_summary(report: dict[str, Any]) -> str:
    gate = report.get("filing_event_gate", {})
    primary = report.get("primary_diagnostics", {})
    tier_1 = primary.get("tier_1_primary_row") or {}
    ex_trap = primary.get("ex_trap_primary_row") or {}
    lines = [
        "# Filing Event Audit",
        "",
        f"- Run ID: `{report.get('run_id')}`",
        f"- Candidate events: {report.get('input', {}).get('candidate_event_count', 0)}",
        f"- Classified filings: {report.get('input', {}).get('classified_filing_count', 0)}",
        f"- Random events: {report.get('input', {}).get('random_event_count', 0)}",
        f"- Gate: `{gate.get('status')}`",
        "- Decision grade: `false`",
        "- Cost grade: `none`",
        "",
        "## Primary Diagnostics",
        "",
        (
            "- Tier 1 vs random: "
            f"n={tier_1.get('paired_random_sample_count')}, "
            f"mean_edge={tier_1.get('mean_edge_pct')}, "
            f"win_edge={tier_1.get('win_rate_edge_pct')}"
        ),
        (
            "- Ex-trap candidate universe: "
            f"n={ex_trap.get('sample_count')}, "
            f"mean_improvement_vs_all={ex_trap.get('mean_improvement_vs_all_pct')}"
        ),
        "",
        "## Gate Reasons",
        "",
    ]
    lines.extend([f"- {reason}" for reason in gate.get("reasons", [])])
    lines.append("")
    return "\n".join(lines)
