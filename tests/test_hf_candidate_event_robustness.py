from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.hf_candidate_event_robustness import (
    HfCandidateEventRobustnessAuditor,
    HfCandidateEventRobustnessOptions,
)


def test_hf_candidate_event_robustness_flags_top_ticker_collapse(tmp_path: Path) -> None:
    input_root = _write_candidate_event_runs(tmp_path / "candidate_events")
    output_root = tmp_path / "robustness"

    result = HfCandidateEventRobustnessAuditor().audit(
        HfCandidateEventRobustnessOptions(
            input_root=input_root,
            output_root=output_root,
            run_id="fixture",
            min_total_events=10,
            min_active_years=2,
            max_top10_ticker_pct=50.0,
            min_remaining_pct_after_top10=40.0,
            min_remaining_events_after_top10=5,
        )
    )

    report = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert report["event_count"] == 12
    assert report["active_year_count"] == 2
    assert report["candidate_event_robustness_gate"]["status"] == "FAIL"
    assert "top10_ticker_pct_gt_50.0: 100.0" in report["candidate_event_robustness_gate"]["reasons"]
    assert "ex_top10_remaining_pct_lt_40.0: 0.0" in report["candidate_event_robustness_gate"]["reasons"]

    cohort_rows = list(csv.DictReader(result.csv_paths["cohort_summary"].open(encoding="utf-8")))
    all_row = next(row for row in cohort_rows if row["cohort"] == "all")
    ex_top1_row = next(row for row in cohort_rows if row["cohort"] == "ex_top_1")
    ex_top10_row = next(row for row in cohort_rows if row["cohort"] == "ex_top_10")
    assert all_row["event_count"] == "12"
    assert all_row["top10_ticker_pct"] == "100.0"
    assert ex_top1_row["event_count"] == "7"
    assert ex_top10_row["event_count"] == "0"

    outcome_rows = list(csv.DictReader(result.csv_paths["forward_outcome_summary"].open(encoding="utf-8")))
    return_row = next(
        row
        for row in outcome_rows
        if row["cohort"] == "all"
        and row["window_minutes"] == "30"
        and row["metric"] == "return_pct"
    )
    assert return_row["sample_count"] == "12"
    assert return_row["gt_zero_rate_pct"] == "75.0"


def test_hf_candidate_event_robustness_cli_writes_outputs(tmp_path: Path) -> None:
    input_root = _write_candidate_event_runs(tmp_path / "candidate_events")
    output_root = tmp_path / "robustness"

    result = CliRunner().invoke(
        app,
        [
            "audit-hf-candidate-event-robustness",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--run-id",
            "cli",
            "--min-total-events",
            "10",
            "--min-active-years",
            "2",
            "--min-remaining-events-after-top10",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = output_root / "cli"
    assert (export_dir / "candidate_event_robustness_summary.json").exists()
    assert (export_dir / "candidate_event_robustness_summary.md").exists()
    assert (export_dir / "cohort_summary.csv").exists()
    assert (export_dir / "forward_outcome_summary.csv").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def _write_candidate_event_runs(root: Path) -> Path:
    _write_run(
        root / "hf_events_2025",
        [
            *_rows_for("AAA", "2025-01", 5, 1.0),
            *_rows_for("BBB", "2025-02", 2, -1.0),
            *_rows_for("CCC", "2025-03", 1, 2.0),
        ],
    )
    _write_run(
        root / "hf_events_2026",
        [
            *_rows_for("DDD", "2026-01", 2, 3.0),
            *_rows_for("EEE", "2026-02", 1, -2.0),
            *_rows_for("FFF", "2026-03", 1, 4.0),
        ],
    )
    return root


def _rows_for(ticker: str, market_month: str, count: int, forward_return: float) -> list[dict[str, object]]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "ticker": ticker,
                "market_date": f"{market_month}-{index + 1:02d}",
                "market_month": market_month,
                "event_time_et": "09:45" if index % 2 == 0 else "14:00",
                "time_bucket": (
                    "real_money_morning"
                    if index % 2 == 0
                    else "afternoon_runner_window"
                ),
                "event_price": 1.0,
                "forward_30m_return_pct": forward_return,
                "forward_30m_max_up_pct": max(forward_return, 0.0) + 1.0,
                "forward_30m_max_down_pct": min(forward_return, 0.0) - 1.0,
                "decision_grade": "false",
                "cost_grade": "none",
            }
        )
    return rows


def _write_run(run_dir: Path, rows: list[dict[str, object]]) -> None:
    run_dir.mkdir(parents=True)
    path = run_dir / "candidate_events.csv"
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
