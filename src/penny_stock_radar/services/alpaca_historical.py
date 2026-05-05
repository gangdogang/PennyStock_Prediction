from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

from ..config import AppSettings
from ..db import fetch_historical_l1_quotes, insert_historical_l1_quotes
from ..models import HistoricalL1Quote

EASTERN = ZoneInfo("America/New_York")
ALPACA_HISTORICAL_QUOTES_SOURCE = "alpaca_iex_historical_quotes"
ALPACA_DIAGNOSTIC_WARNING = (
    "Alpaca IEX historical quotes are diagnostic-only and must not be used as "
    "falsification cost evidence or blocker-resolution evidence."
)


@dataclass(frozen=True, slots=True)
class AlpacaHistoricalQuoteWindow:
    symbols: tuple[str, ...]
    market_date: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class AlpacaHistoricalQuoteSummary:
    market_date: str
    requested_symbols: int
    inserted_rows: int
    source: str
    rows: int = 0
    windows: int = 0
    pages: int = 0
    duplicate_rows: int = 0
    db_duplicate_rows: int = 0
    in_memory_duplicate_rows: int = 0
    skipped_rows: int = 0
    diagnostic_only: bool = True
    warning: str = ALPACA_DIAGNOSTIC_WARNING
    warnings: list[str] = field(default_factory=lambda: [ALPACA_DIAGNOSTIC_WARNING])
    market_dates: list[str] = field(default_factory=list)


class AlpacaHistoricalDataService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        data_base_url: str | None = None,
        feed: str | None = None,
        limit: int | None = None,
    ) -> None:
        self.settings = settings
        self.api_key = api_key if api_key is not None else _first_setting(
            settings,
            "alpaca_api_key",
            "alpaca_key_id",
            "alpaca_key",
        )
        self.api_secret = api_secret if api_secret is not None else _first_setting(
            settings,
            "alpaca_secret_key",
            "alpaca_api_secret",
            "alpaca_secret",
        )
        self.feed = str(
            feed
            if feed is not None
            else _first_setting(
                settings,
                "alpaca_market_data_feed",
                "alpaca_historical_feed",
                "alpaca_feed",
                default="iex",
            )
        )
        self.limit = int(
            limit
            if limit is not None
            else _first_setting(settings, "alpaca_historical_quote_limit", default=10_000)
        )
        timeout = float(
            _first_setting(
                settings,
                "alpaca_timeout_seconds",
                "live_market_timeout_seconds",
                default=10.0,
            )
        )
        base_url = str(
            data_base_url
            if data_base_url is not None
            else _first_setting(
                settings,
                "alpaca_data_base_url",
                "alpaca_market_data_base_url",
                default="https://data.alpaca.markets",
            )
        )
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def import_historical_quotes(
        self,
        windows: Iterable[AlpacaHistoricalQuoteWindow],
        *,
        source: str = ALPACA_HISTORICAL_QUOTES_SOURCE,
        feed: str | None = None,
        limit: int | None = None,
        max_pages_per_window: int | None = None,
    ) -> AlpacaHistoricalQuoteSummary:
        quote_windows = list(windows)
        if not quote_windows:
            raise ValueError("At least one Alpaca quote window is required.")

        requested_symbols = {
            symbol
            for window in quote_windows
            for symbol in _normalize_symbols(window.symbols)
        }
        source_feed = str(feed or self.feed)
        page_limit = int(limit or self.limit)
        max_pages = int(
            max_pages_per_window
            if max_pages_per_window is not None
            else _first_setting(self.settings, "alpaca_historical_max_pages", default=100)
        )
        if max_pages <= 0:
            raise ValueError("max_pages_per_window must be positive.")

        market_dates = sorted({window.market_date for window in quote_windows})
        existing_key_cache: dict[str, set[tuple[str, str]]] = {}
        in_memory_keys: set[tuple[str, str]] = set()
        pending: list[HistoricalL1Quote] = []
        warnings = [ALPACA_DIAGNOSTIC_WARNING]
        observed_rows = 0
        page_count = 0
        skipped_rows = 0
        db_duplicate_rows = 0
        in_memory_duplicate_rows = 0

        for window in quote_windows:
            symbols = _normalize_symbols(window.symbols)
            if not symbols:
                continue
            page_token: str | None = None
            seen_page_tokens: set[str] = set()
            for page_index in range(max_pages):
                payload = self._get_quotes_page(
                    symbols=symbols,
                    start_at=window.start_at,
                    end_at=window.end_at,
                    feed=source_feed,
                    limit=page_limit,
                    page_token=page_token,
                )
                if payload is None:
                    warnings.append("alpaca_request_failed")
                    break
                page_count += 1
                for symbol, raw_quote in _iter_payload_quotes(payload, symbols=symbols):
                    quote = _historical_quote_from_alpaca_row(
                        raw_quote,
                        symbol=symbol,
                        source=source,
                    )
                    if quote is None:
                        skipped_rows += 1
                        continue
                    observed_rows += 1
                    existing_keys = existing_key_cache.setdefault(
                        quote.market_date,
                        _existing_quote_keys(
                            self.settings.database_path,
                            market_date=quote.market_date,
                            symbols=requested_symbols,
                        ),
                    )
                    key = _quote_key(quote)
                    if key in existing_keys:
                        db_duplicate_rows += 1
                        continue
                    if key in in_memory_keys:
                        in_memory_duplicate_rows += 1
                        continue
                    in_memory_keys.add(key)
                    pending.append(quote)
                next_page_token = _next_page_token(payload)
                if not next_page_token:
                    break
                if next_page_token in seen_page_tokens:
                    warnings.append("alpaca_repeated_next_page_token")
                    break
                seen_page_tokens.add(next_page_token)
                page_token = next_page_token
                if page_index + 1 >= max_pages:
                    warnings.append("alpaca_pagination_truncated")

        insert_historical_l1_quotes(self.settings.database_path, pending)
        return AlpacaHistoricalQuoteSummary(
            market_date=market_dates[0] if len(market_dates) == 1 else "multiple",
            requested_symbols=len(requested_symbols),
            inserted_rows=len(pending),
            source=source,
            rows=observed_rows,
            windows=len(quote_windows),
            pages=page_count,
            duplicate_rows=db_duplicate_rows + in_memory_duplicate_rows,
            db_duplicate_rows=db_duplicate_rows,
            in_memory_duplicate_rows=in_memory_duplicate_rows,
            skipped_rows=skipped_rows,
            warnings=_dedupe_preserving_order(warnings),
            market_dates=market_dates,
        )

    def _get_quotes_page(
        self,
        *,
        symbols: tuple[str, ...],
        start_at: datetime,
        end_at: datetime,
        feed: str,
        limit: int,
        page_token: str | None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "start": _format_rfc3339(start_at),
            "end": _format_rfc3339(end_at),
            "feed": feed,
            "limit": str(limit),
        }
        if page_token:
            params["page_token"] = page_token
        try:
            response = self._client.get(
                "/v2/stocks/quotes",
                params=params,
                headers=self._headers(),
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not response.is_success or not isinstance(payload, dict):
            return None
        return payload

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["APCA-API-KEY-ID"] = self.api_key
        if self.api_secret:
            headers["APCA-API-SECRET-KEY"] = self.api_secret
        return headers


def build_quote_windows(
    symbols: Iterable[str],
    market_date: date | str,
    start_time: time | str | datetime,
    end_time: time | str | datetime,
    *,
    symbols_per_window: int | None = None,
) -> list[AlpacaHistoricalQuoteWindow]:
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        raise ValueError("At least one symbol is required.")
    market_day = _coerce_market_date(market_date)
    start_at = _coerce_market_datetime(market_day, start_time)
    end_at = _coerce_market_datetime(market_day, end_time)
    if end_at <= start_at:
        raise ValueError("end_time must be after start_time.")
    batches = _symbol_batches(normalized_symbols, symbols_per_window)
    return [
        AlpacaHistoricalQuoteWindow(
            symbols=batch,
            market_date=market_day.isoformat(),
            start_at=start_at,
            end_at=end_at,
        )
        for batch in batches
    ]


def build_quote_windows_from_strategy_run_dir(
    strategy_run_dir: Path | str,
    *,
    bucket: str | None = None,
    minutes_before: float = 1.0,
    minutes_after: float = 1.0,
    symbols_per_window: int | None = None,
) -> list[AlpacaHistoricalQuoteWindow]:
    return build_quote_windows_from_trade_log(
        Path(strategy_run_dir) / "paper_trade_log.csv",
        bucket=bucket,
        minutes_before=minutes_before,
        minutes_after=minutes_after,
        symbols_per_window=symbols_per_window,
    )


def build_quote_windows_from_trade_log(
    trade_log_path: Path | str,
    *,
    bucket: str | None = None,
    minutes_before: float = 1.0,
    minutes_after: float = 1.0,
    symbols_per_window: int | None = None,
) -> list[AlpacaHistoricalQuoteWindow]:
    path = Path(trade_log_path)
    schedules = _trade_log_entry_schedule(path, bucket=bucket)
    grouped: dict[tuple[str, str, str], set[str]] = {}
    window_times: dict[tuple[str, str, str], tuple[datetime, datetime]] = {}
    before = timedelta(minutes=minutes_before)
    after = timedelta(minutes=minutes_after)
    for schedule in schedules:
        entry_at = _parse_timestamp(schedule["entry_at"], default_tz=EASTERN)
        if entry_at is None:
            continue
        entry_at = entry_at.astimezone(EASTERN)
        start_at = entry_at - before
        end_at = entry_at + after
        if end_at <= start_at:
            continue
        key = (schedule["market_date"], start_at.isoformat(), end_at.isoformat())
        grouped.setdefault(key, set()).add(schedule["symbol"])
        window_times[key] = (start_at, end_at)

    windows: list[AlpacaHistoricalQuoteWindow] = []
    for key in sorted(grouped):
        market_date, _, _ = key
        start_at, end_at = window_times[key]
        for batch in _symbol_batches(tuple(sorted(grouped[key])), symbols_per_window):
            windows.append(
                AlpacaHistoricalQuoteWindow(
                    symbols=batch,
                    market_date=market_date,
                    start_at=start_at,
                    end_at=end_at,
                )
            )
    return windows


def _trade_log_entry_schedule(
    trade_log_path: Path,
    *,
    bucket: str | None,
) -> list[dict[str, str]]:
    if not trade_log_path.exists():
        return []
    with trade_log_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    entry_rows = [
        row
        for row in rows
        if str(row.get("event") or "").strip().upper() == "ENTRY"
    ]
    if entry_rows:
        rows = entry_rows

    schedules: list[dict[str, str]] = []
    for row in rows:
        row_bucket = str(row.get("bucket") or "").strip()
        if bucket is not None and row_bucket != bucket:
            continue
        event = str(row.get("event") or "").strip().upper()
        status = str(row.get("status") or "").strip().upper()
        if event and event != "ENTRY":
            continue
        if not event and status and status != "CLOSED":
            continue
        entry_at = str(row.get("entry_at") or row.get("opened_at") or "").strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        if not entry_at or not symbol:
            continue
        market_date = str(row.get("market_date") or "").strip()
        if not market_date:
            market_date = _market_date_from_timestamp(entry_at)
        if not market_date:
            continue
        schedules.append(
            {
                "bucket": row_bucket,
                "symbol": symbol,
                "market_date": market_date,
                "entry_at": entry_at,
            }
        )
    return schedules


def _historical_quote_from_alpaca_row(
    row: dict[str, Any],
    *,
    symbol: str,
    source: str,
) -> HistoricalL1Quote | None:
    timestamp = _parse_timestamp(row.get("t"), default_tz=timezone.utc)
    bid_price = _coerce_float(row.get("bp"))
    ask_price = _coerce_float(row.get("ap"))
    if timestamp is None or (bid_price is None and ask_price is None):
        return None
    timestamp = timestamp.astimezone(timezone.utc)
    return HistoricalL1Quote(
        symbol=symbol.upper(),
        market_date=timestamp.astimezone(EASTERN).date().isoformat(),
        timestamp=timestamp,
        bid_price=bid_price,
        ask_price=ask_price,
        last_price=None,
        source=source,
    )


def _iter_payload_quotes(
    payload: dict[str, Any],
    *,
    symbols: tuple[str, ...],
) -> Iterable[tuple[str, dict[str, Any]]]:
    raw_quotes = payload.get("quotes")
    if isinstance(raw_quotes, dict):
        for symbol, rows in raw_quotes.items():
            normalized_symbol = str(symbol).strip().upper()
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and normalized_symbol:
                    yield normalized_symbol, row
        return

    if isinstance(raw_quotes, list):
        fallback_symbol = symbols[0] if len(symbols) == 1 else ""
        payload_symbol = str(payload.get("symbol") or "").strip().upper()
        for row in raw_quotes:
            if not isinstance(row, dict):
                continue
            symbol = str(
                row.get("S")
                or row.get("symbol")
                or payload_symbol
                or fallback_symbol
            ).strip().upper()
            if symbol:
                yield symbol, row


def _existing_quote_keys(
    database_path: Path,
    *,
    market_date: str,
    symbols: set[str],
) -> set[tuple[str, str]]:
    rows = fetch_historical_l1_quotes(database_path, market_date=market_date)
    keys: set[tuple[str, str]] = set()
    for row in rows:
        symbol = str(row["symbol"]).strip().upper()
        if symbols and symbol not in symbols:
            continue
        timestamp = _parse_timestamp(row["quote_at"], default_tz=timezone.utc)
        normalized_timestamp = _normalized_timestamp_key(timestamp) if timestamp else str(row["quote_at"])
        keys.add((symbol, normalized_timestamp))
    return keys


def _quote_key(quote: HistoricalL1Quote) -> tuple[str, str]:
    return (quote.symbol.upper(), _normalized_timestamp_key(quote.timestamp))


def _next_page_token(payload: dict[str, Any]) -> str | None:
    token = payload.get("next_page_token")
    text = str(token or "").strip()
    return text or None


def _first_setting(settings: AppSettings, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(settings, name, None)
        if value is not None:
            return value
    return default


def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))


def _symbol_batches(
    symbols: tuple[str, ...],
    symbols_per_window: int | None,
) -> list[tuple[str, ...]]:
    if symbols_per_window is None:
        return [symbols]
    if symbols_per_window <= 0:
        raise ValueError("symbols_per_window must be positive.")
    return [
        symbols[index : index + symbols_per_window]
        for index in range(0, len(symbols), symbols_per_window)
    ]


def _coerce_market_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _coerce_market_datetime(
    market_date: date,
    value: time | str | datetime,
) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=EASTERN)
    parsed_time = value if isinstance(value, time) else time.fromisoformat(str(value))
    combined = datetime.combine(market_date, parsed_time)
    if combined.tzinfo is None:
        return combined.replace(tzinfo=EASTERN)
    return combined


def _parse_timestamp(
    value: Any,
    *,
    default_tz: timezone | ZoneInfo,
) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = _truncate_fractional_seconds(text)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=default_tz)
    return parsed


def _truncate_fractional_seconds(text: str) -> str:
    suffix = ""
    body = text
    t_index = max(text.find("T"), text.find(" "))
    if t_index >= 0:
        plus_index = text.rfind("+")
        minus_index = text.rfind("-")
        tz_index = max(
            plus_index if plus_index > t_index else -1,
            minus_index if minus_index > t_index else -1,
        )
        if tz_index >= 0:
            body = text[:tz_index]
            suffix = text[tz_index:]
    if body.endswith("Z"):
        body = body[:-1]
        suffix = "Z"
    if "." not in body:
        return body + suffix
    prefix, fraction = body.split(".", 1)
    digits = []
    tail = ""
    for index, char in enumerate(fraction):
        if char.isdigit():
            digits.append(char)
            continue
        tail = fraction[index:]
        break
    if not digits:
        return prefix + suffix
    return f"{prefix}.{''.join(digits)[:6]}{tail}{suffix}"


def _format_rfc3339(value: datetime) -> str:
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=EASTERN)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_timestamp_key(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).isoformat()


def _coerce_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _market_date_from_timestamp(value: str) -> str:
    timestamp = _parse_timestamp(value, default_tz=EASTERN)
    if timestamp is None:
        return ""
    return timestamp.astimezone(EASTERN).date().isoformat()


def _dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
