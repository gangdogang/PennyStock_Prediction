from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib

from penny_stock_radar.db import init_database, insert_historical_minute_bars
from penny_stock_radar.models import HistoricalMinuteBar
from penny_stock_radar.services.premkt_model_scoring import PremktModelScorer
from penny_stock_radar.services.premkt_model_training import PremktModelArtifact
from penny_stock_radar.services.replay_bar_cache import ReplayBarSeriesCache
from penny_stock_radar.services.premkt_training_dataset import FEATURE_VERSION

EASTERN = ZoneInfo("America/New_York")


class VolumeProbabilityModel:
    classes_ = [0, 1]

    def predict_proba(self, features):
        probabilities = []
        for _, row in features.iterrows():
            probability = min(float(row["feature_premarket_volume"]) / 10_000.0, 1.0)
            probabilities.append([1.0 - probability, probability])
        return probabilities


def test_premkt_model_scorer_scores_symbols_with_one_bulk_fetch_and_cutoff(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_minute_bars(
        db_path,
        [
            _bar("AAA", "2026-04-18T07:58:00-04:00", 1.0, 1.1, 1000),
            _bar("AAA", "2026-04-18T07:59:00-04:00", 1.1, 1.2, 2000),
            _bar("AAA", "2026-04-18T08:01:00-04:00", 1.2, 9.9, 100_000),
            _bar("BBB", "2026-04-18T07:59:00-04:00", 2.0, 2.1, 5000),
        ],
    )
    model_path = _write_model_artifact(tmp_path)
    scorer = PremktModelScorer(model_path=model_path, database_path=db_path)

    scores = scorer.score_symbols(
        symbols=["AAA", "BBB", "ZZZ"],
        market_date="2026-04-18",
        cutoff_at=datetime(2026, 4, 18, 8, 0, tzinfo=EASTERN),
    )

    assert scores["AAA"].ml_score == 30.0
    assert scores["BBB"].ml_score == 50.0
    assert scores["AAA"].model_feature_version == FEATURE_VERSION
    assert scores["ZZZ"].ml_score is None
    assert any("no historical minute bars before cutoff" in note for note in scores["ZZZ"].scoring_notes)


def test_premkt_model_scorer_can_score_from_replay_bar_cache_without_db_fetch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model_path = _write_model_artifact(tmp_path)
    scorer = PremktModelScorer(model_path=model_path, database_path=tmp_path / "unused.sqlite3")
    cache = ReplayBarSeriesCache.from_rows(
        [
            _raw_row("AAA", "2026-04-18T07:58:00-04:00", 1.0, 1.1, 1000),
            _raw_row("AAA", "2026-04-18T07:59:00-04:00", 1.1, 1.2, 2000),
            _raw_row("AAA", "2026-04-18T08:01:00-04:00", 1.2, 9.9, 100_000),
            _raw_row("BBB", "2026-04-18T07:59:00-04:00", 2.0, 2.1, 5000),
        ]
    )

    def fail_fetch(*args, **kwargs):
        raise AssertionError("score_symbols should use the replay bar cache")

    monkeypatch.setattr(
        "penny_stock_radar.services.premkt_model_scoring.fetch_historical_minute_bars_for_symbols",
        fail_fetch,
    )

    scores = scorer.score_symbols(
        symbols=["AAA", "BBB", "ZZZ"],
        market_date="2026-04-18",
        cutoff_at=datetime(2026, 4, 18, 8, 0, tzinfo=EASTERN),
        bar_cache=cache,
    )

    assert scores["AAA"].ml_score == 30.0
    assert scores["BBB"].ml_score == 50.0
    assert scores["ZZZ"].ml_score is None


def _bar(
    symbol: str,
    timestamp: str,
    open_price: float,
    close_price: float,
    volume: float,
) -> HistoricalMinuteBar:
    bar_at = datetime.fromisoformat(timestamp)
    return HistoricalMinuteBar(
        symbol=symbol,
        market_date=bar_at.date().isoformat(),
        market_phase="premarket",
        timestamp=bar_at,
        open_price=open_price,
        high_price=max(open_price, close_price),
        low_price=min(open_price, close_price),
        close_price=close_price,
        volume=volume,
        source="fixture",
    )


def _raw_row(
    symbol: str,
    timestamp: str,
    open_price: float,
    close_price: float,
    volume: float,
) -> dict[str, object]:
    bar_at = datetime.fromisoformat(timestamp)
    return {
        "symbol": symbol,
        "market_date": bar_at.date().isoformat(),
        "market_phase": "premarket",
        "bar_at": timestamp,
        "open_price": open_price,
        "high_price": max(open_price, close_price),
        "low_price": min(open_price, close_price),
        "close_price": close_price,
        "volume": volume,
        "bid_price": None,
        "ask_price": None,
        "spread_pct": None,
    }


def _write_model_artifact(tmp_path: Path) -> Path:
    model_path = tmp_path / "premkt_model.joblib"
    joblib.dump(
        PremktModelArtifact(
            model=VolumeProbabilityModel(),
            feature_columns=["feature_premarket_volume"],
            target_column="label_winner",
            fill_values={"feature_premarket_volume": 0.0},
            model_type="VolumeProbabilityModel",
            created_at="2026-04-18T12:00:00+00:00",
            notes=[],
            feature_version=FEATURE_VERSION,
        ),
        model_path,
    )
    return model_path
