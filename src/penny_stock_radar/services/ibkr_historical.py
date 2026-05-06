from __future__ import annotations

import os
import time as time_module
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo

from ..db import get_connection, init_database, insert_historical_l1_quotes
from ..models import HistoricalL1Quote
from .external_data_validator import (
    ExternalDataValidator,
    SourceLicenseInfo,
    source_validation_result_to_dict,
)

EASTERN = ZoneInfo("America/New_York")
IBKR_NBBO_SOURCE = "ibkr_nbbo"
IBKR_PERSONAL_USE_LICENSE = SourceLicenseInfo(
    license_type="personal_use",
    redistribution_allowed=False,
    citation_required=True,
    documented_at="IBKR account historical market data license; personal use only, redistribution prohibited",
    cost_evidence_allowed=True,
)


@dataclass(frozen=True, slots=True)
class IbkrHistoricalConfig:
    paper_account: bool = True
    rate_limit_per_minute: int = 60
    chunk_size_seconds: int = 1800
    reqHistoricalTicks_count: int = 1000


@dataclass(frozen=True, slots=True)
class IbkrBackfillSummary:
    requested_symbols: int
    market_date_range: tuple[date, date]
    rows_inserted: int
    rows_duplicate: int
    rate_limit_hits: int
    failed_chunks: list[dict[str, Any]] = field(default_factory=list)
    consolidation_evidence: dict[str, Any] = field(default_factory=dict)


class _IbkrClient(Protocol):
    def reqHistoricalTicks(
        self,
        contract: Any,
        startDateTime: Any,
        endDateTime: Any,
        numberOfTicks: int,
        whatToShow: str,
        useRth: bool,
        ignoreSize: bool,
        miscOptions: list[Any],
    ) -> list[Any]:
        ...


def backfill_ibkr_historical_quotes(
    symbols: list[str],
    market_date_start: date,
    market_date_end: date,
    db_path: Path,
    config: IbkrHistoricalConfig,
) -> IbkrBackfillSummary:
    if market_date_end < market_date_start:
        raise ValueError("market_date_end must be on or after market_date_start.")
    if config.rate_limit_per_minute <= 0:
        raise ValueError("rate_limit_per_minute must be positive.")
    if config.chunk_size_seconds <= 0:
        raise ValueError("chunk_size_seconds must be positive.")
    if config.reqHistoricalTicks_count <= 0:
        raise ValueError("reqHistoricalTicks_count must be positive.")

    requested_symbols = _normalize_symbols(symbols)
    if not requested_symbols:
        raise ValueError("At least one symbol is required.")

    init_database(db_path)
    client = _create_ibkr_client(config)
    limiter = _RateLimiter(config.rate_limit_per_minute)
    existing_keys = _existing_ibkr_quote_keys(db_path, requested_symbols, market_date_start, market_date_end)
    pending_keys: set[tuple[str, str]] = set()
    pending: list[HistoricalL1Quote] = []
    duplicate_rows = 0
    rate_limit_hits = 0
    failed_chunks: list[dict[str, Any]] = []

    try:
        for market_day in _iter_market_dates(market_date_start, market_date_end):
            day_start, day_end = _market_day_window(market_day)
            for symbol in requested_symbols:
                contract = _contract_for_symbol(symbol)
                for chunk_start, chunk_end in _chunk_windows(
                    day_start,
                    day_end,
                    chunk_size_seconds=config.chunk_size_seconds,
                ):
                    chunk_quotes, chunk_rate_hits, chunk_error = _fetch_chunk_with_retry(
                        client=client,
                        contract=contract,
                        symbol=symbol,
                        chunk_start=chunk_start,
                        chunk_end=chunk_end,
                        config=config,
                        limiter=limiter,
                    )
                    rate_limit_hits += chunk_rate_hits
                    if chunk_error is not None:
                        failed_chunks.append(
                            {
                                "symbol": symbol,
                                "market_date": market_day.isoformat(),
                                "start_at": chunk_start.isoformat(),
                                "end_at": chunk_end.isoformat(),
                                "error": chunk_error,
                            }
                        )
                        continue
                    for quote in chunk_quotes:
                        key = _quote_key(quote)
                        if key in existing_keys or key in pending_keys:
                            duplicate_rows += 1
                            continue
                        pending_keys.add(key)
                        pending.append(quote)
        insert_historical_l1_quotes(db_path, pending)
    finally:
        _disconnect_if_possible(client)

    consolidation_evidence = _register_ibkr_source_policy(db_path)
    return IbkrBackfillSummary(
        requested_symbols=len(requested_symbols),
        market_date_range=(market_date_start, market_date_end),
        rows_inserted=len(pending),
        rows_duplicate=duplicate_rows,
        rate_limit_hits=rate_limit_hits,
        failed_chunks=failed_chunks,
        consolidation_evidence=consolidation_evidence,
    )


def ibkr_backfill_summary_to_dict(summary: IbkrBackfillSummary) -> dict[str, Any]:
    payload = asdict(summary)
    payload["market_date_range"] = [
        summary.market_date_range[0].isoformat(),
        summary.market_date_range[1].isoformat(),
    ]
    return payload


def _fetch_chunk_with_retry(
    *,
    client: _IbkrClient,
    contract: Any,
    symbol: str,
    chunk_start: datetime,
    chunk_end: datetime,
    config: IbkrHistoricalConfig,
    limiter: "_RateLimiter",
) -> tuple[list[HistoricalL1Quote], int, str | None]:
    rate_limit_hits = 0
    for attempt in range(3):
        try:
            limiter.wait()
            ticks = _fetch_chunk_ticks(
                client=client,
                contract=contract,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                config=config,
                limiter=limiter,
            )
            return (
                [
                    quote
                    for tick in ticks
                    if (quote := _quote_from_ibkr_tick(tick, symbol=symbol)) is not None
                ],
                rate_limit_hits,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - IBKR client raises heterogeneous errors.
            if _is_rate_limit_error(exc) and attempt < 2:
                rate_limit_hits += 1
                time_module.sleep(min(2.0 * (attempt + 1), 5.0))
                continue
            return [], rate_limit_hits, str(exc)
    return [], rate_limit_hits, "ibkr_rate_limit_retry_exhausted"


def _fetch_chunk_ticks(
    *,
    client: _IbkrClient,
    contract: Any,
    chunk_start: datetime,
    chunk_end: datetime,
    config: IbkrHistoricalConfig,
    limiter: "_RateLimiter",
) -> list[Any]:
    ticks: list[Any] = []
    cursor = chunk_start
    seen_last_key: str | None = None
    pages = 0
    while cursor < chunk_end:
        page = client.reqHistoricalTicks(
            contract,
            cursor,
            chunk_end,
            config.reqHistoricalTicks_count,
            "BID_ASK",
            False,
            True,
            [],
        )
        pages += 1
        if not page:
            break
        ticks.extend(page)
        if len(page) < config.reqHistoricalTicks_count:
            break
        last_timestamp = _tick_timestamp(page[-1])
        last_key = last_timestamp.isoformat() if last_timestamp is not None else None
        if last_timestamp is None or last_key == seen_last_key:
            break
        seen_last_key = last_key
        cursor = last_timestamp + timedelta(microseconds=1)
        if cursor >= chunk_end:
            break
        if pages >= 100:
            raise RuntimeError("ibkr_chunk_pagination_limit_exceeded")
        limiter.wait()
    return ticks


def _quote_from_ibkr_tick(tick: Any, *, symbol: str) -> HistoricalL1Quote | None:
    timestamp = _tick_timestamp(tick)
    bid = _first_float(tick, "priceBid", "bidPrice", "bid", "bp")
    ask = _first_float(tick, "priceAsk", "askPrice", "ask", "ap")
    if timestamp is None or (bid is None and ask is None):
        return None
    return HistoricalL1Quote(
        symbol=symbol,
        market_date=timestamp.astimezone(EASTERN).date().isoformat(),
        timestamp=timestamp.astimezone(timezone.utc),
        bid_price=bid,
        ask_price=ask,
        bid_exchange=_first_text(tick, "bidExchange", "exchangeBid", "bid_exchange"),
        ask_exchange=_first_text(tick, "askExchange", "exchangeAsk", "ask_exchange"),
        last_price=None,
        source=IBKR_NBBO_SOURCE,
        subscription_continuous=True,
    )


def _tick_timestamp(tick: Any) -> datetime | None:
    value = _field(tick, "time")
    if value is None:
        value = _field(tick, "timestamp")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(timezone.utc)


def _register_ibkr_source_policy(db_path: Path) -> dict[str, Any]:
    validator = ExternalDataValidator()
    result = validator.validate(
        source_name=IBKR_NBBO_SOURCE,
        sample_db_path=db_path,
        license_info=IBKR_PERSONAL_USE_LICENSE,
    )
    validator.register_to_policy(result)
    return source_validation_result_to_dict(result)


def _existing_ibkr_quote_keys(
    db_path: Path,
    symbols: tuple[str, ...],
    market_date_start: date,
    market_date_end: date,
) -> set[tuple[str, str]]:
    placeholders = ", ".join("?" for _ in symbols)
    params: list[Any] = [
        IBKR_NBBO_SOURCE,
        market_date_start.isoformat(),
        market_date_end.isoformat(),
        *symbols,
    ]
    with get_connection(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT symbol, quote_at
            FROM historical_l1_quotes
            WHERE source = ?
              AND market_date BETWEEN ? AND ?
              AND symbol IN ({placeholders})
            """,
            tuple(params),
        ).fetchall()
    keys: set[tuple[str, str]] = set()
    for row in rows:
        timestamp = _parse_timestamp(str(row["quote_at"]))
        normalized = timestamp.isoformat() if timestamp is not None else str(row["quote_at"])
        keys.add((str(row["symbol"]).strip().upper(), normalized))
    return keys


def _quote_key(quote: HistoricalL1Quote) -> tuple[str, str]:
    return (quote.symbol.upper(), quote.timestamp.astimezone(timezone.utc).isoformat())


def _create_ibkr_client(config: IbkrHistoricalConfig) -> _IbkrClient:
    try:
        from ib_insync import IB  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra.
        raise RuntimeError("Install optional IBKR support with: pip install .[ibkr]") from exc

    host = os.getenv("PENNY_STOCK_IBKR_HOST", "127.0.0.1")
    default_port = "7497" if config.paper_account else "7496"
    port = int(os.getenv("PENNY_STOCK_IBKR_PORT", default_port))
    client_id = int(os.getenv("PENNY_STOCK_IBKR_CLIENT_ID", "37"))
    client = IB()
    client.connect(host, port, clientId=client_id)
    return client


def _contract_for_symbol(symbol: str) -> Any:
    try:
        from ib_insync import Stock  # type: ignore
    except ImportError:
        return symbol
    return Stock(symbol, "SMART", "USD")


def _disconnect_if_possible(client: Any) -> None:
    disconnect = getattr(client, "disconnect", None)
    if callable(disconnect):
        disconnect()


def _iter_market_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _market_day_window(market_day: date) -> tuple[datetime, datetime]:
    start_at = datetime.combine(market_day, time(4, 0), tzinfo=EASTERN)
    end_at = datetime.combine(market_day, time(20, 0), tzinfo=EASTERN)
    return start_at, end_at


def _chunk_windows(
    start_at: datetime,
    end_at: datetime,
    *,
    chunk_size_seconds: int,
) -> Iterable[tuple[datetime, datetime]]:
    cursor = start_at
    step = timedelta(seconds=chunk_size_seconds)
    while cursor < end_at:
        chunk_end = min(end_at, cursor + step)
        yield cursor, chunk_end
        cursor = chunk_end


def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _first_float(value: Any, *names: str) -> float | None:
    for name in names:
        raw = _field(value, name)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _first_text(value: Any, *names: str) -> str | None:
    for name in names:
        raw = _field(value, name)
        text = str(raw or "").strip().upper()
        if text:
            return text
    return None


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("rate limit", "pacing violation", "error 162", "too many requests"))


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._request_times: list[float] = []

    def wait(self) -> None:
        now = time_module.monotonic()
        cutoff = now - 60.0
        self._request_times = [timestamp for timestamp in self._request_times if timestamp > cutoff]
        if len(self._request_times) >= self.per_minute:
            sleep_for = max(0.0, 60.0 - (now - self._request_times[0]))
            if sleep_for > 0:
                time_module.sleep(sleep_for)
            now = time_module.monotonic()
            cutoff = now - 60.0
            self._request_times = [timestamp for timestamp in self._request_times if timestamp > cutoff]
        self._request_times.append(now)
