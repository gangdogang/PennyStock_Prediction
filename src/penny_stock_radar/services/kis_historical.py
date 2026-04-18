from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import logging
import time as time_module
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

from ..config import AppSettings
from ..kis_auth import load_cached_token, store_cached_token
from ..db import (
    fetch_historical_l1_quotes,
    fetch_historical_minute_bars,
    fetch_latest_passed_universe,
    fetch_passed_universe_for_market_date,
    insert_historical_l1_quotes,
    insert_historical_minute_bars,
)
from ..models import HistoricalL1Quote, HistoricalMinuteBar
from ..providers.kis_time import parse_kis_market_timestamp
from ..providers.listings import KisMasterListingProvider

EASTERN = ZoneInfo("America/New_York")
_US_EXCHANGES = ("NAS", "NYS", "AMS")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HistoricalIngestSummary:
    market_date: str
    requested_symbols: int
    inserted_rows: int
    source: str
    rows: int = 0
    distinct_minute_keys: int = 0
    snapshot_mismatch_count: int = 0
    stale_timestamp_fallback_count: int = 0
    duplicate_minute_bucket_count: int = 0
    unresolved_symbols: list[str] = field(default_factory=list)
    skipped_symbols: list[str] = field(default_factory=list)


class KISHistoricalDataService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.Client(
            base_url=settings.kis_base_url,
            timeout=settings.live_market_timeout_seconds,
        )
        self._owns_client = client is None
        self._token_cache_path = settings.cache_dir / "kis_access_token.json"
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._exchange_by_symbol: dict[str, str] | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def backfill_minute_bars(
        self,
        market_date: date | str,
        *,
        symbols: Iterable[str] | None = None,
        symbol_limit: int | None = None,
        nmin: int = 1,
        max_pages: int = 10,
        source: str = "kis_minute_api",
    ) -> HistoricalIngestSummary:
        market_date_str = self._coerce_market_date(market_date).isoformat()
        requested_symbols = self._resolve_symbols(market_date_str, symbols, symbol_limit=symbol_limit)
        existing_rows = fetch_historical_minute_bars(
            self.settings.database_path,
            market_date=market_date_str,
        )
        existing_keys: set[tuple[str, str]] = set()
        for row in existing_rows:
            normalized_timestamp = self._normalized_timestamp_key(self._parse_timestamp(row["bar_at"]))
            if not normalized_timestamp:
                continue
            existing_keys.add((str(row["symbol"]).upper(), normalized_timestamp))
        unresolved: list[str] = []
        skipped: list[str] = []
        pending: list[HistoricalMinuteBar] = []
        exchange_map = self._exchange_map()

        for symbol in requested_symbols:
            exchange = exchange_map.get(symbol)
            if exchange is None:
                unresolved.append(symbol)
                continue
            rows = self._fetch_minute_rows(
                exchange=exchange,
                symbol=symbol,
                nmin=nmin,
                max_pages=max_pages,
            )
            if not rows:
                skipped.append(symbol)
                continue
            for row in rows:
                bar = self._minute_bar_from_row(
                    row,
                    symbol=symbol,
                    market_date=market_date_str,
                    source=source,
                )
                if bar is None:
                    continue
                key = (symbol, self._normalized_timestamp_key(bar.timestamp))
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                pending.append(bar)

        insert_historical_minute_bars(self.settings.database_path, pending)
        return HistoricalIngestSummary(
            market_date=market_date_str,
            requested_symbols=len(requested_symbols),
            inserted_rows=len(pending),
            source=source,
            unresolved_symbols=sorted(set(unresolved)),
            skipped_symbols=sorted(set(skipped)),
        )

    def capture_l1_quotes(
        self,
        *,
        symbols: Iterable[str],
        source: str = "kis_l1_snapshot",
    ) -> HistoricalIngestSummary:
        requested_symbols = sorted({str(symbol).upper() for symbol in symbols if str(symbol).strip()})
        if not requested_symbols:
            raise ValueError("At least one symbol is required to capture KIS L1 quotes.")
        exchange_map = self._exchange_map()
        snapshot_date = self._now_eastern().date().isoformat()
        existing_rows = fetch_historical_l1_quotes(
            self.settings.database_path,
            market_date=snapshot_date,
        )
        existing_keys = {
            (str(row["symbol"]).upper(), str(row["quote_at"]))
            for row in existing_rows
        }
        existing_minute_keys = {
            (
                str(row["symbol"]).upper(),
                self._minute_bucket_key(self._parse_timestamp(row["quote_at"])),
            )
            for row in existing_rows
            if self._parse_timestamp(row["quote_at"]) is not None
        }
        unresolved: list[str] = []
        skipped: list[str] = []
        pending: list[HistoricalL1Quote] = []
        warned_timestamp_drift = False
        observed_rows = 0
        snapshot_mismatch_count = 0
        stale_timestamp_fallback_count = 0
        duplicate_minute_bucket_count = 0
        pending_minute_keys: set[tuple[str, str]] = set()

        for symbol in requested_symbols:
            exchange = exchange_map.get(symbol)
            if exchange is None:
                unresolved.append(symbol)
                continue
            payload = self._get_kis_json(
                "/uapi/overseas-price/v1/quotations/inquire-asking-price",
                tr_id="HHDFS76200100",
                params={
                    "AUTH": "",
                    "EXCD": exchange,
                    "SYMB": symbol,
                },
            )
            quote, used_timestamp_fallback = self._l1_quote_from_payload(
                payload,
                symbol=symbol,
                source=source,
            )
            if quote is None:
                skipped.append(symbol)
                continue
            observed_rows += 1
            if used_timestamp_fallback:
                stale_timestamp_fallback_count += 1
            if quote.market_date != snapshot_date:
                snapshot_mismatch_count += 1
            quote_at_utc = quote.timestamp.astimezone(ZoneInfo("UTC"))
            now_utc = self._utc_now()
            delta_minutes = abs((now_utc - quote_at_utc).total_seconds()) / 60.0
            if delta_minutes > 120.0 and not warned_timestamp_drift:
                logger.warning(
                    "kis_l1 timestamp drift detected: symbol=%s delta_minutes=%s quote_at=%s now=%s",
                    symbol,
                    round(delta_minutes, 3),
                    quote_at_utc.isoformat(),
                    now_utc.isoformat(),
                )
                warned_timestamp_drift = True
            key = (symbol, quote.timestamp.isoformat())
            minute_key = (symbol, self._minute_bucket_key(quote.timestamp))
            if minute_key in existing_minute_keys or minute_key in pending_minute_keys:
                duplicate_minute_bucket_count += 1
            if key in existing_keys:
                continue
            existing_keys.add(key)
            if minute_key not in existing_minute_keys:
                pending_minute_keys.add(minute_key)
            pending.append(quote)

        insert_historical_l1_quotes(self.settings.database_path, pending)
        return HistoricalIngestSummary(
            market_date=snapshot_date,
            requested_symbols=len(requested_symbols),
            inserted_rows=len(pending),
            source=source,
            rows=observed_rows,
            distinct_minute_keys=len(pending_minute_keys),
            snapshot_mismatch_count=snapshot_mismatch_count,
            stale_timestamp_fallback_count=stale_timestamp_fallback_count,
            duplicate_minute_bucket_count=duplicate_minute_bucket_count,
            unresolved_symbols=sorted(set(unresolved)),
            skipped_symbols=sorted(set(skipped)),
        )

    def _resolve_symbols(
        self,
        market_date: str,
        symbols: Iterable[str] | None,
        *,
        symbol_limit: int | None,
    ) -> list[str]:
        explicit = sorted({str(symbol).upper() for symbol in (symbols or []) if str(symbol).strip()})
        if explicit:
            return explicit[:symbol_limit] if symbol_limit is not None else explicit

        rows = fetch_passed_universe_for_market_date(
            self.settings.database_path,
            market_date,
        )
        if not rows:
            rows = fetch_latest_passed_universe(
                self.settings.database_path,
                prefer_reportable=False,
            )
        resolved = sorted({str(row["symbol"]).upper() for row in rows if row["symbol"]})
        if symbol_limit is not None:
            return resolved[:symbol_limit]
        return resolved

    def _exchange_map(self) -> dict[str, str]:
        if self._exchange_by_symbol is not None:
            return self._exchange_by_symbol
        provider = KisMasterListingProvider(
            exchange_paths={
                "NAS": self.settings.kis_nasdaq_master_path,
                "NYS": self.settings.kis_nyse_master_path,
                "AMS": self.settings.kis_amex_master_path,
            }
        )
        records = provider.fetch()
        self._exchange_by_symbol = {
            record.symbol.upper(): record.exchange.upper()
            for record in records
            if record.exchange and record.exchange.upper() in _US_EXCHANGES
        }
        return self._exchange_by_symbol

    def _fetch_minute_rows(
        self,
        *,
        exchange: str,
        symbol: str,
        nmin: int,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        next_flag = ""
        keyb = ""
        pinc = "0"
        seen_keys: set[str] = set()

        for page_index in range(max_pages):
            payload = self._get_kis_json(
                "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
                tr_id="HHDFS76950200",
                params={
                    "AUTH": "",
                    "EXCD": exchange,
                    "SYMB": symbol,
                    "NMIN": str(nmin),
                    "PINC": pinc,
                    "NEXT": next_flag,
                    "NREC": "120",
                    "FILL": "",
                    "KEYB": keyb,
                },
            )
            if payload is None:
                break
            output_rows = payload.get("output2")
            if not isinstance(output_rows, list) or not output_rows:
                break
            for row in output_rows:
                if not isinstance(row, dict):
                    continue
                row_key = f"{row.get('xymd', '')}{row.get('xhms', '')}"
                if row_key and row_key in seen_keys:
                    continue
                if row_key:
                    seen_keys.add(row_key)
                collected.append(row)
            if page_index + 1 >= max_pages:
                break
            last_row = next(
                (row for row in reversed(output_rows) if isinstance(row, dict)),
                None,
            )
            if last_row is None:
                break
            next_key = self._minute_next_key(last_row, nmin=nmin)
            if next_key is None:
                break
            next_flag = "1"
            pinc = "1"
            keyb = next_key

        return collected

    def _minute_bar_from_row(
        self,
        row: dict[str, Any],
        *,
        symbol: str,
        market_date: str,
        source: str,
    ) -> HistoricalMinuteBar | None:
        timestamp = self._parse_market_timestamp(
            row.get("xymd") or row.get("tymd"),
            row.get("xhms"),
        )
        if timestamp is None or timestamp.date().isoformat() != market_date:
            return None
        open_price = self._coerce_float(row.get("open"))
        high_price = self._coerce_float(row.get("high"))
        low_price = self._coerce_float(row.get("low"))
        close_price = self._coerce_float(row.get("last"))
        volume = self._coerce_float(row.get("evol"))
        if None in {open_price, high_price, low_price, close_price, volume}:
            return None
        return HistoricalMinuteBar(
            symbol=symbol,
            market_date=market_date,
            market_phase=self._market_phase(timestamp),
            timestamp=timestamp,
            open_price=float(open_price),
            high_price=float(high_price),
            low_price=float(low_price),
            close_price=float(close_price),
            volume=float(volume),
            source=source,
        )

    def _l1_quote_from_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        symbol: str,
        source: str,
    ) -> tuple[HistoricalL1Quote | None, bool]:
        if not isinstance(payload, dict):
            return None, False
        meta_output = payload.get("output1")
        book_output = payload.get("output2")
        if not isinstance(meta_output, dict):
            meta_output = {}
        if not isinstance(book_output, dict):
            book_output = meta_output
        if not isinstance(book_output, dict):
            return None, False
        parsed_timestamp = self._parse_market_timestamp(
            meta_output.get("dymd"),
            meta_output.get("dhms"),
        )
        used_timestamp_fallback = parsed_timestamp is None
        timestamp = parsed_timestamp or self._now_eastern()
        bid_price = self._coerce_float(book_output.get("pbid1"))
        ask_price = self._coerce_float(book_output.get("pask1"))
        last_price = self._coerce_float(meta_output.get("last") or book_output.get("last"))
        if bid_price is None and ask_price is None and last_price is None:
            return None, used_timestamp_fallback
        return (
            HistoricalL1Quote(
                symbol=symbol,
                market_date=timestamp.date().isoformat(),
                timestamp=timestamp,
                bid_price=bid_price,
                ask_price=ask_price,
                last_price=last_price,
                source=source,
            ),
            used_timestamp_fallback,
        )

    def _minute_next_key(self, row: dict[str, Any], *, nmin: int) -> str | None:
        timestamp = self._parse_market_timestamp(row.get("xymd"), row.get("xhms"))
        if timestamp is None:
            return None
        next_timestamp = timestamp - timedelta(minutes=nmin)
        return next_timestamp.strftime("%Y%m%d%H%M%S")

    def _get_kis_json(
        self,
        path: str,
        *,
        tr_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        token = self._ensure_token()
        if not token:
            return None
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self.settings.kis_app_key or "",
            "appsecret": self.settings.kis_app_secret or "",
            "tr_id": tr_id,
            "custtype": self.settings.kis_custtype,
        }
        for attempt in range(3):
            try:
                response = self._client.get(path, params=params, headers=headers)
                payload = response.json()
            except Exception:
                return None
            if not isinstance(payload, dict):
                return None
            if str(payload.get("msg_cd")) == "EGW00201" and attempt < 2:
                time_module.sleep(0.35 * (attempt + 1))
                continue
            if response.is_success and str(payload.get("rt_cd")) in {"0", ""}:
                return payload
            if str(payload.get("rt_cd")) in {"0", ""}:
                return payload
            return None
        return None

    def _ensure_token(self) -> str | None:
        if self._access_token and self._token_expires_at and self._token_expires_at > self._utc_now():
            return self._access_token
        cached_token, cached_expires_at = load_cached_token(self._token_cache_path)
        if cached_token and cached_expires_at:
            self._access_token = cached_token
            self._token_expires_at = cached_expires_at
            return self._access_token
        if not self.settings.kis_app_key or not self.settings.kis_app_secret:
            return None
        try:
            response = self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.settings.kis_app_key,
                    "appsecret": self.settings.kis_app_secret,
                },
                headers={"content-type": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("access_token")
        expires_at = self._parse_utc_timestamp(
            payload.get("access_token_token_expired") or payload.get("access_token_expired")
        )
        if not token:
            return None
        self._access_token = str(token)
        self._token_expires_at = expires_at or self._utc_now()
        try:
            store_cached_token(
                self._token_cache_path,
                access_token=self._access_token,
                expires_at=self._token_expires_at,
            )
        except OSError:
            pass
        return self._access_token

    def _coerce_market_date(self, value: date | str) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    def _parse_market_timestamp(self, ymd: Any, hms: Any) -> datetime | None:
        return parse_kis_market_timestamp(ymd, hms, now_utc=self._utc_now())

    def _parse_timestamp(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=ZoneInfo("UTC"))

    def _minute_bucket_key(self, timestamp: datetime | None) -> str:
        if timestamp is None:
            return ""
        return (
            timestamp.astimezone(ZoneInfo("UTC"))
            .replace(second=0, microsecond=0)
            .isoformat()
        )

    def _normalized_timestamp_key(self, timestamp: datetime | None) -> str:
        if timestamp is None:
            return ""
        return timestamp.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    def _parse_utc_timestamp(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo("UTC"))
            except ValueError:
                continue
        return None

    def _coerce_float(self, value: Any) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _market_phase(self, timestamp: datetime) -> str:
        current = timestamp.timetz().replace(tzinfo=None)
        if time(4, 0) <= current < time(9, 30):
            return "premarket"
        if time(9, 30) <= current < time(16, 0):
            return "regular"
        if time(16, 0) <= current < time(20, 0):
            return "postmarket"
        return "overnight"

    def _utc_now(self) -> datetime:
        return datetime.now(tz=ZoneInfo("UTC"))

    def _now_eastern(self) -> datetime:
        return datetime.now(tz=EASTERN)
