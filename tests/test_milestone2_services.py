from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest


def import_or_skip(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        pytest.skip(f"Skipping Milestone 2 service test because {module_name} could not be imported: {exc}")


def test_filing_scanner_emits_match_from_recent_submissions_payload() -> None:
    filing_module = import_or_skip("penny_stock_radar.services.filing_scanner")
    models_module = import_or_skip("penny_stock_radar.models")

    FilingScanner = filing_module.FilingScanner
    FilingMatch = models_module.FilingMatch
    now = datetime.now(timezone.utc)

    class FakeSecProvider:
        def __init__(self) -> None:
            self.filing_text_requests: list[str] = []

        def fetch_company_tickers(self) -> dict[str, dict[str, str]]:
            return {"AAA": {"cik": "0001234567", "title": "Alpha Radar Inc."}}

        def fetch_submissions(self, cik: str) -> dict[str, object]:
            return {
                "filings": {
                    "recent": {
                        "form": ["8-K", "S-1"],
                        "filingDate": [(now - timedelta(hours=1)).date().isoformat(), (now - timedelta(days=10)).date().isoformat()],
                        "acceptanceDateTime": [
                            (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                            (now - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
                        ],
                        "accessionNumber": ["0001234567-26-000001", "0001234567-26-000002"],
                        "primaryDocument": ["alpha8k.htm", "alphaS1.htm"],
                        "primaryDocDescription": ["", "registration statement"],
                    }
                }
            }

        def build_filing_url(self, cik: str, accession_number: str, primary_document: str | None) -> str:
            return f"https://example.test/{cik}/{accession_number}/{primary_document or 'index.html'}"

        def fetch_filing_text(self, filing_url: str) -> str:
            self.filing_text_requests.append(filing_url)
            return "<html><body>reverse stock split and minimum bid compliance</body></html>"

    settings = SimpleNamespace(
        sec_company_tickers_url="https://example.test/company_tickers.json",
        sec_submissions_url_template="https://example.test/submissions/{cik}.json",
        sec_archive_filing_url_template="https://example.test/{cik_number}/{accession_no}/{document}",
        sec_user_agent="Test Agent",
        filings_lookback_hours=48,
    )
    scanner = FilingScanner(settings=settings, provider=FakeSecProvider())

    matches = scanner.scan_symbols(["AAA"], lookback_hours=48)

    assert len(matches) == 1
    match = matches[0]
    assert isinstance(match, FilingMatch)
    assert match.symbol == "AAA"
    assert match.form == "8-K"
    assert "reverse stock split" in match.matched_keywords
    assert "compliance" in match.matched_keywords
    assert "8-K" in match.summary
    assert scanner.provider.filing_text_requests, "Expected filing text lookup for keyword discovery."


def test_setup_scanner_marks_above_20dma_and_volatility_contraction(monkeypatch) -> None:
    setup_module = import_or_skip("penny_stock_radar.services.setup_scanner")
    SetupScanner = setup_module.SetupScanner
    SetupSignal = import_or_skip("penny_stock_radar.models").SetupSignal

    dates = pd.date_range("2025-01-01", periods=40, freq="D")
    rows = []
    for idx, _date in enumerate(dates):
        if idx < 20:
            base = 10.0 + idx * 0.25
            high_offset = 5.0
            low_offset = 5.0
            close = base + 4.0
        else:
            base = 18.0 + (idx - 20) * 0.20
            high_offset = 1.0
            low_offset = 1.0
            close = base + 0.6
        rows.append(
            {
                "Open": base,
                "High": base + high_offset,
                "Low": base - low_offset,
                "Close": close,
                "Volume": 1_000_000,
            }
        )

    frame = pd.DataFrame(rows, index=dates)

    def fake_download(*args, **kwargs):
        assert kwargs["tickers"] == ["SYNTH"]
        return frame

    monkeypatch.setattr("penny_stock_radar.services.setup_scanner.yf.download", fake_download)

    scanner = SetupScanner()
    signals = scanner.scan_symbols(["SYNTH"])

    assert "SYNTH" in signals
    signal = signals["SYNTH"]
    assert isinstance(signal, SetupSignal)
    assert signal.above_sma20 is True
    assert signal.volatility_contraction is True
    assert signal.setup_score >= 2.0
    assert "above_20dma" in signal.notes
    assert "volatility_contraction" in signal.notes


def test_watchlist_builder_ranks_combined_signals_without_network(monkeypatch, tmp_path) -> None:
    watchlist_module = import_or_skip("penny_stock_radar.services.watchlist_builder")
    models_module = import_or_skip("penny_stock_radar.models")
    db_module = import_or_skip("penny_stock_radar.db")

    WatchlistBuilder = watchlist_module.WatchlistBuilder
    FilingMatch = models_module.FilingMatch
    SetupSignal = models_module.SetupSignal
    init_database = db_module.init_database

    universe_rows = [
        {
            "scan_id": "scan-123",
            "symbol": "AAA",
            "sector": "Biotech",
            "float_shares": 4_000_000,
        },
        {
            "scan_id": "scan-123",
            "symbol": "BBB",
            "sector": "Biotech",
            "float_shares": 18_000_000,
        },
        {
            "scan_id": "scan-123",
            "symbol": "CCC",
            "sector": "Energy",
            "float_shares": 3_000_000,
        },
    ]
    filings = [
        FilingMatch(
            symbol="AAA",
            cik="0001234567",
            form="8-K",
            filing_date="2026-03-24",
            acceptance_datetime="2026-03-24T04:00:00Z",
            accession_number="0001234567-26-000001",
            filing_url="https://example.test/AAA",
            matched_keywords=["reverse stock split"],
            summary="8-K | reverse stock split",
        ),
        FilingMatch(
            symbol="CCC",
            cik="0001234568",
            form="RW",
            filing_date="2026-03-24",
            acceptance_datetime="2026-03-24T05:00:00Z",
            accession_number="0001234568-26-000001",
            filing_url="https://example.test/CCC",
            matched_keywords=["withdrawal"],
            summary="RW | withdrawal",
        ),
    ]
    setup_signals = {
        "AAA": SetupSignal(
            symbol="AAA",
            above_sma20=True,
            volatility_contraction=True,
            setup_score=2.0,
            notes=["above_20dma", "volatility_contraction"],
        ),
        "BBB": SetupSignal(
            symbol="BBB",
            above_sma20=True,
            volatility_contraction=False,
            setup_score=1.0,
            notes=["above_20dma"],
        ),
        "CCC": SetupSignal(
            symbol="CCC",
            above_sma20=False,
            volatility_contraction=True,
            setup_score=1.0,
            notes=["volatility_contraction"],
        ),
    }

    captured = {"insert_filings": None, "summaries": None, "watchlist": None}

    init_database(tmp_path / "penny_stock.sqlite3")

    class FakeFilingScanner:
        def scan_symbols(self, symbols, lookback_hours=None):
            assert symbols == ["AAA", "BBB", "CCC"]
            assert lookback_hours == 48
            return filings

    class FakeSetupScanner:
        def scan_symbols(self, symbols):
            assert symbols == ["AAA", "BBB", "CCC"]
            return setup_signals

    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.fetch_latest_passed_universe",
        lambda database_path: universe_rows,
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.insert_filings",
        lambda database_path, scan_id, filing_rows: captured.__setitem__("insert_filings", (database_path, scan_id, list(filing_rows))),
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.update_universe_filing_summaries",
        lambda database_path, scan_id, summaries: captured.__setitem__("summaries", (database_path, scan_id, dict(summaries))),
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.insert_watchlist",
        lambda database_path, scan_id, entries: captured.__setitem__("watchlist", (database_path, scan_id, list(entries))),
    )
    monkeypatch.setattr(
        "penny_stock_radar.services.watchlist_builder.fetch_latest_social_signals",
        lambda database_path, limit=20: [],
    )

    settings = SimpleNamespace(
        database_path=tmp_path / "penny_stock.sqlite3",
        filings_lookback_hours=48,
        watchlist_limit=10,
    )
    builder = WatchlistBuilder(
        settings=settings,
        filing_scanner=FakeFilingScanner(),
        setup_scanner=FakeSetupScanner(),
    )

    entries, returned_filings, returned_signals = builder.build()

    assert returned_filings == filings
    assert returned_signals == setup_signals
    assert [entry.symbol for entry in entries] == ["AAA", "CCC", "BBB"]
    assert entries[0].total_score > entries[1].total_score >= entries[2].total_score
    assert "low_float" in entries[0].reasons
    assert any("reverse stock split" in reason for reason in entries[0].reasons)
    assert entries[0].sympathy_score > 0
    assert captured["insert_filings"] is not None
    assert captured["summaries"] is not None
    assert captured["watchlist"] is not None
