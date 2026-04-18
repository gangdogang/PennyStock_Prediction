from __future__ import annotations

from pathlib import Path

import pytest

from penny_stock_radar.providers.listings import (
    KisMasterListingProvider,
    NasdaqTraderListingProvider,
    build_listing_provider,
)
from penny_stock_radar.config import AppSettings


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


def _write_kis_master_file(path: Path, rows: list[list[str]]) -> None:
    payload = "\n".join("\t".join(row) for row in rows) + "\n"
    path.write_bytes(payload.encode("cp949"))


def _kis_master_row(
    *,
    exchange_code: str,
    symbol: str,
    english_name: str,
    security_type: str = "2",
    type_code: str = "004",
) -> list[str]:
    return [
        "USA",
        "US",
        exchange_code,
        exchange_code,
        symbol,
        symbol,
        english_name,
        english_name,
        security_type,
        "USD",
        "",
        "",
        "1.00",
        "",
        "",
        "0930",
        "1600",
        "N",
        "",
        "",
        "",
        "",
        type_code,
        "",
    ]


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


def test_kis_master_listing_provider_reads_nasdaq_nyse_amex_files(tmp_path: Path) -> None:
    _write_kis_master_file(
        tmp_path / "NASMST.COD",
        [
            _kis_master_row(
                exchange_code="NAS",
                symbol="ABCD",
                english_name="ABCD Common Stock",
            ),
            _kis_master_row(
                exchange_code="NAS",
                symbol="QQQ",
                english_name="Invesco QQQ Trust",
                security_type="3",
                type_code="001",
            ),
        ],
    )
    _write_kis_master_file(
        tmp_path / "NYSMST.COD",
        [
            _kis_master_row(
                exchange_code="NYS",
                symbol="UNIT.U",
                english_name="Example Acquisition Corp Units",
            )
        ],
    )
    _write_kis_master_file(
        tmp_path / "AMSMST.COD",
        [
            _kis_master_row(
                exchange_code="AMS",
                symbol="WARR.WS",
                english_name="Example Warrant",
                security_type="4",
            )
        ],
    )

    provider = KisMasterListingProvider(master_path=tmp_path)

    rows = provider.fetch()

    assert [(row.symbol, row.exchange, row.is_etf) for row in rows] == [
        ("ABCD", "NAS", False),
        ("QQQ", "NAS", True),
        ("UNIT.U", "NYS", False),
        ("WARR.WS", "AMS", False),
    ]


def test_kis_master_listing_provider_requires_all_us_files_in_directory(tmp_path: Path) -> None:
    _write_kis_master_file(
        tmp_path / "NASMST.COD",
        [_kis_master_row(exchange_code="NAS", symbol="ABCD", english_name="ABCD Common Stock")],
    )

    provider = KisMasterListingProvider(master_path=tmp_path)

    with pytest.raises(FileNotFoundError, match="NYSMST.COD"):
        provider.fetch()


def test_build_listing_provider_falls_back_to_nasdaq_when_kis_files_are_missing(tmp_path: Path) -> None:
    settings = AppSettings(
        cache_dir=tmp_path / "cache",
        kis_nasdaq_master_path=tmp_path / "kis" / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "kis" / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "kis" / "AMSMST.COD",
    )

    provider = build_listing_provider(settings, client=_FakeClient(text=SAMPLE_LISTINGS))

    assert isinstance(provider, NasdaqTraderListingProvider)
