from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.config import (
    HF_STOCKS_1M_DATASET_RELATIVE_PATH,
    HF_STOCKS_1M_FALLBACK_PATH,
    resolve_hf_stocks_1m_path,
)
from penny_stock_radar.services.hf_1m_bars import (
    HfCandidateDayOptions,
    HfCandidateDaySegmenter,
    HfCandidateEventOptions,
    HfCandidateEventSegmenter,
    Hf1mBarsAuditError,
    Hf1mBarsAuditOptions,
    Hf1mBarsAuditor,
)


def test_hf_1m_path_resolver_env_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PSR_DATA_ROOT", raising=False)
    monkeypatch.delenv("PSR_HF_STOCKS_1M_PATH", raising=False)

    fallback = resolve_hf_stocks_1m_path()
    assert fallback.path == HF_STOCKS_1M_FALLBACK_PATH
    assert fallback.source == "fallback"

    data_root = tmp_path / "external_data"
    monkeypatch.setenv("PSR_DATA_ROOT", str(data_root))
    from_data_root = resolve_hf_stocks_1m_path()
    assert from_data_root.path == data_root / HF_STOCKS_1M_DATASET_RELATIVE_PATH
    assert from_data_root.source == "PSR_DATA_ROOT"
    assert from_data_root.data_root == data_root

    env_path = tmp_path / "env.parquet"
    monkeypatch.setenv("PSR_HF_STOCKS_1M_PATH", str(env_path))
    from_env_path = resolve_hf_stocks_1m_path()
    assert from_env_path.path == env_path
    assert from_env_path.source == "PSR_HF_STOCKS_1M_PATH"

    explicit_path = tmp_path / "explicit.parquet"
    explicit = resolve_hf_stocks_1m_path(explicit_path)
    assert explicit.path == explicit_path
    assert explicit.source == "explicit"


def test_hf_1m_missing_file_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(Hf1mBarsAuditError, match="HF 1m bars parquet not found") as exc_info:
        Hf1mBarsAuditor().run(
            Hf1mBarsAuditOptions(parquet_path=tmp_path / "missing.parquet")
        )

    message = str(exc_info.value)
    assert "PSR_HF_STOCKS_1M_PATH" in message
    assert "PSR_DATA_ROOT" in message


def test_hf_1m_schema_and_audit_work_on_tiny_fixture(tmp_path: Path) -> None:
    parquet_path = tmp_path / "tiny_hf_1m.parquet"
    _write_tiny_parquet(parquet_path)

    result = Hf1mBarsAuditor().run(
        Hf1mBarsAuditOptions(
            parquet_path=parquet_path,
            output_root=tmp_path / "audits",
            run_id="fixture",
        )
    )

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["decision_grade"] is False
    assert summary["metadata"]["cost_grade"] == "none"
    assert summary["file"]["exists"] is True
    assert summary["schema"]["column_mapping"]["ticker"] == "ticker"
    assert [column["name"] for column in summary["schema"]["columns"]] == [
        "ticker",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert summary["row_count"] == 5
    assert summary["ticker_count"] == 2
    assert summary["ohlc_sanity_failure_count"] == 0
    assert summary["duplicate_ticker_timestamp_count"] == 0
    assert summary["low_price_candidate_counts"] == {
        "close_lte_10": 5,
        "close_lte_5": 3,
        "close_lte_1": 0,
    }
    assert summary["high_move_candidate_day_counts"] == {
        "daily_high_open_gte_1_10": 2,
        "daily_high_open_gte_1_20": 2,
        "daily_high_open_gte_1_50": 1,
    }
    assert summary["top_tickers_by_row_count"][0] == {"ticker": "ABCD", "row_count": 3}
    assert result.summary_md_path.exists()
    assert "gross OHLCV falsification data only" in result.summary_md_path.read_text(
        encoding="utf-8"
    )


def test_hf_1m_ohlc_sanity_detects_bad_rows(tmp_path: Path) -> None:
    parquet_path = tmp_path / "bad_ohlc.parquet"
    _write_parquet(
        parquet_path,
        [
            {
                "ticker": "BAD",
                "timestamp": datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 1.8,
                "low": 1.7,
                "close": 1.9,
                "volume": 100,
            },
            {
                "ticker": "OK",
                "timestamp": datetime(2026, 5, 1, 13, 31, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 2.2,
                "low": 1.9,
                "close": 2.1,
                "volume": 100,
            },
        ],
    )

    result = Hf1mBarsAuditor().run(
        Hf1mBarsAuditOptions(
            parquet_path=parquet_path,
            output_root=tmp_path / "audits",
            run_id="bad",
        )
    )

    assert result.summary["ohlc_sanity_failure_count"] == 1


def test_hf_1m_extended_hours_counts_work(tmp_path: Path) -> None:
    parquet_path = tmp_path / "sessions.parquet"
    _write_tiny_parquet(parquet_path)

    result = Hf1mBarsAuditor().run(
        Hf1mBarsAuditOptions(
            parquet_path=parquet_path,
            output_root=tmp_path / "audits",
            run_id="sessions",
        )
    )

    assert result.summary["extended_hours_row_counts"] == {
        "04:00-09:29_et": 1,
        "09:30-16:00_et": 2,
        "16:00-20:00_et": 2,
    }


def test_hf_1m_cli_writes_audit_artifacts(tmp_path: Path) -> None:
    parquet_path = tmp_path / "tiny_hf_1m.parquet"
    _write_tiny_parquet(parquet_path)

    result = CliRunner().invoke(
        app,
        [
            "audit-hf-1m-bars",
            "--parquet-path",
            str(parquet_path),
            "--output-root",
            str(tmp_path / "audits"),
            "--run-id",
            "cli",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "audits" / "cli"
    assert (export_dir / "audit_summary.json").exists()
    assert (export_dir / "audit_summary.md").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def test_hf_candidate_day_segmenter_writes_candidate_days(tmp_path: Path) -> None:
    parquet_path = tmp_path / "tiny_hf_1m.parquet"
    _write_tiny_parquet(parquet_path)

    result = HfCandidateDaySegmenter().run(
        HfCandidateDayOptions(
            parquet_path=parquet_path,
            output_root=tmp_path / "candidate_days",
            run_id="fixture",
            min_regular_rows=1,
            min_early_dollar_volume=100,
            min_early_move_pct=5,
            min_afternoon_dollar_volume=100,
            min_afternoon_move_pct=1,
            min_daily_high_open_pct=10,
            min_candidate_days=1,
            min_active_months=1,
            max_top_ticker_pct=100,
            max_top10_ticker_pct=100,
            max_top_month_pct=100,
            chunk_months=1,
        )
    )

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["decision_grade"] is False
    assert summary["metadata"]["cost_grade"] == "none"
    assert summary["candidate_day_gate"]["status"] == "PASS"
    assert summary["processing"]["chunk_count"] == 1
    assert summary["candidate_day_count"] >= 1
    assert summary["candidate_flag_counts"]["posthoc_high_move_label"] >= 1
    csv_text = result.candidate_csv_path.read_text(encoding="utf-8")
    assert "gross_ohlcv_only_missing_l1_news_float_halt_kis_tradability" in csv_text


def test_hf_candidate_day_cli_writes_segmentation_artifacts(tmp_path: Path) -> None:
    parquet_path = tmp_path / "tiny_hf_1m.parquet"
    _write_tiny_parquet(parquet_path)

    result = CliRunner().invoke(
        app,
        [
            "segment-hf-candidate-days",
            "--parquet-path",
            str(parquet_path),
            "--output-root",
            str(tmp_path / "candidate_days"),
            "--run-id",
            "cli",
            "--min-regular-rows",
            "1",
            "--min-candidate-days",
            "1",
            "--min-active-months",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "candidate_days" / "cli"
    assert (export_dir / "candidate_days.csv").exists()
    assert (export_dir / "candidate_day_summary.json").exists()
    assert (export_dir / "candidate_day_summary.md").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def test_hf_candidate_event_segmenter_uses_event_time_features_only(tmp_path: Path) -> None:
    parquet_path = tmp_path / "event_hf_1m.parquet"
    _write_event_parquet(parquet_path)

    result = HfCandidateEventSegmenter().run(
        HfCandidateEventOptions(
            parquet_path=parquet_path,
            output_root=tmp_path / "candidate_events",
            run_id="fixture",
            event_times_et=("09:45",),
            forward_windows_minutes=(30, 60),
            min_rows_to_event=2,
            max_event_staleness_minutes=2,
            min_event_dollar_volume=100,
            min_event_move_pct=5,
            min_candidate_events=1,
            min_active_months=1,
            max_top_ticker_pct=100,
            max_top10_ticker_pct=100,
            max_top_month_pct=100,
            chunk_months=1,
        )
    )

    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["metadata"]["decision_grade"] is False
    assert summary["metadata"]["cost_grade"] == "none"
    assert summary["candidate_event_gate"]["status"] == "PASS"
    assert summary["candidate_event_count"] == 1
    assert summary["thresholds"]["event_times_et"] == ["09:45"]
    rows = list(csv.DictReader(result.event_csv_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABCD"
    assert rows[0]["event_staleness_minutes"] == "0"
    assert "forward_30m_return_pct" in rows[0]
    assert (
        rows[0]["blocked_context_reasons"]
        == "gross_ohlcv_event_only_missing_l1_news_float_halt_kis_tradability"
    )


def test_hf_candidate_event_cli_writes_segmentation_artifacts(tmp_path: Path) -> None:
    parquet_path = tmp_path / "event_hf_1m.parquet"
    _write_event_parquet(parquet_path)

    result = CliRunner().invoke(
        app,
        [
            "segment-hf-candidate-events",
            "--parquet-path",
            str(parquet_path),
            "--output-root",
            str(tmp_path / "candidate_events"),
            "--run-id",
            "cli",
            "--event-time",
            "09:45",
            "--forward-window",
            "30",
            "--min-rows-to-event",
            "2",
            "--max-event-staleness-minutes",
            "2",
            "--min-event-dollar-volume",
            "100",
            "--min-event-move-pct",
            "5",
            "--min-candidate-events",
            "1",
            "--min-active-months",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    export_dir = tmp_path / "candidate_events" / "cli"
    assert (export_dir / "candidate_events.csv").exists()
    assert (export_dir / "candidate_event_summary.json").exists()
    assert (export_dir / "candidate_event_summary.md").exists()
    assert "decision_grade=false" in result.output
    assert "cost_grade=none" in result.output


def _write_tiny_parquet(path: Path) -> None:
    _write_parquet(
        path,
        [
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                "open": 1.0,
                "high": 1.2,
                "low": 0.9,
                "close": 1.1,
                "volume": 100,
            },
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
                "open": 1.1,
                "high": 1.4,
                "low": 1.0,
                "close": 1.2,
                "volume": 200,
            },
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 20, 0, tzinfo=timezone.utc),
                "open": 1.2,
                "high": 1.3,
                "low": 1.1,
                "close": 1.2,
                "volume": 300,
            },
            {
                "ticker": "XYZ",
                "timestamp": datetime(2026, 5, 1, 13, 31, tzinfo=timezone.utc),
                "open": 5.0,
                "high": 7.5,
                "low": 4.9,
                "close": 5.2,
                "volume": 400,
            },
            {
                "ticker": "XYZ",
                "timestamp": datetime(2026, 5, 1, 23, 0, tzinfo=timezone.utc),
                "open": 5.2,
                "high": 5.3,
                "low": 5.0,
                "close": 5.1,
                "volume": 500,
            },
        ],
    )


def _write_event_parquet(path: Path) -> None:
    _write_parquet(
        path,
        [
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
                "open": 1.0,
                "high": 1.02,
                "low": 0.99,
                "close": 1.0,
                "volume": 200,
            },
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 13, 45, tzinfo=timezone.utc),
                "open": 1.0,
                "high": 1.12,
                "low": 1.0,
                "close": 1.08,
                "volume": 300,
            },
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc),
                "open": 1.08,
                "high": 1.20,
                "low": 1.05,
                "close": 1.15,
                "volume": 400,
            },
            {
                "ticker": "ABCD",
                "timestamp": datetime(2026, 5, 1, 14, 45, tzinfo=timezone.utc),
                "open": 1.15,
                "high": 1.25,
                "low": 1.10,
                "close": 1.18,
                "volume": 500,
            },
            {
                "ticker": "WEAK",
                "timestamp": datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 2.02,
                "low": 1.99,
                "close": 2.0,
                "volume": 100,
            },
            {
                "ticker": "WEAK",
                "timestamp": datetime(2026, 5, 1, 13, 45, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 2.03,
                "low": 1.98,
                "close": 2.01,
                "volume": 100,
            },
            {
                "ticker": "STALE",
                "timestamp": datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 2.1,
                "low": 1.9,
                "close": 2.0,
                "volume": 1000,
            },
            {
                "ticker": "STALE",
                "timestamp": datetime(2026, 5, 1, 13, 35, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 2.5,
                "low": 2.0,
                "close": 2.4,
                "volume": 1000,
            },
        ],
    )


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    pl = pytest.importorskip("polars")
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)
