from __future__ import annotations

from datetime import UTC, datetime

from penny_stock_radar.config import AppSettings
from penny_stock_radar.services.filing_scanner import FilingScanner


class FakeSecProvider:
    def fetch_company_tickers(self):
        return {
            "ABCD": {
                "cik": "0000123456",
                "title": "ABCD Corp",
            }
        }

    def fetch_submissions(self, cik: str):
        now = datetime.now(UTC).replace(microsecond=0)
        return {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-Q"],
                    "filingDate": [now.date().isoformat(), now.date().isoformat()],
                    "acceptanceDateTime": [
                        now.isoformat().replace("+00:00", "Z"),
                        now.isoformat().replace("+00:00", "Z"),
                    ],
                    "accessionNumber": ["0000123456-26-000001", "0000123456-26-000002"],
                    "primaryDocument": ["abdc8k.htm", "quarterly.htm"],
                    "primaryDocDescription": ["Current report", "Quarterly report"],
                }
            }
        }

    def build_filing_url(self, cik: str, accession_number: str, primary_document: str | None):
        return f"https://example.test/{cik}/{accession_number}/{primary_document}"

    def fetch_filing_text(self, filing_url: str) -> str:
        return "<html><body>Letter of intent and merger agreement executed.</body></html>"


def test_filing_scanner_emits_recent_matches() -> None:
    scanner = FilingScanner(AppSettings(), provider=FakeSecProvider())
    matches = scanner.scan_symbols(["ABCD"], lookback_hours=48)

    assert len(matches) == 1
    match = matches[0]
    assert match.symbol == "ABCD"
    assert match.form == "8-K"
    assert "merger" in match.summary.lower()
    assert "letter of intent" in match.matched_keywords


class MixedTimingSecProvider(FakeSecProvider):
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
                    "primaryDocument": ["pre.htm", "post.htm"],
                    "primaryDocDescription": ["Current report", "Current report"],
                }
            }
        }


def test_filing_scanner_excludes_filings_after_cutoff() -> None:
    scanner = FilingScanner(AppSettings(), provider=MixedTimingSecProvider())
    matches = scanner.scan_symbols(
        ["ABCD"],
        lookback_hours=48,
        filed_at_cutoff=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
    )

    assert len(matches) == 1
    assert matches[0].accession_number == "0000123456-26-000001"
