from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from .csv_utils import iter_non_comment_lines
from .paper_runtime import write_csv


DEFAULT_MIN_TRADES = 100
DEFAULT_MIN_MONTHS = 3
DEFAULT_TOP_N = 5

ROBUSTNESS_CSV_NAMES = {
    "overall": "replay_bucket_robustness_overall.csv",
    "by_month": "replay_bucket_robustness_by_month.csv",
    "by_exit_reason": "replay_bucket_robustness_by_exit_reason.csv",
    "by_symbol": "replay_bucket_robustness_by_symbol.csv",
    "by_date": "replay_bucket_robustness_by_date.csv",
    "stress": "replay_bucket_robustness_stress.csv",
}


@dataclass(frozen=True)
class ReplayBucketRobustnessResult:
    report: dict[str, Any]
    output_path: Path | None
    csv_paths: dict[str, Path]


class ReplayBucketRobustnessAuditor:
    """Reject/promote gate for a single replay bucket.

    This is not a live-trading approval engine. It formalizes the first
    robustness checks a quant should run before allowing more tuning:
    expectancy, payoff geometry, month consistency, concentration, and
    exit-reason attribution.
    """

    def audit(
        self,
        *,
        run_dir: Path | None = None,
        trade_log_path: Path | None = None,
        bucket: str,
        output_path: Path | None = None,
        csv_dir: Path | None = None,
        top_n: int = DEFAULT_TOP_N,
        min_trades: int = DEFAULT_MIN_TRADES,
        min_months: int = DEFAULT_MIN_MONTHS,
        require_all_months_positive: bool = True,
    ) -> ReplayBucketRobustnessResult:
        if trade_log_path is None:
            if run_dir is None:
                raise ValueError("run_dir or trade_log_path is required")
            trade_log_path = run_dir / "paper_trade_log.csv"
        run_dir = run_dir or trade_log_path.parent
        rows = _closed_bucket_rows(_read_csv(trade_log_path), bucket=bucket)

        overall = _stats(rows)
        by_month = _group_stats(rows, key="month")
        by_exit_reason = _group_stats(rows, key="exit_reason")
        by_symbol = _group_stats(rows, key="symbol")
        by_date = _group_stats(rows, key="market_date")
        stress = _stress_rows(rows, top_n=top_n)
        verdict = _verdict(
            overall=overall,
            by_month=by_month,
            stress=stress,
            min_trades=min_trades,
            min_months=min_months,
            require_all_months_positive=require_all_months_positive,
        )

        report: dict[str, Any] = {
            "metadata": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "audit_type": "replay_bucket_robustness",
                "decision_grade": False,
                "decision_grade_reason": "diagnostic_reject_gate_only_not_strategy_approval",
            },
            "filters": {
                "run_dir": str(run_dir),
                "trade_log_path": str(trade_log_path),
                "bucket": bucket,
                "top_n": top_n,
                "min_trades": min_trades,
                "min_months": min_months,
                "require_all_months_positive": require_all_months_positive,
            },
            "status": verdict["status"],
            "status_reasons": verdict["reasons"],
            "recommendation": verdict["recommendation"],
            "overall": overall,
            "by_month": by_month,
            "by_exit_reason": by_exit_reason,
            "direction_counts": dict(Counter(str(row.get("direction") or "") for row in rows)),
            "stress": stress,
            "worst_symbols": _bottom_rows(by_symbol, "total_net_pnl", top_n),
            "worst_dates": _bottom_rows(by_date, "total_net_pnl", top_n),
            "top_symbols": _top_rows(by_symbol, "total_net_pnl", top_n),
            "top_dates": _top_rows(by_date, "total_net_pnl", top_n),
        }

        csv_paths: dict[str, Path] = {}
        if csv_dir is not None:
            csv_dir.mkdir(parents=True, exist_ok=True)
            rows_by_key = {
                "overall": [overall],
                "by_month": by_month,
                "by_exit_reason": by_exit_reason,
                "by_symbol": by_symbol,
                "by_date": by_date,
                "stress": stress,
            }
            for key, name in ROBUSTNESS_CSV_NAMES.items():
                path = csv_dir / name
                write_csv(path, rows_by_key[key])
                csv_paths[key] = path

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        return ReplayBucketRobustnessResult(
            report=report,
            output_path=output_path,
            csv_paths=csv_paths,
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(iter_non_comment_lines(handle)))


def _closed_bucket_rows(rows: Iterable[dict[str, str]], *, bucket: str) -> list[dict[str, str]]:
    normalized_bucket = bucket.strip().lower()
    selected: list[dict[str, str]] = []
    for row in rows:
        row_bucket = str(row.get("bucket") or "").strip().lower()
        event = str(row.get("event") or "").strip().upper()
        status = str(row.get("status") or "").strip().upper()
        if row_bucket != normalized_bucket:
            continue
        if event and event != "EXIT":
            continue
        if status and status != "CLOSED":
            continue
        item = dict(row)
        item["bucket"] = normalized_bucket
        item["market_date"] = _market_date(item)
        item["month"] = item["market_date"][:7] if len(item["market_date"]) >= 7 else ""
        item["exit_reason"] = str(item.get("exit_reason") or "")
        selected.append(item)
    return selected


def _stats(rows: list[dict[str, str]]) -> dict[str, object]:
    pnl_values = [_number(row.get("net_pnl")) for row in rows]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    abs_avg_loss = abs(avg_loss)
    breakeven = (
        abs_avg_loss / (avg_win + abs_avg_loss) * 100.0
        if avg_win > 0 and abs_avg_loss > 0
        else 0.0
    )
    holding_values = [
        _number(row.get("holding_minutes"))
        for row in rows
        if str(row.get("holding_minutes") or "") != ""
    ]
    return {
        "trade_count": len(rows),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": _round((len(wins) / len(rows) * 100.0) if rows else 0.0),
        "breakeven_win_rate_pct": _round(breakeven),
        "win_rate_gap_pct": _round(((len(wins) / len(rows) * 100.0) - breakeven) if rows else 0.0),
        "total_net_pnl": _round(sum(pnl_values)),
        "avg_net_pnl": _round(sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0,
        "median_net_pnl": _round(median(pnl_values)) if pnl_values else 0.0,
        "avg_win": _round(avg_win),
        "avg_loss": _round(avg_loss),
        "payoff_ratio": _round(avg_win / abs_avg_loss) if abs_avg_loss > 0 else 0.0,
        "profit_factor": _round(sum(wins) / abs(sum(losses))) if losses and sum(losses) < 0 else 0.0,
        "avg_holding_minutes": _round(sum(holding_values) / len(holding_values)) if holding_values else 0.0,
    }


def _group_stats(rows: list[dict[str, str]], *, key: str) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "")].append(row)
    output: list[dict[str, object]] = []
    for value, group in sorted(grouped.items()):
        payload = {key: value}
        payload.update(_stats(group))
        output.append(payload)
    return output


def _stress_rows(rows: list[dict[str, str]], *, top_n: int) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for key in ("symbol", "market_date"):
        grouped = _pnl_by_key(rows, key)
        total = sum(grouped.values())
        winners = sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
        losers = sorted(grouped.items(), key=lambda item: (item[1], item[0]))
        for side, ranked in (("top_winner", winners), ("top_loser", losers)):
            for count in sorted({1, 3, max(1, top_n)}):
                removed = ranked[:count]
                removed_pnl = sum(value for _, value in removed)
                output.append(
                    {
                        "dimension": key,
                        "side": side,
                        "removed_count": count,
                        "removed_keys": ",".join(name for name, _ in removed),
                        "removed_net_pnl": _round(removed_pnl),
                        "remaining_net_pnl": _round(total - removed_pnl),
                    }
                )
    return output


def _verdict(
    *,
    overall: dict[str, object],
    by_month: list[dict[str, object]],
    stress: list[dict[str, object]],
    min_trades: int,
    min_months: int,
    require_all_months_positive: bool,
) -> dict[str, object]:
    reasons: list[str] = []
    trade_count = int(overall.get("trade_count") or 0)
    month_count = len([row for row in by_month if str(row.get("month") or "")])
    if trade_count < min_trades:
        reasons.append(f"sample_size_below_min_trades:{trade_count}<{min_trades}")
    if month_count < min_months:
        reasons.append(f"month_count_below_min_months:{month_count}<{min_months}")
    if reasons:
        return {
            "status": "BLOCKED",
            "reasons": reasons,
            "recommendation": "Collect a larger out-of-sample replay before tuning or promoting this bucket.",
        }

    total = _number(overall.get("total_net_pnl"))
    avg = _number(overall.get("avg_net_pnl"))
    win_gap = _number(overall.get("win_rate_gap_pct"))
    if total <= 0:
        reasons.append("negative_total_expectancy")
    if avg <= 0:
        reasons.append("negative_average_expectancy")
    if win_gap < 0:
        reasons.append("actual_win_rate_below_breakeven")

    negative_months = [
        str(row.get("month") or "")
        for row in by_month
        if _number(row.get("total_net_pnl")) <= 0
    ]
    if require_all_months_positive and negative_months:
        reasons.append("non_positive_months:" + ",".join(negative_months))

    for row in stress:
        side = str(row.get("side") or "")
        count = int(row.get("removed_count") or 0)
        remaining = _number(row.get("remaining_net_pnl"))
        if total > 0 and side == "top_winner" and count == 1 and remaining <= 0:
            reasons.append(f"profit_disappears_after_top_{row.get('dimension')}_removal")
        if total <= 0 and side == "top_loser" and count == 3 and remaining > 0:
            reasons.append(f"loss_concentrated_in_top_3_{row.get('dimension')}_tail")

    if reasons:
        return {
            "status": "REJECT",
            "reasons": sorted(set(reasons)),
            "recommendation": (
                "Do not tune stop/target/entry filters from this run. Treat the bucket as "
                "diagnostic-only or use it as an avoid-long signal until null/cost gates pass."
            ),
        }
    return {
        "status": "PROMISING_DIAGNOSTIC",
        "reasons": ["passed_first_order_robustness_checks_not_live_approval"],
        "recommendation": (
            "Freeze parameters and run falsification/null/cost checks. This is not live approval."
        ),
    }


def _pnl_by_key(rows: list[dict[str, str]], key: str) -> dict[str, float]:
    grouped: dict[str, float] = defaultdict(float)
    for row in rows:
        grouped[str(row.get(key) or "")] += _number(row.get("net_pnl"))
    return dict(grouped)


def _top_rows(rows: list[dict[str, object]], metric: str, limit: int) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (-_number(row.get(metric)), str(row)))[:limit]


def _bottom_rows(rows: list[dict[str, object]], metric: str, limit: int) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (_number(row.get(metric)), str(row)))[:limit]


def _market_date(row: dict[str, str]) -> str:
    explicit = str(row.get("market_date") or "")
    if explicit:
        return explicit
    for key in ("entry_at", "opened_at", "exit_at", "closed_at"):
        value = str(row.get(key) or "")
        if len(value) >= 10:
            return value[:10]
    return ""


def _number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float, places: int = 6) -> float:
    return round(float(value), places)
