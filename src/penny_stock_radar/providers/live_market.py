from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
import pandas as pd

from ..config import AppSettings


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Treat large integer timestamps as milliseconds.
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _first_mapping(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        candidate = data.get(key)
        if isinstance(candidate, dict):
            return candidate
    return {}


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


@dataclass(slots=True)
class LiveTrade:
    symbol: str
    price: float | None
    size: int | None
    timestamp: datetime | None
    exchange: str | None = None
    conditions: list[str] = field(default_factory=list)
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LiveQuote:
    symbol: str
    bid_price: float | None
    ask_price: float | None
    bid_size: int | None
    ask_size: int | None
    timestamp: datetime | None
    exchange: str | None = None
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LiveSnapshot:
    symbol: str
    source: str
    latest_trade: LiveTrade | None = None
    latest_quote: LiveQuote | None = None
    market_status: str | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LiveMarketProvider(Protocol):
    source_name: str

    def is_available(self) -> bool: ...

    def latest_trade(self, symbol: str) -> LiveTrade | None: ...

    def latest_quote(self, symbol: str) -> LiveQuote | None: ...

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None: ...


class NullLiveMarketProvider:
    source_name = "null"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def latest_trade(self, symbol: str) -> LiveTrade | None:
        return None

    def latest_quote(self, symbol: str) -> LiveQuote | None:
        return None

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        return None


class _BaseHttpLiveMarketProvider:
    source_name = "http"

    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout_seconds)

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def __enter__(self):  # pragma: no cover - convenience helper
        return self

    def __exit__(self, exc_type, exc, tb):  # pragma: no cover - convenience helper
        self.close()

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        try:
            response = self._client.get(path, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def is_available(self) -> bool:
        return True


class PolygonLiveMarketProvider(_BaseHttpLiveMarketProvider):
    source_name = "polygon"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key
        super().__init__(base_url=base_url, client=client, timeout_seconds=timeout_seconds)

    def latest_trade(self, symbol: str) -> LiveTrade | None:
        data = self._get_json(f"/v2/last/trade/{symbol}", params={"apiKey": self.api_key})
        if not data:
            return None
        payload = _first_mapping(data, "results", "trade", "last", "lastTrade")
        if not payload:
            payload = data
        trade = LiveTrade(
            symbol=symbol,
            price=_coerce_float(_first_value(payload, "price", "p", "last_price")),
            size=_coerce_int(_first_value(payload, "size", "s", "last_size")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t", "sip_timestamp")),
            exchange=str(_first_value(payload, "exchange", "x", "exchange_id")) if _first_value(payload, "exchange", "x", "exchange_id") is not None else None,
            conditions=_normalize_conditions(_first_value(payload, "conditions", "c")),
            source=self.source_name,
            raw=data,
        )
        return trade

    def latest_quote(self, symbol: str) -> LiveQuote | None:
        data = self._get_json(f"/v2/last/nbbo/{symbol}", params={"apiKey": self.api_key})
        if not data:
            return None
        payload = _first_mapping(data, "results", "quote", "last", "lastQuote")
        if not payload:
            payload = data
        quote = LiveQuote(
            symbol=symbol,
            bid_price=_coerce_float(_first_value(payload, "bid_price", "bp", "bid")),
            ask_price=_coerce_float(_first_value(payload, "ask_price", "ap", "ask")),
            bid_size=_coerce_int(_first_value(payload, "bid_size", "bs", "bid_size_lots")),
            ask_size=_coerce_int(_first_value(payload, "ask_size", "as", "ask_size_lots")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t")),
            exchange=str(_first_value(payload, "exchange", "x", "exchange_id")) if _first_value(payload, "exchange", "x", "exchange_id") is not None else None,
            source=self.source_name,
            raw=data,
        )
        return quote

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        data = self._get_json(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
            params={"apiKey": self.api_key},
        )
        if not data:
            return None
        payload = _first_mapping(data, "ticker", "results", "snapshot")
        if not payload:
            payload = data
        latest_trade = self._parse_trade_payload(symbol, payload, data)
        latest_quote = self._parse_quote_payload(symbol, payload, data)
        return LiveSnapshot(
            symbol=symbol,
            source=self.source_name,
            latest_trade=latest_trade,
            latest_quote=latest_quote,
            market_status=str(_first_value(payload, "marketStatus", "market_status")) if _first_value(payload, "marketStatus", "market_status") is not None else None,
            updated_at=_coerce_datetime(_first_value(payload, "updated", "updatedAt", "last_updated")),
            raw=data,
        )

    def _parse_trade_payload(
        self, symbol: str, payload: dict[str, Any], raw: dict[str, Any]
    ) -> LiveTrade | None:
        trade_payload = _first_mapping(payload, "lastTrade", "trade", "last_trade")
        if not trade_payload:
            trade_payload = payload
        if not trade_payload:
            return None
        return LiveTrade(
            symbol=symbol,
            price=_coerce_float(_first_value(trade_payload, "price", "p")),
            size=_coerce_int(_first_value(trade_payload, "size", "s")),
            timestamp=_coerce_datetime(_first_value(trade_payload, "timestamp", "t")),
            exchange=str(_first_value(trade_payload, "exchange", "x")) if _first_value(trade_payload, "exchange", "x") is not None else None,
            conditions=_normalize_conditions(_first_value(trade_payload, "conditions", "c")),
            source=self.source_name,
            raw=raw,
        )

    def _parse_quote_payload(
        self, symbol: str, payload: dict[str, Any], raw: dict[str, Any]
    ) -> LiveQuote | None:
        quote_payload = _first_mapping(payload, "lastQuote", "quote", "last_quote")
        if not quote_payload:
            quote_payload = payload
        if not quote_payload:
            return None
        return LiveQuote(
            symbol=symbol,
            bid_price=_coerce_float(_first_value(quote_payload, "bid_price", "bid", "bp")),
            ask_price=_coerce_float(_first_value(quote_payload, "ask_price", "ask", "ap")),
            bid_size=_coerce_int(_first_value(quote_payload, "bid_size", "bs")),
            ask_size=_coerce_int(_first_value(quote_payload, "ask_size", "as")),
            timestamp=_coerce_datetime(_first_value(quote_payload, "timestamp", "t")),
            exchange=str(_first_value(quote_payload, "exchange", "x")) if _first_value(quote_payload, "exchange", "x") is not None else None,
            source=self.source_name,
            raw=raw,
        )


class AlpacaLiveMarketProvider(_BaseHttpLiveMarketProvider):
    source_name = "alpaca"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://data.alpaca.markets",
        feed: str = "iex",
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.feed = feed
        self._auth_headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }
        if client is None:
            client = httpx.Client(
                base_url=base_url.rstrip("/"),
                headers=self._auth_headers,
                timeout=timeout_seconds,
            )
        else:
            client.headers.update(self._auth_headers)
        super().__init__(base_url=base_url, client=client, timeout_seconds=timeout_seconds)

    def latest_trade(self, symbol: str) -> LiveTrade | None:
        data = self._get_json(
            f"/v2/stocks/{symbol}/trades/latest",
            params={"feed": self.feed},
            headers=self._auth_headers,
        )
        if not data:
            return None
        payload = _first_mapping(data, "trade", "latestTrade", "result", "results")
        if not payload:
            payload = data
        return LiveTrade(
            symbol=symbol,
            price=_coerce_float(_first_value(payload, "price", "p")),
            size=_coerce_int(_first_value(payload, "size", "s")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t", "tape_timestamp")),
            exchange=str(_first_value(payload, "exchange", "x")) if _first_value(payload, "exchange", "x") is not None else None,
            conditions=_normalize_conditions(_first_value(payload, "conditions", "c")),
            source=self.source_name,
            raw=data,
        )

    def latest_quote(self, symbol: str) -> LiveQuote | None:
        data = self._get_json(
            f"/v2/stocks/{symbol}/quotes/latest",
            params={"feed": self.feed},
            headers=self._auth_headers,
        )
        if not data:
            return None
        payload = _first_mapping(data, "quote", "latestQuote", "result", "results")
        if not payload:
            payload = data
        return LiveQuote(
            symbol=symbol,
            bid_price=_coerce_float(_first_value(payload, "bid_price", "bp", "bid")),
            ask_price=_coerce_float(_first_value(payload, "ask_price", "ap", "ask")),
            bid_size=_coerce_int(_first_value(payload, "bid_size", "bs")),
            ask_size=_coerce_int(_first_value(payload, "ask_size", "as")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t")),
            exchange=str(_first_value(payload, "exchange", "x")) if _first_value(payload, "exchange", "x") is not None else None,
            source=self.source_name,
            raw=data,
        )

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        data = self._get_json(
            f"/v2/stocks/{symbol}/snapshot",
            params={"feed": self.feed},
            headers=self._auth_headers,
        )
        if not data:
            return None
        latest_trade = self._parse_trade_payload(symbol, data)
        latest_quote = self._parse_quote_payload(symbol, data)
        return LiveSnapshot(
            symbol=symbol,
            source=self.source_name,
            latest_trade=latest_trade,
            latest_quote=latest_quote,
            market_status=str(_first_value(data, "marketStatus", "market_status")) if _first_value(data, "marketStatus", "market_status") is not None else None,
            updated_at=_coerce_datetime(_first_value(data, "updated", "updatedAt")),
            raw=data,
        )

    def _parse_trade_payload(self, symbol: str, data: dict[str, Any]) -> LiveTrade | None:
        payload = _first_mapping(data, "latestTrade", "trade", "result", "results")
        if not payload:
            payload = data
        if not payload:
            return None
        return LiveTrade(
            symbol=symbol,
            price=_coerce_float(_first_value(payload, "price", "p")),
            size=_coerce_int(_first_value(payload, "size", "s")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t")),
            exchange=str(_first_value(payload, "exchange", "x")) if _first_value(payload, "exchange", "x") is not None else None,
            conditions=_normalize_conditions(_first_value(payload, "conditions", "c")),
            source=self.source_name,
            raw=data,
        )

    def _parse_quote_payload(self, symbol: str, data: dict[str, Any]) -> LiveQuote | None:
        payload = _first_mapping(data, "latestQuote", "quote", "result", "results")
        if not payload:
            payload = data
        if not payload:
            return None
        return LiveQuote(
            symbol=symbol,
            bid_price=_coerce_float(_first_value(payload, "bid_price", "bp", "bid")),
            ask_price=_coerce_float(_first_value(payload, "ask_price", "ap", "ask")),
            bid_size=_coerce_int(_first_value(payload, "bid_size", "bs")),
            ask_size=_coerce_int(_first_value(payload, "ask_size", "as")),
            timestamp=_coerce_datetime(_first_value(payload, "timestamp", "t")),
            exchange=str(_first_value(payload, "exchange", "x")) if _first_value(payload, "exchange", "x") is not None else None,
            source=self.source_name,
            raw=data,
        )


def _normalize_conditions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)]


def build_live_market_provider(
    settings: AppSettings,
    client: httpx.Client | None = None,
) -> LiveMarketProvider:
    mode = settings.live_market_provider.lower()

    if mode == "disabled":
        return NullLiveMarketProvider("Live market data disabled in settings.")

    if mode in {"auto", "polygon"} and settings.polygon_api_key:
        return PolygonLiveMarketProvider(
            api_key=settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            client=client,
            timeout_seconds=settings.live_market_timeout_seconds,
        )

    if mode in {"auto", "alpaca"} and settings.alpaca_api_key and settings.alpaca_secret_key:
        return AlpacaLiveMarketProvider(
            api_key=settings.alpaca_api_key,
            api_secret=settings.alpaca_secret_key,
            base_url=settings.alpaca_data_base_url,
            feed=settings.alpaca_market_data_feed,
            client=client,
            timeout_seconds=settings.live_market_timeout_seconds,
        )

    if mode == "polygon":
        return NullLiveMarketProvider("Polygon API key is missing.")
    if mode == "alpaca":
        return NullLiveMarketProvider("Alpaca API credentials are missing.")
    return NullLiveMarketProvider("No live market data provider is configured.")
