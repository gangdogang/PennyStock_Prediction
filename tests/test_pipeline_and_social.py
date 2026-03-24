from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_replay_report,
    fetch_latest_social_signals,
    init_database,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import UniverseCandidate, WatchlistEntry
from penny_stock_radar.services.replay_pipeline import ReplayPipeline
from penny_stock_radar.services.social_monitor import SocialMonitor


def test_replay_pipeline_generates_and_analyzes_from_watchlist(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    replay_dir = tmp_path / "replay"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=2)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.1,
                float_shares=5_000_000,
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="BBB",
                company_name="BBB Corp",
                exchange="Q",
                price=0.9,
                float_shares=15_000_000,
                passed_filters=True,
                filter_reasons=[],
            ),
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=3.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=0.5,
                low_float_bonus=0.5,
                reasons=["test"],
            ),
            WatchlistEntry(
                symbol="BBB",
                total_score=2.0,
                catalyst_score=0.0,
                technical_score=1.0,
                sympathy_score=0.5,
                low_float_bonus=0.5,
                reasons=["test"],
            ),
        ],
    )

    settings = AppSettings(db_path=db_path, replay_dir=replay_dir, watchlist_limit=10)
    pipeline = ReplayPipeline(settings)
    replay_path = pipeline.generate_mock_replay()
    payload = pipeline.analyze_replay(replay_path)

    assert replay_path.exists()
    assert payload["premarket_signals"]
    assert payload["session_decisions"]
    assert "report" in payload
    assert fetch_latest_replay_report(db_path) is not None


def test_social_monitor_scores_mentions_and_persists(tmp_path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)

    now = datetime.now(timezone.utc)
    mentions = pd.DataFrame(
        [
            {
                "timestamp": now - timedelta(minutes=20),
                "symbol": "AAA",
                "platform": "reddit",
                "author": "u1",
                "engagement": 10,
            },
            {
                "timestamp": now - timedelta(minutes=10),
                "symbol": "AAA",
                "platform": "x",
                "author": "u2",
                "engagement": 8,
            },
            {
                "timestamp": now,
                "symbol": "AAA",
                "platform": "stocktwits",
                "author": "u3",
                "engagement": 9,
            },
        ]
    )

    signals = SocialMonitor().analyze(mentions)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.symbol == "AAA"
    assert signal.social_score > 0
    assert "cross_platform_sync" in signal.reasons

    from penny_stock_radar.db import insert_social_signals

    insert_social_signals(db_path, snapshot.snapshot_id, signals)
    rows = fetch_latest_social_signals(db_path, limit=5)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
