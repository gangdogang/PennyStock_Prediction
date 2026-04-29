from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path

import joblib
import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_paper_trading_run,
    fetch_latest_premkt_predictions,
    get_connection,
    init_database,
    insert_filings,
    insert_historical_minute_bars,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import (
    FilingMatch,
    HistoricalMinuteBar,
    PremktPrediction,
    SetupSignal,
    UniverseCandidate,
    WatchlistEntry,
)
from penny_stock_radar.services.premkt_model_training import PremktModelArtifact
from penny_stock_radar.services.premkt_training_dataset import FEATURE_VERSION
from penny_stock_radar.services.filing_scanner import FilingScanner
from penny_stock_radar.services.premkt_predictor import PremktPredictor
from penny_stock_radar.services.watchlist_builder import WatchlistBuilder


class FixedProbabilityModel:
    classes_ = [0, 1]

    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, features):
        return [[1.0 - self.probability, self.probability] for _ in range(len(features))]


class FakeWatchlistBuilder:
    def __init__(
        self,
        db_path: Path,
        scan_id: str,
    ) -> None:
        self.db_path = db_path
        self.scan_id = scan_id
        self.calls: list[tuple[int | None, int | None, object | None, object | None]] = []

    def build(self, limit=None, lookback_hours=None, *, market_date=None, filing_cutoff=None):
        self.calls.append((limit, lookback_hours, market_date, filing_cutoff))
        entries = [
            WatchlistEntry(
                symbol="AAA",
                total_score=6.8,
                catalyst_score=1.25,
                technical_score=2.0,
                sympathy_score=1.0,
                market_context_score=0.5,
                low_float_bonus=1.5,
                social_score=0.0,
                themes=["biotech", "ai"],
                reasons=["low_float", "8-K | merger", "above_20dma"],
            )
        ]
        filings = [
            FilingMatch(
                symbol="AAA",
                cik="0000123456",
                form="8-K",
                filing_date="2026-04-18",
                acceptance_datetime="2026-04-18T11:00:00Z",
                accession_number="0000123456-26-000001",
                filing_url="https://example.test/a",
                matched_keywords=["merger"],
                summary="8-K | merger agreement",
                themes=["biotech"],
            )
        ]
        insert_watchlist(self.db_path, self.scan_id, entries)
        insert_filings(self.db_path, self.scan_id, filings)
        return entries, filings, {}


def test_premkt_predictor_persists_predictions_without_touching_order_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.15,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    settings = AppSettings(db_path=db_path, live_market_provider="disabled")
    predictor = PremktPredictor(
        settings,
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot.snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    with get_connection(db_path) as connection:
        before_orders = connection.execute("SELECT COUNT(*) AS count FROM paper_orders").fetchone()["count"]
    predictions = predictor.run(limit=5)
    rows = fetch_latest_premkt_predictions(db_path, prefer_reportable=False)
    with get_connection(db_path) as connection:
        after_orders = connection.execute("SELECT COUNT(*) AS count FROM paper_orders").fetchone()["count"]

    assert fetch_latest_paper_trading_run(db_path) is None
    assert before_orders == 0
    assert after_orders == 0
    assert len(predictions) == 1
    assert predictions[0].symbol == "AAA"
    assert predictions[0].score == 100.0
    assert predictions[0].max_hold_days == 3
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
    assert float(rows[0]["score"]) == predictions[0].score


def test_premkt_predictor_respects_env_weight_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.15,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    monkeypatch.setenv("PENNY_STOCK_DB_PATH", str(db_path))
    monkeypatch.setenv("PENNY_STOCK_LIVE_MARKET_PROVIDER", "disabled")
    monkeypatch.setenv("PENNY_STOCK_PREDICTOR_WEIGHT_TOTAL_SCORE", "1.0")
    settings = AppSettings()
    predictor = PremktPredictor(
        settings,
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot.snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(limit=5)

    assert len(predictions) == 1
    assert predictions[0].score == 44.3
    assert predictions[0].max_hold_days == 3
    assert predictions[0].rule_score == predictions[0].score
    assert predictions[0].final_score == predictions[0].score
    assert predictions[0].score_mode == "rule"
    assert predictions[0].ml_score is None


def test_premkt_predictor_respects_hold_day_threshold_overrides(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.15,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    settings = AppSettings(
        db_path=db_path,
        live_market_provider="disabled",
        predictor_max_hold_days_tier3_total=7.0,
        predictor_max_hold_days_tier2_total=7.0,
    )
    predictor = PremktPredictor(
        settings,
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot.snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(limit=5)

    assert len(predictions) == 1
    assert predictions[0].max_hold_days == 1


def test_premkt_predictor_ml_mode_uses_model_score(tmp_path: Path) -> None:
    db_path, snapshot_id = _setup_fake_predictor_db(tmp_path)
    _insert_scoring_bars(db_path)
    model_path = _write_model_artifact(
        tmp_path,
        probability=0.2,
        feature_columns=["feature_premarket_volume"],
    )
    predictor = PremktPredictor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(
        limit=5,
        market_date="2026-04-18",
        persist=False,
        model_path=model_path,
        score_mode="ml",
    )

    assert predictions[0].rule_score == 100.0
    assert predictions[0].ml_score == 20.0
    assert predictions[0].score == 20.0
    assert predictions[0].final_score == 20.0
    assert predictions[0].score_mode == "ml"
    assert predictions[0].model_feature_version == FEATURE_VERSION


def test_premkt_predictor_blend_mode_applies_ml_weight(tmp_path: Path) -> None:
    db_path, snapshot_id = _setup_fake_predictor_db(tmp_path)
    _insert_scoring_bars(db_path)
    model_path = _write_model_artifact(
        tmp_path,
        probability=0.2,
        feature_columns=["feature_premarket_volume"],
    )
    predictor = PremktPredictor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(
        limit=5,
        market_date="2026-04-18",
        persist=False,
        model_path=model_path,
        score_mode="blend",
        ml_weight=0.25,
    )

    assert predictions[0].rule_score == 100.0
    assert predictions[0].ml_score == 20.0
    assert predictions[0].score == 80.0
    assert predictions[0].final_score == 80.0
    assert predictions[0].score_mode == "blend"


def test_premkt_predictor_fills_missing_model_features(tmp_path: Path) -> None:
    db_path, snapshot_id = _setup_fake_predictor_db(tmp_path)
    _insert_scoring_bars(db_path)
    model_path = _write_model_artifact(
        tmp_path,
        probability=0.7,
        feature_columns=["feature_premarket_volume", "feature_missing_extra"],
        fill_values={"feature_premarket_volume": 0.0, "feature_missing_extra": 7.0},
    )
    predictor = PremktPredictor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(
        limit=5,
        market_date="2026-04-18",
        persist=False,
        model_path=model_path,
        score_mode="ml",
    )

    assert predictions[0].score == 70.0
    assert predictions[0].model_missing_feature_count == 1
    assert predictions[0].model_missing_features == ["feature_missing_extra"]
    assert any("missing_features_filled" in note for note in predictions[0].scoring_notes)


def test_premkt_predictor_falls_back_to_rule_score_when_model_features_unavailable(
    tmp_path: Path,
) -> None:
    db_path, snapshot_id = _setup_fake_predictor_db(tmp_path)
    model_path = _write_model_artifact(
        tmp_path,
        probability=0.2,
        feature_columns=["feature_premarket_volume"],
    )
    predictor = PremktPredictor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictions = predictor.run(
        limit=5,
        market_date="2026-04-18",
        persist=False,
        model_path=model_path,
        score_mode="ml",
    )

    assert predictions[0].score == 100.0
    assert predictions[0].ml_score is None
    assert predictions[0].score_mode == "rule"
    assert any("no historical minute bars before cutoff" in note for note in predictions[0].scoring_notes)
    assert any("fell back to rule score" in note for note in predictions[0].scoring_notes)


def test_premkt_predictor_json_export_includes_scoring_lineage(tmp_path: Path) -> None:
    db_path, snapshot_id = _setup_fake_predictor_db(tmp_path)
    _insert_scoring_bars(db_path)
    model_path = _write_model_artifact(
        tmp_path,
        probability=0.2,
        feature_columns=["feature_premarket_volume"],
    )
    output_path = tmp_path / "premkt_predictions_ml.json"
    predictor = PremktPredictor(
        AppSettings(db_path=db_path, live_market_provider="disabled"),
        watchlist_builder=FakeWatchlistBuilder(db_path, snapshot_id),
        now_fn=lambda: datetime(2026, 4, 18, 11, 45, tzinfo=timezone.utc),
    )

    predictor.run(
        limit=5,
        market_date="2026-04-18",
        persist=False,
        output_path=output_path,
        model_path=model_path,
        score_mode="blend",
        ml_weight=0.5,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    row = payload[0]
    assert row["rule_score"] == 100.0
    assert row["ml_score"] == 20.0
    assert row["final_score"] == 60.0
    assert row["score"] == 60.0
    assert row["score_mode"] == "blend"
    assert row["model_path"] == str(model_path)
    assert row["model_feature_version"] == FEATURE_VERSION
    assert row["model_missing_feature_count"] == 0
    assert row["scoring_notes"] == []


class CutoffProvider:
    def fetch_company_tickers(self):
        return {"ABCD": {"cik": "0000123456", "title": "ABCD Corp"}}

    def fetch_submissions(self, cik: str):
        return {
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K"],
                    "filingDate": ["2026-04-16", "2026-04-16"],
                    "acceptanceDateTime": [
                        "2026-04-16T11:30:00Z",
                        "2026-04-16T12:30:00Z",
                    ],
                    "accessionNumber": ["0000123456-26-000001", "0000123456-26-000002"],
                    "primaryDocument": ["early.htm", "late.htm"],
                    "primaryDocDescription": [
                        "Letter of intent and merger agreement",
                        "Reverse stock split registration statement",
                    ],
                }
            }
        }

    def build_filing_url(self, cik: str, accession_number: str, primary_document: str | None):
        return f"https://example.test/{cik}/{accession_number}/{primary_document}"

    def fetch_filing_text(self, filing_url: str) -> str:
        return ""


class FakeSetupScanner:
    def scan_symbols(self, symbols):
        return {
            symbol: SetupSignal(
                symbol=symbol,
                above_sma20=True,
                volatility_contraction=True,
                setup_score=2.0,
                notes=["above_20dma", "volatility_contraction"],
            )
            for symbol in symbols
        }


def test_premkt_predictor_excludes_filings_after_cutoff(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="historical",
        symbol_count=1,
        market_date="2026-04-16",
        snapshot_role="point_in_time",
        point_in_time_tag="fixture",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="ABCD",
                company_name="ABCD Corp",
                exchange="Q",
                price=1.05,
                sector="Biotech",
                float_shares=5_000_000,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )

    settings = AppSettings(
        db_path=db_path,
        filings_lookback_hours=48,
        watchlist_limit=10,
        live_market_provider="disabled",
    )
    builder = WatchlistBuilder(
        settings,
        filing_scanner=FilingScanner(settings, provider=CutoffProvider()),
        setup_scanner=FakeSetupScanner(),
    )
    predictor = PremktPredictor(settings, watchlist_builder=builder)

    predictions = predictor.run(market_date="2026-04-16")
    rows = fetch_latest_premkt_predictions(db_path, prefer_reportable=False)

    assert len(predictions) == 1
    assert "letter of intent" in predictions[0].filing_summary.lower()
    assert "reverse stock split" not in predictions[0].filing_summary.lower()
    assert len(rows) == 1
    assert "reverse stock split" not in str(rows[0]["filing_summary"]).lower()


def _setup_fake_predictor_db(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(
        db_path,
        source="test",
        symbol_count=1,
        market_date="2026-04-18",
    )
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.15,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    return db_path, snapshot.snapshot_id


def _insert_scoring_bars(db_path: Path) -> None:
    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-18",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 18, 7, 58, tzinfo=UTC),
                open_price=1.0,
                high_price=1.15,
                low_price=0.95,
                close_price=1.1,
                volume=1000,
                source="fixture",
            ),
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-18",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 18, 7, 59, tzinfo=UTC),
                open_price=1.1,
                high_price=1.25,
                low_price=1.05,
                close_price=1.2,
                volume=2000,
                source="fixture",
            ),
        ],
    )


def _write_model_artifact(
    tmp_path: Path,
    *,
    probability: float,
    feature_columns: list[str],
    fill_values: dict[str, float] | None = None,
) -> Path:
    model_path = tmp_path / "premkt_model.joblib"
    joblib.dump(
        PremktModelArtifact(
            model=FixedProbabilityModel(probability),
            feature_columns=feature_columns,
            target_column="label_winner",
            fill_values=fill_values or {column: 0.0 for column in feature_columns},
            model_type="FixedProbabilityModel",
            created_at="2026-04-18T12:00:00+00:00",
            notes=[],
            feature_version=FEATURE_VERSION,
        ),
        model_path,
    )
    return model_path
