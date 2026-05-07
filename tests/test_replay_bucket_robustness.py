from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.replay_bucket_robustness import ReplayBucketRobustnessAuditor


def test_replay_bucket_robustness_rejects_negative_payoff_geometry(tmp_path: Path) -> None:
    run_dir = _write_replay_run(tmp_path / "fade_run")
    output = tmp_path / "robustness.json"
    csv_dir = tmp_path / "csv"

    result = ReplayBucketRobustnessAuditor().audit(
        run_dir=run_dir,
        bucket="fade_short",
        output_path=output,
        csv_dir=csv_dir,
        min_trades=4,
        min_months=2,
        top_n=2,
    )

    assert result.report["status"] == "REJECT"
    assert result.report["overall"]["trade_count"] == 4
    assert result.report["overall"]["win_rate_pct"] == 50.0
    assert result.report["overall"]["breakeven_win_rate_pct"] == 66.666667
    assert "actual_win_rate_below_breakeven" in result.report["status_reasons"]
    assert "non_positive_months:2025-04,2025-05" in result.report["status_reasons"]
    assert output.exists()
    assert (csv_dir / "replay_bucket_robustness_by_month.csv").exists()

    month_rows = list(csv.DictReader((csv_dir / "replay_bucket_robustness_by_month.csv").open()))
    assert {row["month"] for row in month_rows} == {"2025-04", "2025-05"}


def test_replay_bucket_robustness_cli_writes_default_outputs(tmp_path: Path) -> None:
    run_dir = _write_replay_run(tmp_path / "fade_run")

    result = CliRunner().invoke(
        app,
        [
            "audit-replay-bucket-robustness",
            "--run-dir",
            str(run_dir),
            "--bucket",
            "fade_short",
            "--min-trades",
            "4",
            "--min-months",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stdout
    report_path = run_dir / "replay_bucket_robustness_fade_short.json"
    csv_dir = run_dir / "replay_bucket_robustness_fade_short"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "REJECT"
    assert report["metadata"]["decision_grade"] is False
    assert (csv_dir / "replay_bucket_robustness_overall.csv").exists()
    assert "Replay bucket robustness wrote" in result.stdout
    assert "REJECT" in result.stdout


def _write_replay_run(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True)
    _write_csv(
        run_dir / "paper_trade_log.csv",
        [
            _row("AAA", "2025-04-01", 10.0, "short_target"),
            _row("BBB", "2025-04-02", -20.0, "short_stop_loss"),
            _row("CCC", "2025-05-01", 10.0, "short_target"),
            _row("DDD", "2025-05-02", -20.0, "short_stop_loss"),
            _row("LONG", "2025-05-03", 999.0, "target", bucket="predictor_weighted"),
        ],
    )
    return run_dir


def _row(
    symbol: str,
    market_date: str,
    net_pnl: float,
    exit_reason: str,
    *,
    bucket: str = "fade_short",
) -> dict[str, object]:
    return {
        "event": "EXIT",
        "status": "CLOSED",
        "bucket": bucket,
        "direction": "SHORT" if bucket == "fade_short" else "LONG",
        "symbol": symbol,
        "market_date": market_date,
        "entry_at": f"{market_date}T09:45:00-04:00",
        "exit_at": f"{market_date}T15:55:00-04:00",
        "holding_minutes": 120,
        "net_pnl": net_pnl,
        "exit_reason": exit_reason,
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
