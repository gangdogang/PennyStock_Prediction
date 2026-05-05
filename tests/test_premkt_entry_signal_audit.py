from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.premkt_entry_signal_audit import PremktEntrySignalAuditor


def test_entry_signal_audit_flags_top_symbol_concentration(tmp_path: Path) -> None:
    run_dir = _write_signal_run(tmp_path / "june_signal")
    output = tmp_path / "audit.json"
    csv_dir = tmp_path / "audit_csv"

    result = PremktEntrySignalAuditor().audit(
        run_dirs=[run_dir],
        output_path=output,
        csv_dir=csv_dir,
    )

    assert result.report["selected_trade_count"] == 4
    assert output.exists()
    overall = result.report["overall"][0]
    assert overall["total_net_pnl"] == 200.0
    assert overall["top_symbol"] == "AAA"
    assert overall["top_1_symbol_removed_net_pnl"] == -100.0
    assert "june_signal/predictor_weighted: profit disappears after removing the top symbol" in (
        result.report["warnings"]
    )

    feature_rows = list(csv.DictReader((csv_dir / "entry_signal_feature_1r.csv").open()))
    entry_time_rows = [
        row
        for row in feature_rows
        if row["scope"] == "all"
        and row["feature"] == "entry_time_bucket"
        and row["feature_value"] == "09:00-09:29"
    ]
    assert entry_time_rows
    assert entry_time_rows[0]["trade_count"] == "4"
    assert entry_time_rows[0]["reached_1r_rate"] == "0.5"


def test_entry_signal_audit_cli_writes_json_and_csv(tmp_path: Path) -> None:
    run_dir = _write_signal_run(tmp_path / "june_signal")
    output = tmp_path / "audit.json"
    csv_dir = tmp_path / "csv"

    result = CliRunner().invoke(
        app,
        [
            "audit-premkt-entry-signal",
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
            "--csv-dir",
            str(csv_dir),
            "--bucket",
            "predictor_weighted",
        ],
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["filters"]["entry_labels"] == ["OPENING_RANGE_CANDIDATE"]
    assert report["filters"]["entry_setup_states"] == ["STARTER_VALID"]
    assert report["selected_trade_count"] == 4
    assert (csv_dir / "entry_signal_overall.csv").exists()
    assert "Entry signal audit wrote" in result.stdout


def _write_signal_run(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "command": "run-premkt-model-replay",
                "start_date": "2025-06-01",
                "end_date": "2025-06-30",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "replay_summary.json").write_text(
        json.dumps({"start_date": "2025-06-01", "end_date": "2025-06-30"}),
        encoding="utf-8",
    )
    _write_csv(
        run_dir / "paper_trade_log.csv",
        [
            _exit_row("AAA", "2025-06-03", 200.0, 1, "2025-06-03T09:05:00-04:00"),
            _exit_row("BBB", "2025-06-04", -120.0, 0, "2025-06-04T09:10:00-04:00"),
            _exit_row("AAA", "2025-06-05", 100.0, 1, "2025-06-05T09:12:00-04:00"),
            _exit_row("CCC", "2025-06-06", 20.0, 0, "2025-06-06T09:18:00-04:00"),
            _exit_row(
                "DDD",
                "2025-06-09",
                999.0,
                1,
                "2025-06-09T09:20:00-04:00",
                label="CONDITIONAL_ENTRY",
            ),
        ],
    )
    _write_csv(
        run_dir / "paper_setup_features.csv",
        [
            _feature_row("AAA", "2025-06-03", "2025-06-03T09:05:00-04:00", 250000.0),
            _feature_row("BBB", "2025-06-04", "2025-06-04T09:10:00-04:00", 50000.0),
            _feature_row("AAA", "2025-06-05", "2025-06-05T09:12:00-04:00", 300000.0),
            _feature_row("CCC", "2025-06-06", "2025-06-06T09:18:00-04:00", 125000.0),
        ],
    )
    return run_dir


def _exit_row(
    symbol: str,
    market_date: str,
    net_pnl: float,
    reached_1r: int,
    entry_at: str,
    *,
    label: str = "OPENING_RANGE_CANDIDATE",
) -> dict[str, object]:
    return {
        "run_id": "june_signal",
        "market_date": market_date,
        "bucket": "predictor_weighted",
        "symbol": symbol,
        "event": "EXIT",
        "status": "CLOSED",
        "entry_at": entry_at,
        "exit_at": entry_at,
        "net_pnl": net_pnl,
        "realized_pnl_pct": net_pnl / 100.0,
        "analysis_score": 4.1,
        "analysis_label": label,
        "entry_analysis_label": label,
        "exit_analysis_label": label,
        "exit_reason": "session_end" if net_pnl > 0 else "stop_loss",
        "holding_minutes": 12.0,
        "entry_setup_state": "STARTER_VALID",
        "entry_setup_quality": 7,
        "entry_setup_risk": 2,
        "max_r_multiple": 1.4 if reached_1r else 0.4,
        "min_r_multiple": -0.6,
        "reached_1r": reached_1r,
        "intrabar_stop_touched": 0 if net_pnl > 0 else 1,
        "bar_dollar_volume": 200000.0,
        "shares_pct_of_bar_volume": 0.2,
        "notional_pct_of_bar_dollar_volume": 0.2,
    }


def _feature_row(
    symbol: str,
    market_date: str,
    observed_at: str,
    dollar_volume: float,
) -> dict[str, object]:
    return {
        "run_id": "june_signal",
        "market_date": market_date,
        "bucket": "predictor_weighted",
        "symbol": symbol,
        "observed_at": observed_at,
        "position_state": "flat",
        "spread_pct": 1.2,
        "dollar_volume": dollar_volume,
        "distance_to_vwap_pct": 1.5,
        "volume_bar_ratio": 1.8,
        "minutes_since_hod": 3.0,
        "vwap_reclaim": 1,
        "opening_range_breakout": 1,
        "failed_breakout": 0,
        "true_leader": 1,
        "liquidity_ok": 1,
        "catalyst_risk_flag": 0,
        "dilution_risk_flag": 0,
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
