from __future__ import annotations

from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_watchlist,
    init_database,
    insert_market_activity,
    insert_universe_candidates,
)
from penny_stock_radar.models import (
    FilingMatch,
    ListingRecord,
    MarketActivity,
    MetadataRecord,
    SetupSignal,
    UniverseCandidate,
)
from penny_stock_radar.providers.live_market import LiveMover
from penny_stock_radar.services.watchlist_builder import WatchlistBuilder


class FakeFilingScanner:
    def scan_symbols(self, symbols, lookback_hours=None):
        assert lookback_hours == 48
        return [
            FilingMatch(
                symbol="ABCD",
                cik="0000123456",
                form="8-K",
                filing_date="2026-03-24",
                accession_number="0000123456-26-000001",
                filing_url="https://example.test/abdc8k.htm",
                summary="8-K | merger agreement | keywords=merger",
                matched_keywords=["merger"],
            )
        ]


class FakeSetupScanner:
    def scan_symbols(self, symbols):
        return {
            "ABCD": SetupSignal(
                symbol="ABCD",
                above_sma20=True,
                volatility_contraction=True,
                setup_score=2.0,
                notes=["above_20dma", "volatility_contraction"],
            ),
            "WXYZ": SetupSignal(
                symbol="WXYZ",
                above_sma20=False,
                volatility_contraction=False,
                setup_score=0.0,
                notes=[],
            ),
        }


class FakeListingProvider:
    def __init__(self, records: list[ListingRecord]) -> None:
        self._records = records

    def fetch(self, limit=None):
        return list(self._records if limit is None else self._records[:limit])


class FakeMetadataProvider:
    def __init__(self, rows: dict[str, MetadataRecord]) -> None:
        self._rows = rows

    def fetch_metadata(self, symbols, max_workers, prices=None):
        prices = prices or {}
        result: dict[str, MetadataRecord] = {}
        for symbol in symbols:
            row = self._rows.get(symbol)
            if row is not None:
                result[symbol] = row.model_copy(
                    update={"price": row.price if row.price is not None else prices.get(symbol)}
                )
                continue
            result[symbol] = MetadataRecord(
                symbol=symbol,
                price=prices.get(symbol),
                market_cap=80_000_000,
                float_shares=8_000_000,
                quote_type="EQUITY",
            )
        return result


def test_watchlist_builder_ranks_and_persists_entries(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=2)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="ABCD",
                company_name="ABCD Corp",
                exchange="Q",
                price=1.2,
                market_cap=80_000_000,
                float_shares=6_000_000,
                sector="Biotech",
                passed_filters=True,
                filter_reasons=[],
            ),
            UniverseCandidate(
                symbol="WXYZ",
                company_name="WXYZ Corp",
                exchange="Q",
                price=1.6,
                market_cap=90_000_000,
                float_shares=18_000_000,
                sector="Biotech",
                passed_filters=True,
                filter_reasons=[],
            ),
        ],
    )

    settings = AppSettings(
        db_path=db_path,
        filings_lookback_hours=48,
        watchlist_limit=10,
    )
    builder = WatchlistBuilder(
        settings,
        filing_scanner=FakeFilingScanner(),
        setup_scanner=FakeSetupScanner(),
    )

    entries, filings, signals = builder.build(limit=10, lookback_hours=48)

    assert len(entries) == 1
    assert entries[0].symbol == "ABCD"
    assert entries[0].total_score > 0
    assert len(filings) == 1
    assert "ABCD" in signals

    persisted = fetch_latest_watchlist(db_path, limit=10)
    assert len(persisted) == 1
    assert persisted[0]["symbol"] == "ABCD"


def test_watchlist_builder_adds_live_market_context_symbols(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    snapshot = create_snapshot_run(db_path, source="test", symbol_count=1)
    insert_universe_candidates(
        db_path,
        snapshot.snapshot_id,
        [
            UniverseCandidate(
                symbol="ABCD",
                company_name="ABCD Corp",
                exchange="Q",
                price=1.2,
                market_cap=80_000_000,
                float_shares=6_000_000,
                sector="Biotech",
                passed_filters=True,
                filter_reasons=[],
            ),
        ],
    )

    class FakeLiveProvider:
        def is_available(self) -> bool:
            return True

        def top_gainers(self, limit: int = 20):
            return [
                LiveMover(symbol="LIVE", source="fake", price=1.8, pct_change=58.0, rank=1),
            ]

        def most_active(self, limit: int = 20):
            return [
                LiveMover(symbol="LIVE", source="fake", volume=450_000, trade_count=1_200, rank=2),
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.build_live_market_provider",
        lambda settings: FakeLiveProvider(),
    )

    settings = AppSettings(
        db_path=db_path,
        filings_lookback_hours=48,
        watchlist_limit=10,
    )
    builder = WatchlistBuilder(
        settings,
        filing_scanner=FakeFilingScanner(),
        setup_scanner=FakeSetupScanner(),
        listing_provider=FakeListingProvider(
            [ListingRecord(symbol="LIVE", company_name="Live Runner Inc", exchange="Q")]
        ),
        metadata_provider=FakeMetadataProvider(
            {
                "LIVE": MetadataRecord(
                    symbol="LIVE",
                    price=1.8,
                    market_cap=60_000_000,
                    float_shares=8_000_000,
                    sector="Biotech",
                    industry="Diagnostics",
                    quote_type="EQUITY",
                )
            }
        ),
    )

    entries, _, _ = builder.build(limit=10, lookback_hours=48)

    live_entry = next(entry for entry in entries if entry.symbol == "LIVE")
    assert live_entry.market_context_score > 0
    assert "live_pct_leader" in live_entry.reasons
    assert "live_volume_leader" in live_entry.reasons

    persisted = fetch_latest_watchlist(db_path, limit=10)
    persisted_live = next(row for row in persisted if row["symbol"] == "LIVE")
    assert persisted_live["market_context_score"] > 0


def test_watchlist_builder_reuses_recent_market_leaders_when_live_feed_is_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    older = create_snapshot_run(db_path, source="older", symbol_count=1)
    newer = create_snapshot_run(db_path, source="newer", symbol_count=1)
    insert_market_activity(
        db_path,
        older.snapshot_id,
        "regular",
        [
            MarketActivity(
                symbol="MOVR",
                market_phase="regular",
                source="fake",
                last_price=1.9,
                previous_close=1.0,
                pct_change=90.0,
                volume=320_000,
                dollar_volume=608_000,
                pct_rank=1,
                volume_rank=2,
                predicted=False,
                analysis_label="CONDITIONAL_ENTRY",
                analysis_score=4.2,
                reasons=["seed"],
            )
        ],
    )
    insert_universe_candidates(
        db_path,
        newer.snapshot_id,
        [
            UniverseCandidate(
                symbol="ABCD",
                company_name="ABCD Corp",
                exchange="Q",
                price=1.2,
                market_cap=80_000_000,
                float_shares=6_000_000,
                sector="Biotech",
                passed_filters=True,
                filter_reasons=[],
            ),
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
        filing_scanner=FakeFilingScanner(),
        setup_scanner=FakeSetupScanner(),
        listing_provider=FakeListingProvider(
            [ListingRecord(symbol="MOVR", company_name="Mover Labs", exchange="Q")]
        ),
        metadata_provider=FakeMetadataProvider(
            {
                "MOVR": MetadataRecord(
                    symbol="MOVR",
                    price=1.9,
                    market_cap=75_000_000,
                    float_shares=7_500_000,
                    sector="Biotech",
                    industry="Diagnostics",
                    quote_type="EQUITY",
                )
            }
        ),
    )

    entries, _, _ = builder.build(limit=10, lookback_hours=48)

    movr_entry = next(entry for entry in entries if entry.symbol == "MOVR")
    assert movr_entry.market_context_score > 0
    assert "recent_pct_leader" in movr_entry.reasons
    assert "recent_trade_quality" in movr_entry.reasons
