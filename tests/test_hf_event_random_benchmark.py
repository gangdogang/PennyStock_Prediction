from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.hf_event_random_benchmark import (
    HfEventRandomBenchmarkOptions,
    HfEventRandomBenchmarkRunner,
)


def test_hf_event_random_benchmark_compares_candidate_to_random_controls(
    tmp_path: Path,
) -> None:
    parquet_path = tmp_path / "bars.parquet"
    candidate_path = tmp_path / "candidate_events.csv"
    _write_minute_parquet(parquet_path)
    _write_candidate_events(candidate_path)

    result = HfEventRandomBenchmarkRunner().run(
        HfEventRandomBenchmarkOptions(
            parquet_path=parquet_path,
            candidate_event_root=candidate_path,
            output_root=tmp_path / "benchmark",
            run_id="fixture",
            seed=7,
            random_modes=("same_time_bucket",),
            forward_windows_minutes=(30,),
            primary_cohort="all",
            primary_window_minutes=30,
            min_primary_sample_count=1,
            min_mean_edge_pct=-100.0,
            min_win_rate_edge_pct=-100.0,
        )
    )

    report = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert report["metadata"]["decision_grade"] is False
    assert report["metadata"]["cost_grade"] == "none"
    assert report["candidate_event_count"] == 3
    assert report["random_control_count"] == 3
    assert report["processing"]["random_outcome_chunk_count"] == 1
    assert report["benchmark_gate"]["status"] == "PASS"
    assert report["primary_comparisons"][0]["metric"] == "return_pct"

    random_rows = list(csv.DictReader(result.csv_paths["random_events"].open(encoding="utf-8")))
    assert {row["benchmark_mode"] for row in random_rows} == {"same_time_bucket"}
    assert {row["random_control_valid"] for row in random_rows} == {"True"}
    first_seed_minutes = [row["random_event_minute"] for row in random_rows]

    repeat = HfEventRandomBenchmarkRunner().run(
        HfEventRandomBenchmarkOptions(
            parquet_path=parquet_path,
            candidate_event_root=candidate_path,
            output_root=tmp_path / "benchmark_repeat",
            run_id="fixture",
            seed=7,
            random_modes=("same_time_bucket",),
            forward_windows_minutes=(30,),
            primary_cohort="all",
            primary_window_minutes=30,
            min_primary_sample_count=1,
            min_mean_edge_pct=-100.0,
            min_win_rate_edge_pct=-100.0,
        )
    )
    repeat_rows = list(csv.DictReader(repeat.csv_paths["random_events"].open(encoding="utf-8")))
    assert [row["random_event_minute"] for row in repeat_rows] == first_seed_minutes

    comparison_rows = list(
        csv.DictReader(result.csv_paths["comparison_summary"].open(encoding="utf-8"))
    )
    return_row = next(
        row
        for row in comparison_rows
        if row["cohort"] == "all"
        and row["benchmark_mode"] == "same_time_bucket"
        and row["window_minutes"] == "30"
        and row["metric"] == "return_pct"
    )
    assert return_row["paired_sample_count"] == "3"
    assert float(return_row["candidate_mean"]) > 0.0
    assert return_row["missing_random_control_count"] == "0"


def test_hf_event_random_benchmark_cli_writes_outputs(tmp_path: Path) -> None:
    parquet_path = tmp_path / "bars.parquet"
    candidate_path = tmp_path / "candidate_events.csv"
    _write_minute_parquet(parquet_path)
    _write_candidate_events(candidate_path)

    result = CliRunner().invoke(
        app,
        [
            "run-hf-event-random-benchmark",
            "--parquet-path",
            str(parquet_path),
            "--candidate-event-root",
            str(candidate_path),
            "--output-root",
            str(tmp_path / "benchmark"),
            "--run-id",
            "cli",
            "--random-mode",
            "same_time_bucket",
            "--primary-cohort",
            "all",
            "--primary-window-minutes",
            "30",
            "--min-primary-sample-count",
            "1",
            "--min-mean-edge-pct",
            "-100",
            "--min-win-rate-edge-pct",
            "-100",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "benchmark" / "cli"
    assert (export_dir / "hf_event_random_benchmark_summary.json").exists()
    assert (export_dir / "hf_event_random_benchmark_summary.md").exists()
    assert (export_dir / "comparison_summary.csv").exists()
    assert (export_dir / "random_events.csv").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def test_mito_event_random_benchmark_cli_accepts_directory_input(tmp_path: Path) -> None:
    input_root = tmp_path / "mito" / "data"
    candidate_path = tmp_path / "candidate_events.csv"
    _write_minute_parquet(input_root / "ohlcv_2026-05.parquet")
    _write_candidate_events(candidate_path)

    result = CliRunner().invoke(
        app,
        [
            "run-mito-event-random-benchmark",
            "--input-root",
            str(input_root),
            "--candidate-event-root",
            str(candidate_path),
            "--output-root",
            str(tmp_path / "mito_benchmark"),
            "--run-id",
            "cli",
            "--random-mode",
            "same_time_bucket",
            "--primary-cohort",
            "all",
            "--primary-window-minutes",
            "30",
            "--min-primary-sample-count",
            "1",
            "--min-mean-edge-pct",
            "-100",
            "--min-win-rate-edge-pct",
            "-100",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "mito_benchmark" / "cli"
    report = json.loads(
        (export_dir / "hf_event_random_benchmark_summary.json").read_text(encoding="utf-8")
    )
    assert report["metadata"]["dataset"] == "mito0o852_OHLCV_1m"
    assert report["input"]["parquet"]["input_type"] == "directory"
    assert report["input"]["parquet"]["parquet_file_count"] == 1
    assert report["thresholds"]["random_outcome_chunk_months"] == 1
    assert (export_dir / "comparison_summary.csv").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def _write_minute_parquet(path: Path) -> None:
    pl = pytest.importorskip("polars")
    rows = []
    for day, day_offset in [("2026-05-01", 0), ("2026-05-02", 100)]:
        year, month, dom = [int(part) for part in day.split("-")]
        for ticker, base in [("ABCD", 1.0 + day_offset), ("WXYZ", 2.0 + day_offset)]:
            for minute_offset in range(0, 181):
                et_minute = 570 + minute_offset
                utc_hour = (et_minute // 60) + 4
                utc_minute = et_minute % 60
                close = base + (minute_offset * 0.001)
                rows.append(
                    {
                        "ticker": ticker,
                        "timestamp": datetime(
                            year,
                            month,
                            dom,
                            utc_hour,
                            utc_minute,
                            tzinfo=timezone.utc,
                        ),
                        "open": close,
                        "high": close + 0.005,
                        "low": close - 0.005,
                        "close": close,
                        "volume": 1000,
                    }
                )
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def _write_candidate_events(path: Path) -> None:
    rows = [
        _candidate_row("ABCD", "2026-05-01", "09:45", 5.0),
        _candidate_row("WXYZ", "2026-05-01", "10:30", 4.0),
        _candidate_row("ABCD", "2026-05-02", "09:45", 3.0),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _candidate_row(
    ticker: str,
    market_date: str,
    event_time: str,
    forward_return: float,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "market_date": market_date,
        "market_month": market_date[:7],
        "event_time_et": event_time,
        "time_bucket": "real_money_morning",
        "event_price": 1.0,
        "forward_30m_return_pct": forward_return,
        "forward_30m_max_up_pct": forward_return + 1.0,
        "forward_30m_max_down_pct": -1.0,
        "decision_grade": "false",
        "cost_grade": "none",
    }
