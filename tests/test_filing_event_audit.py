from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.filing_event_audit import (
    FilingEventAuditOptions,
    FilingEventAuditor,
)


def test_filing_event_audit_joins_candidate_random_and_filing_tiers(tmp_path: Path) -> None:
    candidate_path = _write_candidate_events(tmp_path / "candidate_events.csv")
    random_path = _write_random_events(tmp_path / "random_events.csv")
    filings_path = _write_classified_filings(tmp_path / "classified_filings.csv")

    result = FilingEventAuditor().audit(
        FilingEventAuditOptions(
            candidate_event_root=candidate_path,
            classified_filings_csv=filings_path,
            random_events_csv=random_path,
            output_root=tmp_path / "audit",
            run_id="fixture",
            min_sample_count=1,
            min_mean_edge_pct=1.0,
            min_win_rate_edge_pct=1.0,
            min_ex_trap_improvement_pct=1.0,
        )
    )

    report = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert report["metadata"]["decision_grade"] is False
    assert report["metadata"]["cost_grade"] == "none"
    assert report["input"]["candidate_event_count"] == 4
    assert report["matched_filing_count"] == 3
    assert report["filing_group_counts"]["tier_1"] == 2
    assert report["filing_group_counts"]["trap"] == 1
    assert report["filing_event_gate"]["status"] == "PROMISING_DIAGNOSTIC"
    assert "tier_1_candidate_beats_random_thresholds" in report["filing_event_gate"]["reasons"]
    assert "ex_trap_mean_improves_candidate_universe" in report["filing_event_gate"]["reasons"]

    tier_rows = list(
        csv.DictReader(result.csv_paths["filing_tier_event_summary"].open(encoding="utf-8"))
    )
    tier_1_row = next(
        row
        for row in tier_rows
        if row["dimension"] == "filing_group"
        and row["dimension_value"] == "tier_1"
        and row["benchmark_mode"] == "same_time_bucket"
        and row["window_minutes"] == "120"
    )
    assert int(tier_1_row["paired_random_sample_count"]) == 2
    assert float(tier_1_row["mean_edge_pct"]) > 1.0

    enriched_rows = list(
        csv.DictReader(
            result.csv_paths["candidate_events_with_filing_tiers"].open(encoding="utf-8")
        )
    )
    by_ticker = {row["ticker"]: row for row in enriched_rows}
    assert by_ticker["FDAA"]["filing_group"] == "tier_1"
    assert by_ticker["DILU"]["filing_group"] == "trap"
    assert by_ticker["NONE"]["filing_group"] == "no_recent_filing"


def test_mito_candidate_event_filing_tier_cli_writes_outputs(tmp_path: Path) -> None:
    candidate_path = _write_candidate_events(tmp_path / "candidate_events.csv")
    random_path = _write_random_events(tmp_path / "random_events.csv")
    filings_path = _write_classified_filings(tmp_path / "classified_filings.csv")

    result = CliRunner().invoke(
        app,
        [
            "audit-mito-candidate-event-filing-tiers",
            "--candidate-event-root",
            str(candidate_path),
            "--classified-filings-csv",
            str(filings_path),
            "--random-events-csv",
            str(random_path),
            "--output-root",
            str(tmp_path / "audit"),
            "--run-id",
            "cli",
            "--min-sample-count",
            "1",
            "--min-mean-edge-pct",
            "1",
            "--min-win-rate-edge-pct",
            "1",
            "--min-ex-trap-improvement-pct",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "audit" / "cli"
    assert (export_dir / "candidate_events_with_filing_tiers.csv").exists()
    assert (export_dir / "filing_tier_event_summary.csv").exists()
    assert (export_dir / "avoid_long_summary.csv").exists()
    assert (export_dir / "filing_event_audit_summary.json").exists()
    assert (export_dir / "filing_event_audit_summary.md").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def _write_candidate_events(path: Path) -> Path:
    rows = [
        {
            "ticker": "FDAA",
            "market_date": "2026-05-02",
            "event_time_et": "09:45",
            "time_bucket": "morning",
            "forward_120m_return_pct": "6.0",
        },
        {
            "ticker": "FDAA",
            "market_date": "2026-05-03",
            "event_time_et": "10:30",
            "time_bucket": "morning",
            "forward_120m_return_pct": "4.0",
        },
        {
            "ticker": "DILU",
            "market_date": "2026-05-02",
            "event_time_et": "09:45",
            "time_bucket": "morning",
            "forward_120m_return_pct": "-8.0",
        },
        {
            "ticker": "NONE",
            "market_date": "2026-05-02",
            "event_time_et": "09:45",
            "time_bucket": "morning",
            "forward_120m_return_pct": "1.0",
        },
    ]
    return _write_csv(path, rows)


def _write_random_events(path: Path) -> Path:
    rows = [
        {
            "candidate_row_id": "0",
            "benchmark_mode": "same_time_bucket",
            "random_control_valid": "True",
            "forward_120m_return_pct": "-1.0",
        },
        {
            "candidate_row_id": "1",
            "benchmark_mode": "same_time_bucket",
            "random_control_valid": "True",
            "forward_120m_return_pct": "-0.5",
        },
        {
            "candidate_row_id": "2",
            "benchmark_mode": "same_time_bucket",
            "random_control_valid": "True",
            "forward_120m_return_pct": "0.0",
        },
        {
            "candidate_row_id": "3",
            "benchmark_mode": "same_time_bucket",
            "random_control_valid": "True",
            "forward_120m_return_pct": "0.0",
        },
    ]
    return _write_csv(path, rows)


def _write_classified_filings(path: Path) -> Path:
    rows = [
        {
            "symbol": "FDAA",
            "form": "8-K",
            "filing_date": "2026-05-01",
            "accession_number": "0001",
            "catalyst_tier": "tier_1",
            "catalyst_direction": "bullish_candidate",
            "trap_flag": "false",
            "classification_reasons": "positive topline",
        },
        {
            "symbol": "DILU",
            "form": "424B5",
            "filing_date": "2026-05-01",
            "accession_number": "0002",
            "catalyst_tier": "trap",
            "catalyst_direction": "bearish_or_no_long",
            "trap_flag": "true",
            "classification_reasons": "prospectus supplement",
        },
    ]
    return _write_csv(path, rows)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path
