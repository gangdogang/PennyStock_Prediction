from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..config import AppSettings
from ..models import ListingRecord


_KIS_MASTER_COLUMNS = (
    "national_code",
    "exchange_id",
    "exchange_code",
    "exchange_name",
    "symbol",
    "realtime_symbol",
    "korean_name",
    "english_name",
    "security_type",
    "currency",
    "float_position",
    "data_type",
    "base_price",
    "bid_order_size",
    "ask_order_size",
    "market_start_time",
    "market_end_time",
    "dr_yn",
    "dr_national_code",
    "sector",
    "existence_of_constituent_stocks",
    "tick_size_type",
    "type",
    "tick_size_type_detail",
)
_KIS_ETF_TYPE_CODES = {"001", "002", "003", "005", "006"}
_KIS_EXCHANGE_FILENAMES = {
    "NAS": "NASMST.COD",
    "NYS": "NYSMST.COD",
    "AMS": "AMSMST.COD",
}
_KIS_EXCHANGE_ALIASES = {
    "NASDAQ": "NAS",
    "NAS": "NAS",
    "NYSE": "NYS",
    "NYS": "NYS",
    "AMEX": "AMS",
    "AMS": "AMS",
}


class ListingProvider(Protocol):
    def fetch(self, limit: int | None = None) -> list[ListingRecord]: ...


class NasdaqTraderListingProvider:
    """Downloads the public Nasdaq Trader symbol directory."""

    source_name = "nasdaq_trader"

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
            records.append(
                ListingRecord(
                    symbol=symbol,
                    company_name=(row.get("Security Name") or "").strip(),
                    exchange=(row.get("Listing Exchange") or "").strip() or "UNKNOWN",
                    is_etf=(row.get("ETF") or "").strip().upper() == "Y",
                    is_test_issue=(row.get("Test Issue") or "").strip().upper() == "Y",
                    is_nextshares=(row.get("NextShares") or "").strip().upper() == "Y",
                )
            )
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


class KisMasterListingProvider:
    """Parses local KIS overseas master files for broker-supported U.S. symbols."""

    source_name = "kis_master"

    def __init__(
        self,
        master_path: Path | str | None = None,
        exchange_paths: dict[str, Path | str] | None = None,
    ) -> None:
        self.master_path = Path(master_path) if master_path is not None else None
        self.exchange_paths = {
            self._normalize_exchange_key(exchange): Path(path)
            for exchange, path in (exchange_paths or {}).items()
        }

    def fetch(self, limit: int | None = None) -> list[ListingRecord]:
        records: list[ListingRecord] = []
        seen_symbols: set[str] = set()
        for exchange, path in self._resolved_exchange_paths().items():
            for record in self._parse_master_file(path, exchange=exchange):
                normalized_symbol = record.symbol.upper()
                if normalized_symbol in seen_symbols:
                    continue
                seen_symbols.add(normalized_symbol)
                records.append(record)
                if limit is not None and len(records) >= limit:
                    return records
        return records

    def _resolved_exchange_paths(self) -> dict[str, Path]:
        if self.master_path is not None:
            resolved: dict[str, Path] = {}
            missing: list[str] = []
            for exchange, filename in _KIS_EXCHANGE_FILENAMES.items():
                explicit_path = self.exchange_paths.get(exchange)
                derived_path = self.master_path / filename
                path = explicit_path if explicit_path is not None and explicit_path.exists() else derived_path
                resolved[exchange] = path
                if not path.exists():
                    missing.append(path.name)
            if missing:
                raise FileNotFoundError(
                    f"KIS master directory is missing required files: {', '.join(sorted(missing))}"
                )
            return {exchange: resolved[exchange] for exchange in ("NAS", "NYS", "AMS")}

        if not self.exchange_paths:
            raise ValueError("KIS master provider requires a master directory or exchange paths.")

        return {
            exchange: self.exchange_paths[exchange]
            for exchange in ("NAS", "NYS", "AMS")
            if exchange in self.exchange_paths
        }

    def _parse_master_file(self, path: Path, *, exchange: str) -> list[ListingRecord]:
        payload_text = self._read_text(path)
        reader = csv.reader(io.StringIO(payload_text), delimiter="\t")
        records: list[ListingRecord] = []
        for raw_row in reader:
            if not raw_row:
                continue
            row = [str(value or "").strip() for value in raw_row]
            if not any(row):
                continue
            if len(row) < len(_KIS_MASTER_COLUMNS):
                row.extend([""] * (len(_KIS_MASTER_COLUMNS) - len(row)))
            fields = dict(zip(_KIS_MASTER_COLUMNS, row))
            symbol = fields["symbol"].upper()
            if not symbol:
                continue
            company_name = fields["english_name"] or fields["korean_name"] or symbol
            records.append(
                ListingRecord(
                    symbol=symbol,
                    company_name=company_name,
                    exchange=(fields["exchange_code"] or exchange).upper(),
                    realtime_symbol=fields["realtime_symbol"] or None,
                    name_ko=fields["korean_name"] or None,
                    name_en=fields["english_name"] or None,
                    currency=fields["currency"] or None,
                    security_type=fields["security_type"] or None,
                    security_subtype_code=fields["type"] or None,
                    base_price=self._coerce_float(fields["base_price"]),
                    is_dr=fields["dr_yn"].upper() == "Y",
                    industry_code=fields["sector"] or None,
                    is_etf=self._is_etf_row(fields),
                )
            )
        return records

    def _read_text(self, path: Path) -> str:
        for encoding in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="cp949", errors="ignore")

    def _is_etf_row(self, fields: dict[str, str]) -> bool:
        security_type = fields["security_type"].strip()
        instrument_type = fields["type"].strip()
        return security_type == "3" or instrument_type in _KIS_ETF_TYPE_CODES

    def _coerce_float(self, value: str) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _normalize_exchange_key(self, exchange: str) -> str:
        normalized = _KIS_EXCHANGE_ALIASES.get(str(exchange).strip().upper())
        if normalized is None:
            raise ValueError(f"Unsupported KIS exchange key: {exchange}")
        return normalized


def build_listing_provider(
    settings: AppSettings,
    client: Any | None = None,
) -> ListingProvider:
    explicit_paths = {
        exchange: path
        for exchange, path in _kis_exchange_paths_from_settings(settings).items()
        if path.exists()
    }
    master_path = _path_setting(
        settings,
        "kis_master_path",
        "kis_master_dir",
        "kis_us_master_path",
        "kis_us_master_dir",
    )
    if master_path is not None and master_path.exists():
        return KisMasterListingProvider(master_path=master_path)
    if all(exchange in explicit_paths for exchange in ("NAS", "NYS", "AMS")):
        return KisMasterListingProvider(exchange_paths=explicit_paths)

    cache_dir = getattr(settings, "cache_dir", Path("data/cache"))
    return NasdaqTraderListingProvider(
        settings.nasdaq_trader_url,
        cache_path=cache_dir / "nasdaqtraded.txt",
        client=client,
    )


def _kis_exchange_paths_from_settings(settings: AppSettings) -> dict[str, Path]:
    exchange_paths: dict[str, Path] = {}
    for exchange, setting_names in (
        ("NAS", ("kis_nasdaq_master_path", "kis_master_nasdaq_path", "kis_nas_master_path")),
        ("NYS", ("kis_nyse_master_path", "kis_master_nyse_path", "kis_nys_master_path")),
        ("AMS", ("kis_amex_master_path", "kis_master_amex_path", "kis_ams_master_path")),
    ):
        path = _path_setting(settings, *setting_names)
        if path is not None:
            exchange_paths[exchange] = path
    return exchange_paths


def _path_setting(settings: AppSettings, *names: str) -> Path | None:
    for name in names:
        value = getattr(settings, name, None)
        if value:
            return Path(value)
    return None
