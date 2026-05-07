from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.filing_catalyst_tier_audit import (
    FilingCatalystTierAuditOptions,
    FilingCatalystTierAuditor,
    classify_filing_catalyst,
)


def test_classify_filing_catalyst_trap_overrides_bullish_terms() -> None:
    row = {
        "symbol": "DILU",
        "form": "S-3",
        "summary": "FDA approval and prospectus supplement for registered direct offering with warrants",
        "matched_keywords": '["fda approval", "prospectus supplement", "warrant"]',
    }

    classified = classify_filing_catalyst(row)

    assert classified["catalyst_tier"] == "trap"
    assert classified["trap_flag"] == "true"
    assert "prospectus supplement" in classified["classification_reasons"]
    assert "form:S-3" in classified["classification_reasons"]


def test_filing_catalyst_tier_audit_writes_inventory(tmp_path: Path) -> None:
    csv_path = _write_filings_csv(tmp_path / "filings.csv")

    result = FilingCatalystTierAuditor().audit(
        FilingCatalystTierAuditOptions(
            filings_csv=csv_path,
            output_root=tmp_path / "tier_audit",
            run_id="fixture",
            min_total_filings=1,
        )
    )

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["decision_grade"] is False
    assert summary["metadata"]["cost_grade"] == "none"
    assert summary["filing_count"] == 4
    assert summary["symbol_count"] == 4
    assert summary["tier_counts"]["trap"] == 1
    assert summary["tier_counts"]["tier_1"] == 1
    assert summary["tier_counts"]["tier_2"] == 1
    assert summary["tier_counts"]["tier_3"] == 1
    assert summary["filing_catalyst_tier_gate"]["status"] == "PASS"

    rows = list(csv.DictReader(result.classified_csv_path.open(encoding="utf-8")))
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["FDAA"]["catalyst_tier"] == "tier_1"
    assert by_symbol["PART"]["catalyst_tier"] == "tier_2"
    assert by_symbol["GENR"]["catalyst_tier"] == "tier_3"
    assert by_symbol["DILU"]["trap_flag"] == "true"


def test_filing_catalyst_tier_cli_writes_outputs(tmp_path: Path) -> None:
    csv_path = _write_filings_csv(tmp_path / "filings.csv")

    result = CliRunner().invoke(
        app,
        [
            "audit-filing-catalyst-tiers",
            "--filings-csv",
            str(csv_path),
            "--output-root",
            str(tmp_path / "tier_audit"),
            "--run-id",
            "cli",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "tier_audit" / "cli"
    assert (export_dir / "classified_filings.csv").exists()
    assert (export_dir / "filing_catalyst_tier_summary.json").exists()
    assert (export_dir / "filing_catalyst_tier_summary.md").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def _write_filings_csv(path: Path) -> Path:
    rows = [
        {
            "symbol": "FDAA",
            "form": "8-K",
            "filing_date": "2026-05-01",
            "acceptance_datetime": "2026-05-01T07:30:00-04:00",
            "accession_number": "0001",
            "primary_doc_description": "Clinical trial positive topline results",
            "item_numbers": '["8.01"]',
            "themes": '["biotech"]',
            "matched_keywords": '["positive topline"]',
            "summary": "8-K | positive topline phase 3 clinical trial",
        },
        {
            "symbol": "PART",
            "form": "8-K",
            "filing_date": "2026-05-01",
            "acceptance_datetime": "2026-05-01T07:31:00-04:00",
            "accession_number": "0002",
            "primary_doc_description": "Strategic partnership",
            "item_numbers": '["1.01"]',
            "themes": '["ai"]',
            "matched_keywords": '["partnership"]',
            "summary": "8-K | partnership and collaboration",
        },
        {
            "symbol": "GENR",
            "form": "8-K",
            "filing_date": "2026-05-01",
            "acceptance_datetime": "2026-05-01T07:32:00-04:00",
            "accession_number": "0003",
            "primary_doc_description": "Other events",
            "item_numbers": '["8.01"]',
            "themes": "[]",
            "matched_keywords": "[]",
            "summary": "8-K | corporate update",
        },
        {
            "symbol": "DILU",
            "form": "424B5",
            "filing_date": "2026-05-01",
            "acceptance_datetime": "2026-05-01T07:33:00-04:00",
            "accession_number": "0004",
            "primary_doc_description": "Prospectus supplement",
            "item_numbers": "[]",
            "themes": "[]",
            "matched_keywords": '["prospectus supplement", "warrant"]',
            "summary": "424B5 | prospectus supplement registered direct offering warrants",
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path
