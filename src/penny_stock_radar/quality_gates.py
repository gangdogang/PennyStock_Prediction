from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .config import AppSettings


@dataclass(frozen=True, slots=True)
class CoverageGateEvaluation:
    ok: bool
    status: str
    summary: str


def evaluate_coverage_gate(
    path: Path,
    *,
    strict: bool = False,
) -> CoverageGateEvaluation:
    if not path.exists():
        summary = f"Coverage gate file missing: {path}"
        if strict:
            return CoverageGateEvaluation(ok=False, status="missing", summary=summary)
        return CoverageGateEvaluation(ok=True, status="missing", summary=summary)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CoverageGateEvaluation(
            ok=False,
            status="invalid",
            summary=f"Coverage gate file unreadable: {path} ({exc})",
        )
    if not isinstance(payload, dict):
        return CoverageGateEvaluation(
            ok=False,
            status="invalid",
            summary=f"Coverage gate file must contain a JSON object: {path}",
        )

    status = str(payload.get("status") or "unknown").strip().lower()
    gate_name = str(payload.get("gate_name") or "step0_l1_coverage")
    market_date = str(payload.get("market_date") or "-")
    threshold_pct = payload.get("threshold_pct")
    symbol_pct = payload.get("symbol_coverage_pct")
    interval_pct = payload.get("interval_coverage_pct")
    summary = (
        f"Coverage gate {status}: {gate_name} "
        f"(market_date={market_date}, threshold={threshold_pct}, "
        f"symbol={symbol_pct}, interval={interval_pct})"
    )
    if status == "passed":
        return CoverageGateEvaluation(ok=True, status=status, summary=summary)
    if status in {"failed", "unknown"}:
        return CoverageGateEvaluation(
            ok=not strict,
            status=status,
            summary=summary,
        )
    return CoverageGateEvaluation(
        ok=False,
        status="invalid",
        summary=f"Coverage gate file has unsupported status {status!r}: {path}",
    )


def default_coverage_gate_path() -> Path:
    return AppSettings().backtest_coverage_gate_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect the latest Step 0 backtest coverage gate status file.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Override the coverage gate status JSON path.",
    )
    parser.add_argument(
        "--strict-coverage-gate",
        action="store_true",
        help="Fail when the gate file is missing or when the latest status is not passed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = args.path or default_coverage_gate_path()
    evaluation = evaluate_coverage_gate(path, strict=args.strict_coverage_gate)
    print(evaluation.summary)
    return 0 if evaluation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
