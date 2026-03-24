from __future__ import annotations

from datetime import datetime

import httpx

from penny_stock_radar.config import AppSettings
from penny_stock_radar.providers.live_market import (
    AlpacaLiveMarketProvider,
    NullLiveMarketProvider,
    PolygonLiveMarketProvider,
    build_live_market_provider,
)


def test_build_live_market_provider_falls_back_without_credentials() -> None:
    settings = AppSettings(
        live_market_provider="auto",
        polygon_api_key=None,
        alpaca_api_key=None,
        alpaca_secret_key=None,
    )

    provider = build_live_market_provider(settings)

    assert isinstance(provider, NullLiveMarketProvider)
    assert provider.is_available() is False
    assert provider.latest_trade("AAA") is None
    assert provider.latest_quote("AAA") is None
    assert provider.latest_snapshot("AAA") is None


def test_polygon_live_provider_parses_rest_payloads() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/v2/last/trade/AAA"):
            return httpx.Response(
                200,
                json={
                    "results": {
                        "p": 1.23,
                        "s": 450,
                        "t": "2026-03-24T00:01:02Z",
                        "x": "P",
                        "c": ["@", "T"],
                    }
                },
            )
        if request.url.path.endswith("/v2/last/nbbo/AAA"):
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bp": 1.20,
                        "ap": 1.25,
                        "bs": 120,
                        "as": 140,
                        "t": "2026-03-24T00:01:03Z",
                        "x": "P",
                    }
                },
            )
        if request.url.path.endswith("/v2/snapshot/locale/us/markets/stocks/tickers/AAA"):
            return httpx.Response(
                200,
                json={
                    "ticker": {
                        "lastTrade": {"p": 1.24, "s": 500, "t": "2026-03-24T00:01:04Z"},
                        "lastQuote": {"bp": 1.21, "ap": 1.26, "bs": 100, "as": 200, "t": "2026-03-24T00:01:05Z"},
                        "marketStatus": "open",
                    }
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.polygon.io")
    provider = PolygonLiveMarketProvider(api_key="polygon-test", client=client)

    trade = provider.latest_trade("AAA")
    quote = provider.latest_quote("AAA")
    snapshot = provider.latest_snapshot("AAA")

    assert trade is not None and trade.price == 1.23 and trade.source == "polygon"
    assert quote is not None and quote.ask_price == 1.25 and quote.bid_size == 120
    assert snapshot is not None and snapshot.market_status == "open"
    assert snapshot.latest_trade is not None and snapshot.latest_trade.price == 1.24
    assert snapshot.latest_quote is not None and snapshot.latest_quote.ask_price == 1.26
    assert any(path.endswith("/v2/last/trade/AAA") for path, _ in requests)
    assert all(params.get("apiKey") == "polygon-test" for _, params in requests)


def test_alpaca_live_provider_parses_rest_payloads() -> None:
    requests: list[tuple[str, dict[str, str], httpx.Headers]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, dict(request.url.params), request.headers))
        if request.url.path.endswith("/v2/stocks/AAA/trades/latest"):
            return httpx.Response(
                200,
                json={
                    "trade": {
                        "p": 2.34,
                        "s": 275,
                        "t": "2026-03-24T00:02:02Z",
                        "x": "V",
                    }
                },
            )
        if request.url.path.endswith("/v2/stocks/AAA/quotes/latest"):
            return httpx.Response(
                200,
                json={
                    "quote": {
                        "bp": 2.30,
                        "ap": 2.40,
                        "bs": 80,
                        "as": 90,
                        "t": "2026-03-24T00:02:03Z",
                    }
                },
            )
        if request.url.path.endswith("/v2/stocks/AAA/snapshot"):
            return httpx.Response(
                200,
                json={
                    "latestTrade": {
                        "p": 2.35,
                        "s": 300,
                        "t": "2026-03-24T00:02:04Z",
                    },
                    "latestQuote": {
                        "bp": 2.31,
                        "ap": 2.41,
                        "bs": 75,
                        "as": 85,
                        "t": "2026-03-24T00:02:05Z",
                    },
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://data.alpaca.markets")
    provider = AlpacaLiveMarketProvider(
        api_key="alpaca-key",
        api_secret="alpaca-secret",
        client=client,
        feed="sip",
    )

    trade = provider.latest_trade("AAA")
    quote = provider.latest_quote("AAA")
    snapshot = provider.latest_snapshot("AAA")

    assert trade is not None and trade.price == 2.34 and trade.source == "alpaca"
    assert quote is not None and quote.ask_price == 2.40 and quote.bid_size == 80
    assert snapshot is not None and snapshot.latest_trade is not None
    assert snapshot.latest_trade.price == 2.35
    assert snapshot.latest_quote is not None and snapshot.latest_quote.ask_price == 2.41
    assert any(path.endswith("/v2/stocks/AAA/trades/latest") for path, _, _ in requests)
    assert any(headers.get("APCA-API-KEY-ID") == "alpaca-key" for _, _, headers in requests)
