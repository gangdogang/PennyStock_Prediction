from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx
from ..config import AppSettings
from ..observability.metrics import build_live_metric_emitter
from ..kis_auth import load_cached_token, store_cached_token
from .kis_time import parse_kis_market_timestamp


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
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_kis_quote_datetime(
    date_value: Any,
    time_value: Any,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    return parse_kis_market_timestamp(date_value, time_value, now_utc=now_utc)


def _normalize_kis_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


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
    previous_close: float | None = None
    volume: float | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LiveMover:
    symbol: str
    source: str
    price: float | None = None
    pct_change: float | None = None
    volume: float | None = None
    trade_count: int | None = None
    rank: int = 0
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LiveMarketProvider(Protocol):
    source_name: str

    def is_available(self) -> bool: ...

    def latest_trade(self, symbol: str) -> LiveTrade | None: ...

    def latest_quote(self, symbol: str) -> LiveQuote | None: ...

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None: ...

    def top_gainers(self, limit: int = 20) -> list[LiveMover]: ...

    def most_active(self, limit: int = 20) -> list[LiveMover]: ...


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

    def top_gainers(self, limit: int = 20) -> list[LiveMover]:
        return []

    def most_active(self, limit: int = 20) -> list[LiveMover]:
        return []


class _BaseHttpLiveMarketProvider:
    source_name = "http"

    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        metrics_emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout_seconds)
        self._metrics_emitter = metrics_emitter

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
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            response = self._client.get(path, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._emit_metric(
                "provider_request",
                {
                    "provider": self.source_name,
                    "method": "GET",
                    "path": path,
                    "symbol": params.get("symbol") if params else None,
                    "ok": False,
                    "status_code": response.status_code if response is not None else None,
                    "error_code": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                },
            )
            return None
        self._emit_metric(
            "provider_request",
            {
                "provider": self.source_name,
                "method": "GET",
                "path": path,
                "symbol": params.get("symbol") if params else None,
                "ok": isinstance(data, dict),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
        )
        return data if isinstance(data, dict) else None

    def is_available(self) -> bool:
        return True

    def _emit_metric(self, metric_type: str, payload: dict[str, Any]) -> None:
        if self._metrics_emitter is None:
            return
        self._metrics_emitter(metric_type, payload)


class KISLiveMarketProvider(_BaseHttpLiveMarketProvider):
    source_name = "kis"
    _us_exchanges = ("NAS", "NYS", "AMS")

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str = "https://openapi.koreainvestment.com:9443",
        market_div_code: str = "J",
        custtype: str = "P",
        token_cache_path: Path | None = None,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        metrics_emitter: Callable[[str, dict[str, Any]], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.market_div_code = market_div_code.upper()
        self.custtype = custtype.upper()
        self.token_cache_path = token_cache_path
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._symbol_exchange_cache: dict[str, str] = {}
        self._now_fn = now_fn or _utc_now
        super().__init__(
            base_url=base_url,
            client=client,
            timeout_seconds=timeout_seconds,
            metrics_emitter=metrics_emitter,
        )

    def latest_trade(self, symbol: str) -> LiveTrade | None:
        normalized_symbol = _normalize_kis_symbol(symbol)
        exchange = self._resolve_exchange(normalized_symbol)
        if not exchange:
            return None
        price_data = self._fetch_price(normalized_symbol, exchange)
        payload = _first_mapping(price_data or {}, "output")
        if not payload:
            return None
        return LiveTrade(
            symbol=normalized_symbol,
            price=_coerce_float(_first_value(payload, "last")),
            size=None,
            timestamp=_utc_now(),
            exchange=exchange,
            source=self.source_name,
            raw={"exchange": exchange, "price": price_data},
        )

    def latest_quote(self, symbol: str) -> LiveQuote | None:
        normalized_symbol = _normalize_kis_symbol(symbol)
        exchange = self._resolve_exchange(normalized_symbol)
        if not exchange:
            return None
        quote_data = self._fetch_quote(normalized_symbol, exchange)
        return self._build_quote(normalized_symbol, exchange, quote_data)

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        normalized_symbol = _normalize_kis_symbol(symbol)
        exchange = self._resolve_exchange(normalized_symbol)
        if not exchange:
            return None

        price_data = self._fetch_price(normalized_symbol, exchange)
        detail_data = self._fetch_price_detail(normalized_symbol, exchange)
        quote_data = self._fetch_quote(normalized_symbol, exchange)
        if not price_data and not detail_data and not quote_data:
            return None

        price_payload = _first_mapping(price_data or {}, "output")
        detail_payload = _first_mapping(detail_data or {}, "output")
        quote_meta = _first_mapping(quote_data or {}, "output1")
        quote_book = _first_mapping(quote_data or {}, "output2", "output1")

        previous_close = _coerce_float(_first_value(detail_payload, "base"))
        if previous_close is None:
            previous_close = _coerce_float(_first_value(price_payload, "base"))

        volume = _coerce_float(_first_value(detail_payload, "tvol"))
        if volume is None:
            volume = _coerce_float(_first_value(price_payload, "tvol"))

        updated_at = _coerce_kis_quote_datetime(
            _first_value(quote_meta, "dymd"),
            _first_value(quote_meta, "dhms"),
            now_utc=self._now_fn(),
        )
        if updated_at is None:
            updated_at = self._now_fn()

        last_price = _coerce_float(_first_value(price_payload, "last"))
        if last_price is None:
            last_price = _coerce_float(_first_value(detail_payload, "last"))

        latest_trade = LiveTrade(
            symbol=normalized_symbol,
            price=last_price,
            size=None,
            timestamp=updated_at,
            exchange=exchange,
            source=self.source_name,
            raw={
                "exchange": exchange,
                "price": price_data,
                "price_detail": detail_data,
            },
        )
        latest_quote = self._build_quote(
            normalized_symbol,
            exchange,
            quote_data,
            default_timestamp=updated_at,
        )
        raw = self._build_snapshot_raw(
            exchange=exchange,
            price_data=price_data,
            detail_data=detail_data,
            quote_data=quote_data,
            last_price=last_price,
            previous_close=previous_close,
            volume=volume,
            updated_at=updated_at,
        )
        return LiveSnapshot(
            symbol=normalized_symbol,
            source=self.source_name,
            latest_trade=latest_trade if latest_trade.price is not None else None,
            latest_quote=latest_quote,
            market_status=self._market_status(price_payload, detail_payload, quote_book),
            previous_close=previous_close,
            volume=volume,
            updated_at=updated_at,
            raw=raw,
        )

    def top_gainers(self, limit: int = 20) -> list[LiveMover]:
        rows: list[LiveMover] = []
        for exchange in self._preferred_exchanges():
            data = self._get_kis_json(
                "/uapi/overseas-stock/v1/ranking/updown-rate",
                tr_id="HHDFS76290000",
                params={
                    "EXCD": exchange,
                    "NDAY": "0",
                    "GUBN": "1",
                    "VOL_RANG": "0",
                    "AUTH": "",
                    "KEYB": "",
                },
            )
            rows.extend(self._build_ranked_movers(data, exchange=exchange))
        rows.sort(
            key=lambda mover: (
                -(mover.pct_change if mover.pct_change is not None else float("-inf")),
                -(mover.volume if mover.volume is not None else float("-inf")),
                mover.symbol,
            )
        )
        return self._finalize_global_ranks(rows, limit=limit)

    def most_active(self, limit: int = 20) -> list[LiveMover]:
        rows: list[LiveMover] = []
        for exchange in self._preferred_exchanges():
            data = self._get_kis_json(
                "/uapi/overseas-stock/v1/ranking/trade-vol",
                tr_id="HHDFS76310010",
                params={
                    "EXCD": exchange,
                    "NDAY": "0",
                    "VOL_RANG": "0",
                    "KEYB": "",
                    "AUTH": "",
                    "PRC1": "",
                    "PRC2": "",
                },
            )
            rows.extend(self._build_ranked_movers(data, exchange=exchange))
        rows.sort(
            key=lambda mover: (
                -(mover.volume if mover.volume is not None else float("-inf")),
                -(mover.pct_change if mover.pct_change is not None else float("-inf")),
                mover.symbol,
            )
        )
        return self._finalize_global_ranks(rows, limit=limit)

    def _build_quote(
        self,
        symbol: str,
        exchange: str,
        data: dict[str, Any] | None,
        *,
        default_timestamp: datetime | None = None,
    ) -> LiveQuote | None:
        if not data:
            return None
        meta_payload = _first_mapping(data, "output1")
        book_payload = _first_mapping(data, "output2", "output1")
        if not book_payload:
            return None
        timestamp = _coerce_kis_quote_datetime(
            _first_value(meta_payload, "dymd"),
            _first_value(meta_payload, "dhms"),
            now_utc=self._now_fn(),
        )
        return LiveQuote(
            symbol=symbol,
            bid_price=_coerce_float(_first_value(book_payload, "pbid1")),
            ask_price=_coerce_float(_first_value(book_payload, "pask1")),
            bid_size=_coerce_int(_first_value(book_payload, "vbid1")),
            ask_size=_coerce_int(_first_value(book_payload, "vask1")),
            timestamp=timestamp or default_timestamp,
            exchange=exchange,
            source=self.source_name,
            raw={"exchange": exchange, "quote": data},
        )

    def _build_ranked_movers(
        self,
        data: dict[str, Any] | None,
        *,
        exchange: str,
    ) -> list[LiveMover]:
        if not data:
            return []
        output = data.get("output2")
        if output is None:
            output = data.get("output")
        rows_data = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
        updated_at = _utc_now()
        rows: list[LiveMover] = []
        for item in rows_data:
            if not isinstance(item, dict):
                continue
            symbol = _normalize_kis_symbol(_first_value(item, "symb", "code", "rsym"))
            if not symbol:
                continue
            rows.append(
                LiveMover(
                    symbol=symbol,
                    source=self.source_name,
                    price=_coerce_float(item.get("last")),
                    pct_change=_coerce_float(item.get("rate")),
                    volume=_coerce_float(item.get("tvol")),
                    trade_count=None,
                    rank=_coerce_int(item.get("rank")) or 0,
                    updated_at=updated_at,
                    raw={"exchange": exchange, "data": item},
                )
            )
        return rows

    def _finalize_global_ranks(self, rows: list[LiveMover], *, limit: int) -> list[LiveMover]:
        deduped: list[LiveMover] = []
        seen: set[str] = set()
        for mover in rows:
            if mover.symbol in seen:
                continue
            seen.add(mover.symbol)
            deduped.append(mover)
            if len(deduped) >= max(int(limit), 1):
                break
        for index, mover in enumerate(deduped, start=1):
            mover.rank = index
        return deduped

    def _build_snapshot_raw(
        self,
        *,
        exchange: str,
        price_data: dict[str, Any] | None,
        detail_data: dict[str, Any] | None,
        quote_data: dict[str, Any] | None,
        last_price: float | None,
        previous_close: float | None,
        volume: float | None,
        updated_at: datetime | None,
    ) -> dict[str, Any]:
        day_block = {"c": last_price, "v": volume}
        prev_day_block = {"c": previous_close}
        raw: dict[str, Any] = {
            "ticker": {
                "day": day_block,
                "prevDay": prev_day_block,
            },
            "day": day_block,
            "prevDay": prev_day_block,
            "previous_close": previous_close,
            "volume": volume,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "kis": {"exchange": exchange},
        }
        if price_data:
            raw["kis"]["price"] = price_data
        if detail_data:
            raw["kis"]["price_detail"] = detail_data
        if quote_data:
            raw["kis"]["quote"] = quote_data
        return raw

    def _market_status(
        self,
        price_payload: dict[str, Any],
        detail_payload: dict[str, Any],
        quote_payload: dict[str, Any],
    ) -> str | None:
        has_live_fields = any(
            value is not None
            for value in (
                _coerce_float(_first_value(price_payload, "last")),
                _coerce_float(_first_value(detail_payload, "last")),
                _coerce_float(_first_value(quote_payload, "pbid1")),
                _coerce_float(_first_value(quote_payload, "pask1")),
            )
        )
        return "open" if has_live_fields else None

    def _fetch_price(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        return self._get_kis_json(
            "/uapi/overseas-price/v1/quotations/price",
            tr_id="HHDFS00000300",
            params=self._symbol_params(symbol, exchange),
        )

    def _fetch_price_detail(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        return self._get_kis_json(
            "/uapi/overseas-price/v1/quotations/price-detail",
            tr_id="HHDFS76200200",
            params=self._symbol_params(symbol, exchange),
        )

    def _fetch_quote(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        return self._get_kis_json(
            "/uapi/overseas-price/v1/quotations/inquire-asking-price",
            tr_id="HHDFS76200100",
            params=self._symbol_params(symbol, exchange),
        )

    def _symbol_params(self, symbol: str, exchange: str) -> dict[str, str]:
        return {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": _normalize_kis_symbol(symbol),
        }

    def _preferred_exchanges(self) -> tuple[str, ...]:
        explicit = self.market_div_code.strip().upper()
        if explicit in self._us_exchanges:
            return (explicit,) + tuple(exchange for exchange in self._us_exchanges if exchange != explicit)
        return self._us_exchanges

    def _resolve_exchange(self, symbol: str) -> str | None:
        normalized_symbol = _normalize_kis_symbol(symbol)
        cached = self._symbol_exchange_cache.get(normalized_symbol)
        if cached:
            return cached
        for exchange in self._preferred_exchanges():
            data = self._fetch_price(normalized_symbol, exchange)
            payload = _first_mapping(data or {}, "output")
            if payload and _first_value(payload, "last", "base", "rsym") is not None:
                self._symbol_exchange_cache[normalized_symbol] = exchange
                return exchange
        return None

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
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": self.custtype,
        }
        for attempt in range(3):
            started = time.perf_counter()
            response: httpx.Response | None = None
            try:
                response = self._client.get(path, params=params, headers=headers)
                data = response.json()
            except Exception as exc:
                self._emit_metric(
                    "provider_request",
                    {
                        "provider": self.source_name,
                        "method": "GET",
                        "path": path,
                        "symbol": params.get("SYMB") if params else None,
                        "exchange": params.get("EXCD") if params else None,
                        "tr_id": tr_id,
                        "attempt": attempt + 1,
                        "ok": False,
                        "status_code": response.status_code if response is not None else None,
                        "error_code": type(exc).__name__,
                        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    },
                )
                return None
            if not isinstance(data, dict):
                self._emit_metric(
                    "provider_request",
                    {
                        "provider": self.source_name,
                        "method": "GET",
                        "path": path,
                        "symbol": params.get("SYMB") if params else None,
                        "exchange": params.get("EXCD") if params else None,
                        "tr_id": tr_id,
                        "attempt": attempt + 1,
                        "ok": False,
                        "status_code": response.status_code if response is not None else None,
                        "error_code": "invalid_json_payload",
                        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    },
                )
                return None
            ok = str(data.get("rt_cd")) in {"0", ""}
            self._emit_metric(
                "provider_request",
                {
                    "provider": self.source_name,
                    "method": "GET",
                    "path": path,
                    "symbol": params.get("SYMB") if params else None,
                    "exchange": params.get("EXCD") if params else None,
                    "tr_id": tr_id,
                    "attempt": attempt + 1,
                    "ok": ok,
                    "status_code": response.status_code if response is not None else None,
                    "rt_cd": str(data.get("rt_cd") or ""),
                    "msg_cd": str(data.get("msg_cd") or ""),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                },
            )
            if str(data.get("msg_cd")) == "EGW00201" and attempt < 2:
                time.sleep(0.35 * (attempt + 1))
                continue
            if response is not None and response.is_success and ok:
                return data
            if ok:
                return data
            return None
        return None

    def _ensure_token(self) -> str | None:
        if self._access_token and self._token_expires_at and self._token_expires_at > _utc_now():
            return self._access_token
        if self.token_cache_path is not None:
            cached_token, cached_expires_at = load_cached_token(self.token_cache_path)
            if cached_token and cached_expires_at:
                self._access_token = cached_token
                self._token_expires_at = cached_expires_at
                return self._access_token
        try:
            response = self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
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
        expires_at = _coerce_datetime(
            payload.get("access_token_token_expired") or payload.get("access_token_expired")
        )
        if not token:
            return None
        self._access_token = str(token)
        self._token_expires_at = expires_at or _utc_now()
        if self.token_cache_path is not None:
            try:
                store_cached_token(
                    self.token_cache_path,
                    access_token=self._access_token,
                    expires_at=self._token_expires_at,
                )
            except OSError:
                pass
        return self._access_token


def build_live_market_provider(
    settings: AppSettings,
    client: httpx.Client | None = None,
) -> LiveMarketProvider:
    mode = settings.live_market_provider.lower()
    if mode == "auto":
        mode = "kis" if settings.kis_app_key and settings.kis_app_secret else "disabled"
    metrics_emitter = build_live_metric_emitter(settings)

    if mode == "disabled":
        return NullLiveMarketProvider("Live market data disabled in settings.")

    if mode == "kis":
        if not settings.kis_app_key or not settings.kis_app_secret:
            raise RuntimeError(
                "KIS API credentials are required when live_market_provider='kis'. "
                "Set PENNY_STOCK_KIS_APP_KEY and PENNY_STOCK_KIS_APP_SECRET, or use "
                "PENNY_STOCK_LIVE_MARKET_PROVIDER=disabled."
            )
        return KISLiveMarketProvider(
            app_key=settings.kis_app_key,
            app_secret=settings.kis_app_secret,
            base_url=settings.kis_base_url,
            market_div_code=settings.kis_market_div_code,
            custtype=settings.kis_custtype,
            token_cache_path=settings.cache_dir / "kis_access_token.json",
            client=client,
            timeout_seconds=settings.live_market_timeout_seconds,
            metrics_emitter=metrics_emitter,
        )

    raise ValueError(f"Unsupported live_market_provider: {mode!r}")
