from __future__ import annotations

from pathlib import Path

from penny_stock_radar.config import AppSettings
from penny_stock_radar.models import ListingRecord, MetadataRecord
from penny_stock_radar.services.universe_builder import UniverseBuilder


class FakeListingProvider:
    def __init__(self, records: list[ListingRecord]) -> None:
        self._records = records

    def fetch(self, limit=None):
        return list(self._records if limit is None else self._records[:limit])


class RecordingMetadataProvider:
    def __init__(self) -> None:
        self.price_requests: list[str] = []
        self.metadata_requests: list[str] = []

    def fetch_prices(self, symbols):
        self.price_requests = list(symbols)
        return {"ABCD": 1.25}

    def fetch_metadata(self, symbols, max_workers, prices=None):
        del max_workers, prices
        self.metadata_requests = list(symbols)
        return {
            "ABCD": MetadataRecord(
                symbol="ABCD",
                price=1.25,
                market_cap=80_000_000,
                float_shares=8_000_000,
                quote_type="EQUITY",
            )
        }


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


def test_universe_builder_skips_structurally_filtered_symbols_before_yfinance(tmp_path) -> None:
    settings = AppSettings(db_path=tmp_path / "radar.sqlite3")
    metadata_provider = RecordingMetadataProvider()
    builder = UniverseBuilder(
        settings,
        listing_provider=FakeListingProvider(
            [
                ListingRecord(symbol="ABCD", company_name="ABCD Corp", exchange="Q"),
                ListingRecord(
                    symbol="ACHR.W",
                    company_name="Archer Aviation Inc Warrant",
                    exchange="Q",
                ),
                ListingRecord(
                    symbol="ABR$E",
                    company_name="Arbor Realty Trust 8.250% Series E Preferred Stock",
                    exchange="N",
                ),
            ]
        ),
        metadata_provider=metadata_provider,
    )

    _, candidates = builder.run(max_symbols=10)

    assert metadata_provider.price_requests == ["ABCD"]
    assert metadata_provider.metadata_requests == ["ABCD"]

    by_symbol = {row.symbol: row for row in candidates}
    assert by_symbol["ABCD"].passed_filters is True
    assert "warrant_security" in by_symbol["ACHR.W"].filter_reasons
    assert "preferred_security" in by_symbol["ABR$E"].filter_reasons


def test_universe_builder_uses_kis_master_universe_when_configured(tmp_path: Path) -> None:
    master_dir = tmp_path / "kis_master"
    master_dir.mkdir()
    _write_kis_master_file(
        master_dir / "NASMST.COD",
        [
            _kis_master_row(
                exchange_code="NAS",
                symbol="ABCD",
                english_name="ABCD Common Stock",
            ),
            _kis_master_row(
                exchange_code="NAS",
                symbol="ETF1",
                english_name="Example ETF",
                security_type="3",
                type_code="001",
            ),
        ],
    )
    _write_kis_master_file(
        master_dir / "NYSMST.COD",
        [
            _kis_master_row(
                exchange_code="NYS",
                symbol="UNIT.U",
                english_name="Example Acquisition Corp Units",
            ),
            _kis_master_row(
                exchange_code="NYS",
                symbol="SPAC",
                english_name="Example Acquisition Corporation",
            ),
        ],
    )
    _write_kis_master_file(
        master_dir / "AMSMST.COD",
        [
            _kis_master_row(
                exchange_code="AMS",
                symbol="WARR.WS",
                english_name="Example Warrant",
                security_type="4",
            ),
            _kis_master_row(
                exchange_code="AMS",
                symbol="PREF.PRA",
                english_name="Example Preferred Stock",
            ),
            _kis_master_row(
                exchange_code="AMS",
                symbol="RIGHT.RT",
                english_name="Example Rights",
            ),
        ],
    )

    settings = AppSettings(db_path=tmp_path / "radar.sqlite3")
    object.__setattr__(settings, "kis_master_path", master_dir)
    metadata_provider = RecordingMetadataProvider()
    builder = UniverseBuilder(settings, metadata_provider=metadata_provider)

    snapshot, candidates = builder.run(max_symbols=20)

    assert snapshot.source == "kis_master+yfinance"
    assert metadata_provider.price_requests == ["ABCD"]
    assert metadata_provider.metadata_requests == ["ABCD"]

    by_symbol = {row.symbol: row for row in candidates}
    assert by_symbol["ABCD"].passed_filters is True
    assert "unit_security" in by_symbol["UNIT.U"].filter_reasons
    assert "spac_security" in by_symbol["SPAC"].filter_reasons
    assert "warrant_security" in by_symbol["WARR.WS"].filter_reasons
    assert "preferred_security" in by_symbol["PREF.PRA"].filter_reasons
    assert "rights_security" in by_symbol["RIGHT.RT"].filter_reasons
    assert "ETF1" not in by_symbol
