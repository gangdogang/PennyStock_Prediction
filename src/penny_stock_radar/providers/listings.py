from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import httpx

from ..models import ListingRecord


class NasdaqTraderListingProvider:
    """Downloads the public Nasdaq Trader symbol directory."""

    def __init__(
        self,
        source_url: str,
        cache_path: Path | None = None,
        client: Any | None = None,
    ) -> None:
        self.source_url = source_url
        self.cache_path = cache_path
        self.client = client

    def fetch(self, limit: int | None = None) -> list[ListingRecord]:
        payload_text: str
        try:
            response = self._http_get()
            response.raise_for_status()
            payload_text = response.text
            self._write_cache(payload_text)
        except Exception as exc:
            cached = self._read_cache()
            if cached is None:
                raise RuntimeError(
                    "Nasdaq Trader listings fetch failed and no cached listing seed is available."
                ) from exc
            payload_text = cached

        reader = csv.DictReader(io.StringIO(payload_text), delimiter="|")
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

    def _http_get(self) -> Any:
        if self.client is not None:
            return self.client.get(self.source_url, follow_redirects=True, timeout=30.0)
        return httpx.get(self.source_url, follow_redirects=True, timeout=30.0)

    def _read_cache(self) -> str | None:
        if self.cache_path is None:
            return None
        try:
            return self.cache_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, payload_text: str) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(payload_text, encoding="utf-8")
        except OSError:
            return
