from __future__ import annotations

from pathlib import Path

import pandas as pd

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_market_activity,
    fetch_latest_premarket_signals,
    fetch_latest_prediction_outcomes,
    fetch_latest_replay_report,
    fetch_latest_scan_id,
    fetch_latest_session_decisions,
    fetch_latest_social_signals,
    fetch_latest_watchlist,
    init_database,
    insert_market_activity,
    insert_premarket_signals,
    insert_prediction_outcomes,
    insert_session_decisions,
    insert_social_signals,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import (
    MarketActivity,
    PremarketSignal,
    PredictionOutcome,
    ReplayEvaluation,
    SessionDecision,
    SocialSignal,
    UniverseCandidate,
    WatchlistEntry,
)
from penny_stock_radar.services.replay_pipeline import ReplayPipeline
from penny_stock_radar.services.report_builder import ReportBuilder


def _seed_scan(
    db_path: Path,
    snapshot_id: str,
    symbol: str,
    float_shares: int,
    watchlist_score: float,
    premarket_quality: float,
    session_decision: str,
    social_score: float,
) -> None:
    insert_universe_candidates(
        db_path,
        snapshot_id,
        [
            UniverseCandidate(
                symbol=symbol,
                company_name=f"{symbol} Corp",
                exchange="Q",
                price=1.25,
                float_shares=float_shares,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot_id,
        [
            WatchlistEntry(
                symbol=symbol,
                total_score=watchlist_score,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=0.5,
                low_float_bonus=0.5,
                reasons=["seed"],
            )
        ],
    )
    insert_premarket_signals(
        db_path,
        snapshot_id,
        [
            PremarketSignal(
                symbol=symbol,
                premarket_high=1.60,
                premarket_low=1.10,
                premarket_close=1.55,
                premarket_vwap=1.30,
                premarket_rvol=4.2,
                dollar_volume=250_000.0,
                trade_count=90,
                tps=0.02,
                size_mean=5000.0,
                size_std=400.0,
                size_cv=0.08,
                spread_pct=0.025,
                round_level_break=True,
                tape_anomaly=True,
                quality_score=premarket_quality,
                reasons=["seed"],
            )
        ],
    )
    insert_session_decisions(
        db_path,
        snapshot_id,
        [
            SessionDecision(
                symbol=symbol,
                decision=session_decision,
                anchored_vwap=1.40,
                opening_range_high=1.58,
                opening_range_low=1.18,
                regular_high=1.70,
                regular_low=1.15,
                regular_close=1.62,
                extension_z=1.2,
                mfe_pct=12.0,
                mae_pct=-4.0,
                reasons=["seed"],
            )
        ],
    )
    insert_social_signals(
        db_path,
        snapshot_id,
        [
            SocialSignal(
                symbol=symbol,
                mention_count=3,
                mention_velocity=8.0,
                unique_authors=3,
                cross_platform_count=2,
                engagement_total=25.0,
                social_score=social_score,
                reasons=["seed"],
            )
        ],
    )


def test_latest_fetch_helpers_track_most_recent_scan_and_report(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    replay_dir = tmp_path / "replay"
    init_database(db_path)

    older = create_snapshot_run(db_path, source="older", symbol_count=1)
    newer = create_snapshot_run(db_path, source="newer", symbol_count=2)

    _seed_scan(db_path, older.snapshot_id, "OLD", 9_000_000, 2.0, 1.0, "AVOID", 1.0)
    _seed_scan(db_path, newer.snapshot_id, "NEW", 4_000_000, 4.0, 5.0, "ENTER", 4.0)

    settings = AppSettings(db_path=db_path, replay_dir=replay_dir, watchlist_limit=10)
    pipeline = ReplayPipeline(settings)

    watchlist_rows = fetch_latest_watchlist(db_path, limit=10)
    assert [row["symbol"] for row in watchlist_rows] == ["NEW"]

    scan_row = fetch_latest_scan_id(db_path)
    assert scan_row is not None
    assert scan_row["scan_id"] == newer.snapshot_id

    replay_path = pipeline.generate_mock_replay(output_path=replay_dir / "auto.csv", symbol_limit=1)
    payload = pipeline.analyze_replay(replay_path)

    premarket_rows = fetch_latest_premarket_signals(db_path, limit=10)
    session_rows = fetch_latest_session_decisions(db_path, limit=10)
    social_rows = fetch_latest_social_signals(db_path, limit=10)
    report_row = fetch_latest_replay_report(db_path)
    latest_report = pipeline.latest_report()

    assert premarket_rows
    assert session_rows
    assert social_rows
    assert report_row is not None
    assert latest_report is not None
    assert latest_report["label"] == report_row["label"]
    assert latest_report["expectancy"] == report_row["expectancy"]
    assert payload["report"]["symbol_count"] == 1
    assert report_row["symbol_count"] == 1
    assert latest_report["label"] in {"continuation", "fade"}
    assert premarket_rows[0]["symbol"] == "NEW"
    assert session_rows[0]["symbol"] == "NEW"
    assert social_rows[0]["symbol"] == "NEW"


def test_replay_report_helper_is_in_sync_with_database_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="report", symbol_count=1)
    _seed_scan(db_path, snapshot.snapshot_id, "REP", 5_000_000, 3.5, 4.0, "ENTER", 2.0)

    settings = AppSettings(db_path=db_path, replay_dir=tmp_path / "replay")
    pipeline = ReplayPipeline(settings)
    replay_path = pipeline.generate_mock_replay(output_path=tmp_path / "replay.csv", symbol_limit=1)
    payload = pipeline.analyze_replay(replay_path)

    report_row = fetch_latest_replay_report(db_path)
    assert report_row is not None
    assert report_row["label"] == payload["report"]["label"]
    assert report_row["precision_at_k"] == payload["report"]["precision_at_k"]
    assert report_row["expectancy"] == payload["report"]["expectancy"]
    assert report_row["symbol_count"] == 1


def test_report_builder_exports_json_and_markdown(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="export", symbol_count=1)
    _seed_scan(db_path, snapshot.snapshot_id, "EXP", 4_000_000, 4.0, 5.0, "ENTER", 3.0)
    insert_market_activity(
        db_path,
        snapshot.snapshot_id,
        "premarket",
        [
            MarketActivity(
                symbol="EXP",
                market_phase="premarket",
                source="fake",
                last_price=1.7,
                previous_close=1.2,
                pct_change=41.7,
                volume=250_000,
                dollar_volume=425_000,
                trade_size=1000,
                spread_pct=0.02,
                pct_rank=1,
                volume_rank=1,
                watchlist_rank=1,
                watchlist_score=4.0,
                predicted=True,
                analysis_label="SURGING",
                analysis_score=4.0,
                reasons=["watchlist_predicted"],
            )
        ],
    )
    insert_prediction_outcomes(
        db_path,
        snapshot.snapshot_id,
        "premarket",
        [
            PredictionOutcome(
                symbol="EXP",
                market_phase="premarket",
                predicted=True,
                watchlist_rank=1,
                watchlist_score=4.0,
                pct_rank=1,
                volume_rank=1,
                pct_change=41.7,
                volume=250_000,
                dollar_volume=425_000,
                analysis_label="SURGING",
                outcome="matched_both",
                reasons=["predicted_watchlist", "top_pct_change"],
            )
        ],
    )

    builder = ReportBuilder()
    json_output = tmp_path / "summary.json"
    markdown_output = tmp_path / "summary.md"
    html_output = tmp_path / "summary.html"

    payload = builder.export_json(db_path, json_output, limit=10)
    markdown = builder.export_markdown(db_path, markdown_output, limit=10)
    html = builder.export_html(db_path, html_output, limit=10)

    assert json_output.exists()
    assert markdown_output.exists()
    assert html_output.exists()
    assert payload["watchlist"]
    assert payload["live_premarket"]
    assert payload["prediction_premarket"]
    assert "EXP" in markdown
    assert "Penny Stock Radar Summary" in markdown
    assert "Premarket Prediction Audit" in markdown
    assert "Standalone Snapshot UI" in html
    assert "Prediction Audit" in html
    assert "EXP" in html
    assert "진입" in html
