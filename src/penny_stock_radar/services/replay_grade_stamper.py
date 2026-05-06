from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .kis_quote_consolidation_check import KIS_L1_SOURCE, LATEST_KIS_CONSOLIDATION_PATH


@dataclass(frozen=True, slots=True)
class ReplayGrade:
    decision_grade: bool
    grade_reason: str
    cost_source_blockers: list[str]
    universe_blockers: list[str]
    coverage_blockers: list[str]
    stamped_at: datetime


def stamp_replay_run(replay_dir: Path) -> ReplayGrade:
    base = Path(replay_dir)
    report = _load_falsification_report(base)
    cost_blockers: list[str] = []
    universe_blockers: list[str] = []
    coverage_blockers: list[str] = []

    if report is None:
        coverage_blockers.append("falsification_audit_missing")
    else:
        decision = _decision_gate_value(report)
        if decision != "PASS":
            coverage_blockers.append(f"falsification_decision_gate_not_pass:{decision or 'missing'}")
        coverage_blockers.extend(_coverage_blockers(report))
        universe_blockers.extend(_universe_blockers(report, base))
        cost_blockers.extend(_cost_source_blockers(report, base))

    cost_blockers = sorted(set(cost_blockers))
    universe_blockers = sorted(set(universe_blockers))
    coverage_blockers = sorted(set(coverage_blockers))
    decision_grade = not (cost_blockers or universe_blockers or coverage_blockers)
    reason = _grade_reason(
        decision_grade=decision_grade,
        cost_blockers=cost_blockers,
        universe_blockers=universe_blockers,
        coverage_blockers=coverage_blockers,
    )
    grade = ReplayGrade(
        decision_grade=decision_grade,
        grade_reason=reason,
        cost_source_blockers=cost_blockers,
        universe_blockers=universe_blockers,
        coverage_blockers=coverage_blockers,
        stamped_at=datetime.now(timezone.utc),
    )
    _update_replay_summary(base, grade)
    return grade


def replay_grade_to_dict(grade: ReplayGrade) -> dict[str, Any]:
    payload = asdict(grade)
    payload["stamped_at"] = grade.stamped_at.isoformat()
    return payload


def csv_decision_grade_comment(grade: ReplayGrade) -> str:
    reason = grade.grade_reason.replace("\n", " ").replace("\r", " ")
    return f"# decision_grade={grade.decision_grade}, reason={reason}"


def load_replay_grade(replay_dir: Path) -> dict[str, Any] | None:
    summary = _read_json(Path(replay_dir) / "replay_summary.json")
    if not isinstance(summary, dict):
        return None
    grade = summary.get("replay_grade")
    return grade if isinstance(grade, dict) else None


def _load_falsification_report(replay_dir: Path) -> dict[str, Any] | None:
    summary = _read_json(replay_dir / "replay_summary.json")
    candidates: list[Path] = []
    if isinstance(summary, dict):
        for key in (
            "falsification_audit_path",
            "research_audit_report_path",
            "falsification_audit_report_path",
        ):
            value = summary.get(key)
            if value:
                path = Path(str(value))
                candidates.append(path if path.is_absolute() else replay_dir / path)
    candidates.extend(
        [
            replay_dir / "research_audit_report.json",
            replay_dir / "falsification_audit" / "research_audit_report.json",
            replay_dir / "falsification" / "research_audit_report.json",
            replay_dir / "audit" / "research_audit_report.json",
            replay_dir / "falsification_audit_report.json",
        ]
    )
    candidates.extend(sorted(replay_dir.glob("**/research_audit_report.json")))
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = _read_json(path)
        if isinstance(payload, dict):
            return payload
    return None


def _decision_gate_value(report: dict[str, Any]) -> str:
    gate = report.get("decision_gate")
    if not isinstance(gate, dict):
        return ""
    return str(gate.get("decision") or gate.get("status") or "").upper()


def _coverage_blockers(report: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for blocker in report.get("blockers") or []:
        text = str(blocker)
        lower = text.lower()
        if "universe_kis_untradable" in lower:
            continue
        if any(token in lower for token in ("cost", "spread", "l1", "quote", "alpaca", "nbbo")):
            continue
        blockers.append(f"falsification_blocker:{text}")
    return blockers


def _universe_blockers(report: dict[str, Any], replay_dir: Path) -> list[str]:
    payload = report.get("universe_tradability_audit")
    if not isinstance(payload, dict):
        payload = _read_json(replay_dir / "universe_tradability_report.json")
    if not isinstance(payload, dict):
        return ["universe_tradability_audit_missing"]
    if bool(payload.get("decision_blocker")) or payload.get("decision_grade") is False:
        return [str(payload.get("grade_reason") or "universe_tradability_blocked")]
    return []


def _cost_source_blockers(report: dict[str, Any], replay_dir: Path) -> list[str]:
    cost_audit = report.get("cost_audit")
    if not isinstance(cost_audit, dict):
        return ["cost_audit_missing"]

    blockers: list[str] = []
    source_counts = _source_counts(cost_audit)
    excluded_sources = set(_list_strings(cost_audit.get("excluded_l1_sources")))
    excluded_sources.update(_list_strings(cost_audit.get("excluded_minute_spread_sources")))
    for source in sorted(excluded_sources & set(source_counts)):
        blockers.append(f"cost_source_policy_violation:{source}")

    eligible_count = sum(
        int(value or 0)
        for key in (
            "l1_cost_eligible_source_counts",
            "minute_spread_cost_eligible_source_counts",
        )
        for value in (cost_audit.get(key) or {}).values()
    )
    if source_counts and eligible_count <= 0:
        blockers.append("cost_source_policy_violation:no_cost_eligible_sources")

    if KIS_L1_SOURCE in source_counts:
        classification = _kis_classification(cost_audit, replay_dir)
        if classification != "nbbo_consolidated":
            blockers.append(f"kis_quote_consolidation_not_nbbo:{classification or 'missing'}")
    return blockers


def _source_counts(cost_audit: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("l1_source_counts", "minute_spread_source_counts"):
        value = cost_audit.get(key)
        if not isinstance(value, dict):
            continue
        for source, count in value.items():
            counts[str(source)] = counts.get(str(source), 0) + int(count or 0)
    return counts


def _kis_classification(cost_audit: dict[str, Any], replay_dir: Path) -> str:
    policy = cost_audit.get("cost_source_policy")
    if isinstance(policy, dict):
        kis_policy = policy.get("kis_l1_snapshot_policy")
        if isinstance(kis_policy, dict) and kis_policy.get("classification"):
            return str(kis_policy["classification"])
    for path in (
        replay_dir / LATEST_KIS_CONSOLIDATION_PATH,
        Path.cwd() / LATEST_KIS_CONSOLIDATION_PATH,
    ):
        verdict = _read_json(path)
        if isinstance(verdict, dict) and verdict.get("classification"):
            return str(verdict["classification"])
    return ""


def _grade_reason(
    *,
    decision_grade: bool,
    cost_blockers: list[str],
    universe_blockers: list[str],
    coverage_blockers: list[str],
) -> str:
    if decision_grade:
        return "decision_grade_pass"
    reasons = [
        *(f"cost:{item}" for item in cost_blockers),
        *(f"universe:{item}" for item in universe_blockers),
        *(f"coverage:{item}" for item in coverage_blockers),
    ]
    return "; ".join(reasons)


def _update_replay_summary(replay_dir: Path, grade: ReplayGrade) -> None:
    summary_path = replay_dir / "replay_summary.json"
    summary = _read_json(summary_path)
    if not isinstance(summary, dict):
        return
    summary["replay_grade"] = replay_grade_to_dict(grade)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
