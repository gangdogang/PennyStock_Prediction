from __future__ import annotations

import json
from pathlib import Path

from penny_stock_radar.quality_gates import evaluate_coverage_gate


def test_evaluate_coverage_gate_accepts_missing_file_in_non_strict_mode(tmp_path: Path) -> None:
    result = evaluate_coverage_gate(tmp_path / "missing.json", strict=False)

    assert result.ok is True
    assert result.status == "missing"
    assert "missing" in result.summary


def test_evaluate_coverage_gate_rejects_missing_file_in_strict_mode(tmp_path: Path) -> None:
    result = evaluate_coverage_gate(tmp_path / "missing.json", strict=True)

    assert result.ok is False
    assert result.status == "missing"


def test_evaluate_coverage_gate_handles_failed_status_by_mode(tmp_path: Path) -> None:
    gate_path = tmp_path / "coverage_gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "gate_name": "step0_l1_coverage_60",
                "market_date": "2026-04-17",
                "threshold_pct": 60.0,
                "symbol_coverage_pct": 52.0,
                "interval_coverage_pct": 44.0,
            }
        ),
        encoding="utf-8",
    )

    permissive = evaluate_coverage_gate(gate_path, strict=False)
    strict = evaluate_coverage_gate(gate_path, strict=True)

    assert permissive.ok is True
    assert permissive.status == "failed"
    assert strict.ok is False
    assert strict.status == "failed"


def test_evaluate_coverage_gate_accepts_passed_status(tmp_path: Path) -> None:
    gate_path = tmp_path / "coverage_gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "gate_name": "step0_l1_coverage_60",
                "market_date": "2026-04-17",
                "threshold_pct": 60.0,
                "symbol_coverage_pct": 68.0,
                "interval_coverage_pct": 66.0,
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_coverage_gate(gate_path, strict=True)

    assert result.ok is True
    assert result.status == "passed"
    assert "step0_l1_coverage_60" in result.summary


def test_evaluate_coverage_gate_rejects_invalid_json(tmp_path: Path) -> None:
    gate_path = tmp_path / "coverage_gate.json"
    gate_path.write_text("{invalid", encoding="utf-8")

    result = evaluate_coverage_gate(gate_path, strict=False)

    assert result.ok is False
    assert result.status == "invalid"
