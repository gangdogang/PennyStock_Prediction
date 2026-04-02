from __future__ import annotations

from pathlib import Path

import pytest

from penny_stock_radar.providers.listings import NasdaqTraderListingProvider


SAMPLE_LISTINGS = """Symbol|Security Name|Listing Exchange|ETF|Test Issue|NextShares
ABCD|ABCD Corp|Q|N|N|N
EFGH|EFGH ETF|Q|Y|N|N
File Creation Time|20260402|||||
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, *, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def get(self, url: str, follow_redirects: bool = True, timeout: float = 30.0) -> _FakeResponse:
        del url, follow_redirects, timeout
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.text or "")


def test_listings_provider_writes_cache_on_success(tmp_path: Path) -> None:
    cache_path = tmp_path / "nasdaqtraded.txt"
    provider = NasdaqTraderListingProvider(
        "https://example.test/nasdaqtraded.txt",
        cache_path=cache_path,
        client=_FakeClient(text=SAMPLE_LISTINGS),
    )

    rows = provider.fetch(limit=1)

    assert [row.symbol for row in rows] == ["ABCD"]
    assert cache_path.read_text(encoding="utf-8") == SAMPLE_LISTINGS


def test_listings_provider_uses_cache_when_remote_fetch_fails(tmp_path: Path) -> None:
    cache_path = tmp_path / "nasdaqtraded.txt"
    cache_path.write_text(SAMPLE_LISTINGS, encoding="utf-8")
    provider = NasdaqTraderListingProvider(
        "https://example.test/nasdaqtraded.txt",
        cache_path=cache_path,
        client=_FakeClient(error=RuntimeError("offline")),
    )

    rows = provider.fetch()

    assert [row.symbol for row in rows] == ["ABCD", "EFGH"]


def test_listings_provider_raises_without_remote_or_cache(tmp_path: Path) -> None:
    provider = NasdaqTraderListingProvider(
        "https://example.test/nasdaqtraded.txt",
        cache_path=tmp_path / "missing.txt",
        client=_FakeClient(error=RuntimeError("offline")),
    )

    with pytest.raises(RuntimeError, match="no cached listing seed"):
        provider.fetch()
