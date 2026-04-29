from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.premkt_replay_validation import (
    REQUIRED_REPORT_KEYS,
    PremktReplayValidationEvaluator,
)


def test_oos_shorter_than_three_months_is_insufficient_data(tmp_path: Path) -> None:
    calibration = _write_replay_run(tmp_path / "calibration", "2026-01-01", "2026-01-31")
    oos = _write_replay_run(tmp_path / "oos", "2026-02-01", "2026-03-15")

    report = PremktReplayValidationEvaluator().evaluate(
        calibration_dir=calibration,
        out_of_sample_dir=oos,
    ).report

    assert report["decision_gate"]["status"] == "insufficient_data"


def test_coverage_warning_blocks_promising_status(tmp_path: Path) -> None:
    calibration = _write_replay_run(tmp_path / "calibration", "2026-01-01", "2026-01-31")
    oos = _write_replay_run(
        tmp_path / "oos",
        "2026-02-01",
        "2026-04-30",
        coverage_warnings=[
            {"market_date": "2026-02-10", "reason": "missing_l1_bid_ask_fallback_to_last_price"}
        ],
    )

    report = PremktReplayValidationEvaluator().evaluate(
        calibration_dir=calibration,
        out_of_sample_dir=oos,
    ).report

    assert report["decision_gate"]["status"] == "failed_data_quality"
    assert report["decision_gate"]["status"] != "promising_needs_shadow"


def test_calibration_good_oos_bad_is_overfit_suspected(tmp_path: Path) -> None:
    calibration = _write_replay_run(
        tmp_path / "calibration",
        "2026-01-01",
        "2026-01-31",
        predictor_pnl=1000.0,
        watchlist_pnl=100.0,
        blind_pnl=50.0,
    )
    oos = _write_replay_run(
        tmp_path / "oos",
        "2026-02-01",
        "2026-04-30",
        predictor_pnl=-50.0,
        watchlist_pnl=100.0,
        blind_pnl=25.0,
    )

    report = PremktReplayValidationEvaluator().evaluate(
        calibration_dir=calibration,
        out_of_sample_dir=oos,
    ).report

    assert report["decision_gate"]["status"] == "overfit_suspected"


def test_predictor_not_better_than_comparisons_is_no_edge(tmp_path: Path) -> None:
    calibration = _write_replay_run(
        tmp_path / "calibration",
        "2026-01-01",
        "2026-01-31",
        predictor_pnl=10.0,
        watchlist_pnl=100.0,
        blind_pnl=50.0,
    )
    oos = _write_replay_run(
        tmp_path / "oos",
        "2026-02-01",
        "2026-04-30",
        predictor_pnl=90.0,
        watchlist_pnl=100.0,
        blind_pnl=25.0,
    )

    report = PremktReplayValidationEvaluator().evaluate(
        calibration_dir=calibration,
        out_of_sample_dir=oos,
    ).report

    assert report["decision_gate"]["status"] == "no_edge_detected"


def test_good_oos_is_capped_at_promising_needs_shadow(tmp_path: Path) -> None:
    calibration = _write_replay_run(tmp_path / "calibration", "2026-01-01", "2026-01-31")
    oos = _write_replay_run(tmp_path / "oos", "2026-02-01", "2026-04-30")

    report = PremktReplayValidationEvaluator().evaluate(
        calibration_dir=calibration,
        out_of_sample_dir=oos,
    ).report

    assert report["decision_gate"]["status"] == "promising_needs_shadow"
    assert report["decision_gate"]["status"] != "live_ready"


def test_evaluation_report_contains_required_keys_and_cli_writes_output(tmp_path: Path) -> None:
    calibration = _write_replay_run(tmp_path / "calibration", "2026-01-01", "2026-01-31")
    oos = _write_replay_run(tmp_path / "oos", "2026-02-01", "2026-04-30")
    output = tmp_path / "evaluation_report.json"

    result = CliRunner().invoke(
        app,
        [
            "evaluate-premkt-replay",
            "--calibration-dir",
            str(calibration),
            "--out-of-sample-dir",
            str(oos),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert set(REQUIRED_REPORT_KEYS) <= set(report)
    assert report["bucket_comparison"]["out_of_sample"]
    assert report["decision_gate"]["status"] == "promising_needs_shadow"


def _write_replay_run(
    run_dir: Path,
    start_date: str,
    end_date: str,
    *,
    predictor_pnl: float = 1000.0,
    watchlist_pnl: float = 100.0,
    blind_pnl: float = 50.0,
    coverage_warnings: list[dict[str, str]] | None = None,
) -> Path:
    run_dir.mkdir(parents=True)
    pnl_by_bucket = {
        "predictor_weighted": predictor_pnl,
        "momentum_only": watchlist_pnl,
        "watchlist_blind_momentum": blind_pnl,
    }
    manifest = {
        "command": "run-premkt-model-replay",
        "start_date": start_date,
        "end_date": end_date,
        "model_path": "data/ml/premkt_model.joblib",
        "score_mode": "blend",
        "ml_weight": 0.5,
        "local_only": True,
        "settings_snapshot": {
            "paper_initial_capital": 10000.0,
            "paper_stop_loss_pct": 5.0,
        },
        "fill_model": {
            "class": "FillModel",
            "min_trade_fee": 0.01,
            "premarket_spread_multiplier": 1.5,
        },
        "leakage_guard": [
            "Point-in-time universe/watchlist snapshot is used.",
            "Predictor features are cutoff-limited.",
        ],
    }
    bucket_results = {
        bucket: _bucket_result(pnl)
        for bucket, pnl in pnl_by_bucket.items()
    }
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "completed_dates": _date_strings(start_date, end_date),
        "skipped_dates": [],
        "failed_dates": [],
        "model_path": manifest["model_path"],
        "score_mode": manifest["score_mode"],
        "ml_weight": manifest["ml_weight"],
        "bucket_results": bucket_results,
        "coverage_warnings": coverage_warnings or [],
        "data_quality_notes": [],
        "leakage_guard_notes": manifest["leakage_guard"],
        "conclusion_status": "comparable",
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    (run_dir / "replay_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    _write_kpis(run_dir / "paper_backtest_kpis.csv", bucket_results)
    _write_trade_log(run_dir / "paper_trade_log.csv", pnl_by_bucket)
    _write_pair_diff(run_dir / "paper_bucket_pair_diff.csv", bucket_results)
    return run_dir


def _bucket_result(pnl: float) -> dict[str, float | int]:
    return {
        "trade_count": 4,
        "closed_trade_count": 4,
        "total_net_pnl": pnl,
        "total_return_pct": pnl / 10000.0 * 100.0,
        "win_rate": 0.75 if pnl > 0 else 0.25,
        "profit_factor": 2.0 if pnl > 0 else 0.5,
        "expectancy_r": pnl / 1000.0,
        "max_drawdown_pct": 2.0,
        "transaction_cost": 4.0,
        "capacity_limited_count": 0,
        "partial_fill_count": 0,
    }


def _write_kpis(path: Path, bucket_results: dict[str, dict[str, float | int]]) -> None:
    fieldnames = ["bucket", *next(iter(bucket_results.values())).keys()]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for bucket, values in bucket_results.items():
            writer.writerow({"bucket": bucket, **values})


def _write_trade_log(path: Path, pnl_by_bucket: dict[str, float]) -> None:
    fieldnames = [
        "bucket",
        "event",
        "status",
        "symbol",
        "quantity",
        "entry_price",
        "exit_price",
        "net_pnl",
        "realized_pnl_pct",
        "transaction_cost",
        "fill_status",
        "capacity_limited",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (bucket, pnl) in enumerate(pnl_by_bucket.items(), start=1):
            writer.writerow(
                {
                    "bucket": bucket,
                    "event": "EXIT",
                    "status": "CLOSED",
                    "symbol": f"AAA{index}",
                    "quantity": 100,
                    "entry_price": 1.0,
                    "exit_price": 1.0 + (pnl / 10000.0),
                    "net_pnl": pnl,
                    "realized_pnl_pct": pnl / 100.0,
                    "transaction_cost": 4.0,
                    "fill_status": "FILLED",
                    "capacity_limited": "0",
                }
            )


def _write_pair_diff(path: Path, bucket_results: dict[str, dict[str, float | int]]) -> None:
    rows = [
        ("predictor_weighted", "momentum_only"),
        ("predictor_weighted", "watchlist_blind_momentum"),
        ("momentum_only", "watchlist_blind_momentum"),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["left_bucket", "right_bucket", "total_net_pnl_diff"],
        )
        writer.writeheader()
        for left, right in rows:
            writer.writerow(
                {
                    "left_bucket": left,
                    "right_bucket": right,
                    "total_net_pnl_diff": (
                        float(bucket_results[left]["total_net_pnl"])
                        - float(bucket_results[right]["total_net_pnl"])
                    ),
                }
            )


def _date_strings(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days
