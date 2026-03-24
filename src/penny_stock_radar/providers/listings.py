from __future__ import annotations

import csv
import io

import httpx

from ..models import ListingRecord


class NasdaqTraderListingProvider:
    """Downloads the public Nasdaq Trader symbol directory."""

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url

    def fetch(self, limit: int | None = None) -> list[ListingRecord]:
        response = httpx.get(self.source_url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()

        reader = csv.DictReader(io.StringIO(response.text), delimiter="|")
        records: list[ListingRecord] = []
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol or symbol == "File Creation Time":
                continue
            record = ListingRecord(
                symbol=symbol,
                company_name=(row.get("Security Name") or "").strip(),
                exchange=(row.get("Listing Exchange") or "").strip() or "UNKNOWN",
                is_etf=(row.get("ETF") or "").strip().upper() == "Y",
                is_test_issue=(row.get("Test Issue") or "").strip().upper() == "Y",
                is_nextshares=(row.get("NextShares") or "").strip().upper() == "Y",
            )
            records.append(record)
            if limit is not None and len(records) >= limit:
                break
        return records
