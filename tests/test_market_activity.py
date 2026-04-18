from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_market_activity,
    fetch_latest_prediction_outcomes,
    init_database,
    insert_universe_candidates,
    insert_watchlist,
)
from penny_stock_radar.models import UniverseCandidate, WatchlistEntry
from penny_stock_radar.providers.live_market import LiveQuote, LiveSnapshot, LiveTrade
from penny_stock_radar.services.market_activity import MarketActivityScanner


class _FakeProvider:
    source_name = "fake"

    def __init__(
        self,
        snapshots: dict[str, LiveSnapshot],
        gainers: list[dict[str, object]] | None = None,
        actives: list[dict[str, object]] | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._gainers = gainers or []
        self._actives = actives or []

    def is_available(self) -> bool:
        return True

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        return self._snapshots.get(symbol)

    def top_gainers(self, limit: int = 20):
        from penny_stock_radar.providers.live_market import LiveMover

        return [
            LiveMover(
                symbol=str(row["symbol"]),
                source="fake",
                price=float(row["price"]) if row.get("price") is not None else None,
                pct_change=float(row["pct_change"]) if row.get("pct_change") is not None else None,
                rank=index + 1,
            )
            for index, row in enumerate(self._gainers[:limit])
        ]

    def most_active(self, limit: int = 20):
        from penny_stock_radar.providers.live_market import LiveMover

        return [
            LiveMover(
                symbol=str(row["symbol"]),
                source="fake",
                volume=float(row["volume"]) if row.get("volume") is not None else None,
                trade_count=int(row["trade_count"]) if row.get("trade_count") is not None else None,
                rank=index + 1,
            )
            for index, row in enumerate(self._actives[:limit])
        ]


def _snapshot(
    symbol: str,
    price: float,
    prev_close: float,
    volume: int,
    *,
    bid_price: float | None = None,
    ask_price: float | None = None,
    trade_timestamp: datetime | None = None,
    quote_timestamp: datetime | None = None,
    market_status: str | None = "open",
) -> LiveSnapshot:
    trade_timestamp = trade_timestamp or datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    quote_timestamp = quote_timestamp or datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    return LiveSnapshot(
        symbol=symbol,
        source="fake",
        latest_trade=LiveTrade(
            symbol=symbol,
            price=price,
            size=1000,
            timestamp=trade_timestamp,
            source="fake",
        ),
        latest_quote=LiveQuote(
            symbol=symbol,
            bid_price=bid_price if bid_price is not None else price - 0.01,
            ask_price=ask_price if ask_price is not None else price + 0.01,
            bid_size=100,
            ask_size=100,
            timestamp=quote_timestamp,
            source="fake",
        ),
        market_status=market_status,
        updated_at=trade_timestamp,
        raw={
            "ticker": {
                "prevDay": {"c": prev_close},
                "day": {"c": price, "v": volume},
            }
        },
    )


def test_market_activity_scan_persists_rankings_and_prediction_csv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=3)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="AAA",
                company_name="AAA Corp",
                exchange="Q",
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="BBB",
                company_name="BBB Corp",
                exchange="Q",
                price=1.2,
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="CCC",
                company_name="CCC Corp",
                exchange="Q",
                price=1.3,
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
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            ),
            WatchlistEntry(
                symbol="BBB",
                total_score=2.0,
                catalyst_score=0.5,
                technical_score=0.5,
                sympathy_score=0.5,
                low_float_bonus=0.5,
                reasons=["seed"],
            ),
        ],
    )

    snapshots = {
        "AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=300_000),
        "BBB": _snapshot("BBB", price=1.05, prev_close=1.00, volume=10_000),
        "CCC": _snapshot("CCC", price=1.50, prev_close=1.00, volume=500_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(snapshots),
    )

    settings = AppSettings(db_path=db_path)
    scanner = MarketActivityScanner(settings)
    output_csv = tmp_path / "premarket_prediction_vs_actual.csv"

    result = scanner.scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
        comparison_csv=output_csv,
    )

    assert result.market_phase == "premarket"
    assert output_csv.exists()

    activity_rows = fetch_latest_market_activity(
        db_path,
        "premarket",
        limit=10,
        prefer_reportable=False,
    )
    outcome_rows = fetch_latest_prediction_outcomes(
        db_path,
        "premarket",
        limit=10,
        prefer_reportable=False,
    )

    assert [row["symbol"] for row in activity_rows][:2] == ["CCC", "AAA"]
    activity_by_symbol = {row["symbol"]: row for row in activity_rows}
    outcomes_by_symbol = {row["symbol"]: row for row in outcome_rows}
    assert activity_by_symbol["AAA"]["bid_price"] == 1.39
    assert activity_by_symbol["AAA"]["ask_price"] == 1.41
    assert activity_by_symbol["AAA"]["has_live_trade"] == 1
    assert activity_by_symbol["AAA"]["has_live_quote"] == 1
    assert activity_by_symbol["AAA"]["data_age_seconds"] is not None
    assert outcomes_by_symbol["AAA"]["outcome"] == "matched_both"
    assert outcomes_by_symbol["BBB"]["outcome"] == "predicted_only"
    assert outcomes_by_symbol["CCC"]["outcome"] == "unpredicted_leader"

    csv_text = output_csv.read_text(encoding="utf-8")
    assert "matched_both" in csv_text
    assert "unpredicted_leader" in csv_text


def test_market_activity_scan_emits_observability_metrics_jsonl(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            {"AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=300_000)}
        ),
    )

    metrics_path = tmp_path / "live_metrics.jsonl"
    settings = AppSettings(
        db_path=db_path,
        use_mock_market_data=False,
        live_market_provider="kis",
        live_observability_enabled=True,
        live_observability_path=metrics_path,
    )
    scanner = MarketActivityScanner(settings)

    scanner.scan(
        phase="premarket",
        scan_limit=10,
        top_limit=1,
    )

    records = [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
    ]
    quote_records = [record for record in records if record["metric_type"] == "quote_observation"]
    scan_records = [record for record in records if record["metric_type"] == "scan_summary"]

    assert len(quote_records) == 1
    assert quote_records[0]["symbol"] == "AAA"
    assert quote_records[0]["spread_pct"] is not None
    assert quote_records[0]["data_age_seconds"] is not None
    assert quote_records[0]["market_phase"] == "premarket"

    assert len(scan_records) == 1
    assert scan_records[0]["provider"] == "fake"
    assert scan_records[0]["scanned_symbol_count"] == 1
    assert scan_records[0]["decision_counts"]


def test_market_activity_invalid_quote_is_not_treated_as_live_quote(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            {
                "AAA": _snapshot(
                    "AAA",
                    price=1.40,
                    prev_close=1.00,
                    volume=300_000,
                    bid_price=2.00,
                    ask_price=1.00,
                )
            }
        ),
    )

    result = MarketActivityScanner(AppSettings(db_path=db_path)).scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
    )

    row = next(item for item in result.activity if item.symbol == "AAA")
    assert row.has_live_quote is False
    assert row.spread_pct is None


def test_market_activity_uses_oldest_valid_component_timestamp_and_skips_halted_symbols(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
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
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="BBB",
                company_name="BBB Corp",
                exchange="Q",
                price=1.2,
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
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            ),
            WatchlistEntry(
                symbol="BBB",
                total_score=4.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.0,
                reasons=["seed"],
            ),
        ],
    )
    stale_quote_at = datetime(2026, 3, 24, 11, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            {
                "AAA": _snapshot(
                    "AAA",
                    price=1.40,
                    prev_close=1.00,
                    volume=300_000,
                    trade_timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                    quote_timestamp=stale_quote_at,
                ),
                "BBB": _snapshot(
                    "BBB",
                    price=1.50,
                    prev_close=1.00,
                    volume=400_000,
                    market_status="LULD Pause",
                ),
            }
        ),
    )

    result = MarketActivityScanner(AppSettings(db_path=db_path)).scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
    )

    by_symbol = {row.symbol: row for row in result.activity}
    assert by_symbol["AAA"].market_data_at == stale_quote_at
    assert "BBB" not in by_symbol


def test_market_activity_scan_includes_extra_symbols_for_open_positions(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            {
                "AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=300_000),
                "HOLD": _snapshot("HOLD", price=1.15, prev_close=1.00, volume=180_000),
            }
        ),
    )

    result = MarketActivityScanner(AppSettings(db_path=db_path)).scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
        extra_symbols=("HOLD",),
    )

    assert result.scanned_symbol_count == 2
    assert {row.symbol for row in result.activity} == {"AAA", "HOLD"}


def test_market_activity_keeps_halted_extra_symbol_visible_for_open_position_handling(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            {
                "AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=300_000),
                "HOLD": _snapshot(
                    "HOLD",
                    price=1.15,
                    prev_close=1.00,
                    volume=180_000,
                    market_status="LULD Pause",
                ),
            }
        ),
    )

    result = MarketActivityScanner(AppSettings(db_path=db_path)).scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
        extra_symbols=("HOLD",),
    )

    hold = next(row for row in result.activity if row.symbol == "HOLD")
    assert hold.analysis_label == "SKIP"
    assert hold.market_status == "LULD Pause"


def test_market_activity_respects_watchlist_limit_for_predicted_symbols(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
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
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="BBB",
                company_name="BBB Corp",
                exchange="Q",
                price=1.2,
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
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            ),
            WatchlistEntry(
                symbol="BBB",
                total_score=4.0,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.0,
                reasons=["seed"],
            ),
        ],
    )

    snapshots = {
        "AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=300_000),
        "BBB": _snapshot("BBB", price=1.35, prev_close=1.00, volume=280_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(snapshots),
    )

    settings = AppSettings(db_path=db_path, watchlist_limit=1)
    scanner = MarketActivityScanner(settings)
    result = scanner.scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
    )

    by_symbol = {row.symbol: row for row in result.activity}
    predicted_symbols = [row.symbol for row in result.outcomes if row.predicted]

    assert by_symbol["AAA"].predicted is True
    assert by_symbol["BBB"].predicted is False
    assert predicted_symbols == ["AAA"]


def test_market_activity_prefers_provider_screeners_over_tiny_local_universe(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )

    snapshots = {
        "AAA": _snapshot("AAA", price=1.40, prev_close=1.00, volume=100_000),
        "BBB": _snapshot("BBB", price=2.20, prev_close=1.00, volume=400_000),
        "CCC": _snapshot("CCC", price=0.80, prev_close=1.00, volume=900_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            snapshots,
            gainers=[
                {"symbol": "BBB", "price": 2.2, "pct_change": 120.0},
                {"symbol": "AAA", "price": 1.4, "pct_change": 40.0},
            ],
            actives=[
                {"symbol": "CCC", "volume": 900_000, "trade_count": 5000},
                {"symbol": "BBB", "volume": 400_000, "trade_count": 3000},
            ],
        ),
    )

    settings = AppSettings(db_path=db_path)
    scanner = MarketActivityScanner(settings)
    result = scanner.scan(
        phase="premarket",
        scan_limit=10,
        top_limit=2,
    )

    assert result.scanned_symbol_count == 3
    assert [row.symbol for row in sorted(result.activity, key=lambda row: row.pct_rank)][:2] == [
        "BBB",
        "AAA",
    ]
    by_symbol = {row.symbol: row for row in result.activity}
    assert by_symbol["BBB"].analysis_label == "NEWS_CHECK_FIRST"
    assert by_symbol["AAA"].analysis_label == "OPENING_RANGE_CANDIDATE"
    assert [row.symbol for row in sorted(result.activity, key=lambda row: row.volume_rank)][:2] == [
        "CCC",
        "BBB",
    ]


def test_market_activity_all_scope_keeps_high_price_momentum_leaders(
    monkeypatch,
    tmp_path: Path,
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
                price=1.1,
                passed_filters=True,
                filter_reasons=[],
            )
        ],
    )
    insert_watchlist(
        db_path,
        snapshot.snapshot_id,
        [
            WatchlistEntry(
                symbol="AAA",
                total_score=4.5,
                catalyst_score=1.0,
                technical_score=1.0,
                sympathy_score=1.0,
                low_float_bonus=1.5,
                reasons=["seed"],
            )
        ],
    )

    snapshots = {
        "MEGA": _snapshot("MEGA", price=18.40, prev_close=12.00, volume=3_200_000),
        "AAA": _snapshot("AAA", price=1.35, prev_close=1.00, volume=220_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(
            snapshots,
            gainers=[
                {"symbol": "MEGA", "price": 18.4, "pct_change": 53.3},
                {"symbol": "AAA", "price": 1.35, "pct_change": 35.0},
            ],
            actives=[
                {"symbol": "MEGA", "volume": 3_200_000, "trade_count": 8_000},
                {"symbol": "AAA", "volume": 220_000, "trade_count": 900},
            ],
        ),
    )

    settings = AppSettings(db_path=db_path, market_scope="all")
    scanner = MarketActivityScanner(settings)
    result = scanner.scan(phase="premarket", scan_limit=10, top_limit=2)

    by_symbol = {row.symbol: row for row in result.activity}
    assert "MEGA" in by_symbol
    assert by_symbol["MEGA"].pct_rank == 1
    assert by_symbol["MEGA"].analysis_label == "NEWS_CHECK_FIRST"


def test_market_activity_scores_top5_reclaim_candidate(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=6)
    universe_rows = [
        UniverseCandidate(
            symbol=symbol,
            company_name=f"{symbol} Corp",
            exchange="Q",
            price=2.0,
            passed_filters=True,
            filter_reasons=[],
        )
        for symbol in ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    ]
    insert_universe_candidates(db_path, snapshot.snapshot_id, universe_rows)
    insert_watchlist(db_path, snapshot.snapshot_id, [])

    settings = AppSettings(db_path=db_path, market_scope="all")
    scanner = MarketActivityScanner(settings)

    first_snapshots = {
        "AAA": _snapshot("AAA", price=3.0, prev_close=1.0, volume=900_000),
        "BBB": _snapshot("BBB", price=2.8, prev_close=1.0, volume=850_000),
        "CCC": _snapshot("CCC", price=2.6, prev_close=1.0, volume=800_000),
        "DDD": _snapshot("DDD", price=1.82, prev_close=1.7, volume=220_000),
        "EEE": _snapshot("EEE", price=2.3, prev_close=1.0, volume=700_000),
        "FFF": _snapshot("FFF", price=2.1, prev_close=1.0, volume=650_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(first_snapshots),
    )
    scanner.scan(phase="premarket", scan_limit=10, top_limit=5)

    second_snapshots = {
        "AAA": _snapshot("AAA", price=3.1, prev_close=1.0, volume=950_000),
        "BBB": _snapshot("BBB", price=2.9, prev_close=1.0, volume=900_000),
        "CCC": _snapshot("CCC", price=2.7, prev_close=1.0, volume=840_000),
        "DDD": _snapshot("DDD", price=2.45, prev_close=1.7, volume=260_000),
        "EEE": _snapshot("EEE", price=2.35, prev_close=1.0, volume=710_000),
        "FFF": _snapshot("FFF", price=1.40, prev_close=1.0, volume=600_000),
    }
    monkeypatch.setattr(
        "penny_stock_radar.services.market_activity.build_live_market_provider",
        lambda settings: _FakeProvider(second_snapshots),
    )
    result = scanner.scan(phase="premarket", scan_limit=10, top_limit=5)

    by_symbol = {row.symbol: row for row in result.activity}
    ddd = by_symbol["DDD"]
    assert ddd.pct_rank <= 5
    assert ddd.leader_persistence_score > 0
    assert ddd.behavioral_score > 0
    assert "top5_live_leader" in ddd.reasons
    assert ddd.analysis_label in {"WAIT_PULLBACK", "OPENING_RANGE_CANDIDATE", "NEWS_CHECK_FIRST"}
