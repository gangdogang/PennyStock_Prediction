from __future__ import annotations

from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    create_snapshot_run,
    fetch_latest_watchlist,
    init_database,
    insert_universe_candidates,
)
from penny_stock_radar.models import FilingMatch, SetupSignal, UniverseCandidate
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
