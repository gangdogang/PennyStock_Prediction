from __future__ import annotations

import json
from pathlib import Path

from penny_stock_radar.services.replay_grade_stamper import (
    csv_decision_grade_comment,
    stamp_replay_run,
)


def test_stamp_replay_run_pass_fixture_is_decision_grade(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay_pass"
    replay_dir.mkdir()
    _write_summary(replay_dir)
    _write_research_audit(
        replay_dir,
        decision="PASS",
        universe={"decision_blocker": False, "decision_grade": True, "grade_reason": "ok"},
        cost_audit={
            "l1_source_counts": {"kis_l1_snapshot": 50},
            "l1_cost_eligible_source_counts": {"kis_l1_snapshot": 50},
            "minute_spread_source_counts": {},
            "minute_spread_cost_eligible_source_counts": {},
            "excluded_l1_sources": [],
            "excluded_minute_spread_sources": [],
            "cost_source_policy": {
                "kis_l1_snapshot_policy": {
                    "classification": "nbbo_consolidated",
                    "cost_eligible": True,
                    "reason": "fixture",
                }
            },
        },
    )

    grade = stamp_replay_run(replay_dir)

    assert grade.decision_grade is True
    assert grade.grade_reason == "decision_grade_pass"
    assert grade.cost_source_blockers == []
    summary = json.loads((replay_dir / "replay_summary.json").read_text(encoding="utf-8"))
    assert summary["existing_field"] == "kept"
    assert summary["replay_grade"]["decision_grade"] is True
    assert csv_decision_grade_comment(grade) == "# decision_grade=True, reason=decision_grade_pass"


def test_stamp_replay_run_alpaca_only_fixture_is_not_decision_grade(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay_alpaca"
    replay_dir.mkdir()
    _write_summary(replay_dir)
    _write_research_audit(
        replay_dir,
        decision="PASS",
        universe={"decision_blocker": False, "decision_grade": True, "grade_reason": "ok"},
        cost_audit={
            "l1_source_counts": {"alpaca_iex_historical_quotes": 12},
            "l1_cost_eligible_source_counts": {},
            "minute_spread_source_counts": {},
            "minute_spread_cost_eligible_source_counts": {},
            "excluded_l1_sources": ["alpaca_iex_historical_quotes"],
            "excluded_minute_spread_sources": [],
            "cost_source_policy": {
                "diagnostic_only_exact_sources": ["alpaca_iex_historical_quotes"],
            },
        },
    )

    grade = stamp_replay_run(replay_dir)

    assert grade.decision_grade is False
    assert "alpaca_iex_historical_quotes" in grade.grade_reason
    assert "cost_source_policy_violation:alpaca_iex_historical_quotes" in grade.cost_source_blockers
    assert "cost_source_policy_violation:no_cost_eligible_sources" in grade.cost_source_blockers
    summary = json.loads((replay_dir / "replay_summary.json").read_text(encoding="utf-8"))
    assert summary["replay_grade"]["decision_grade"] is False


def test_stamp_replay_run_blocks_missing_falsification_audit(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay_missing"
    replay_dir.mkdir()
    _write_summary(replay_dir)

    grade = stamp_replay_run(replay_dir)

    assert grade.decision_grade is False
    assert grade.coverage_blockers == ["falsification_audit_missing"]
    assert "falsification_audit_missing" in grade.grade_reason


def _write_summary(replay_dir: Path) -> None:
    (replay_dir / "replay_summary.json").write_text(
        json.dumps({"existing_field": "kept"}, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_research_audit(
    replay_dir: Path,
    *,
    decision: str,
    universe: dict,
    cost_audit: dict,
) -> None:
    payload = {
        "decision_gate": {"decision": decision},
        "blockers": [] if decision == "PASS" else ["fixture_blocker"],
        "universe_tradability_audit": universe,
        "cost_audit": cost_audit,
    }
    (replay_dir / "research_audit_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
