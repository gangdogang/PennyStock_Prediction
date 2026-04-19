from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx
import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.providers.live_market import (
    KISLiveMarketProvider,
    NullLiveMarketProvider,
    build_live_market_provider,
)


def test_build_live_market_provider_disabled_returns_null() -> None:
    settings = AppSettings(
        live_market_provider="disabled",
    )

    provider = build_live_market_provider(settings)

    assert isinstance(provider, NullLiveMarketProvider)
    assert provider.is_available() is False
    assert provider.latest_trade("AAA") is None
    assert provider.latest_quote("AAA") is None
    assert provider.latest_snapshot("AAA") is None


def test_build_live_market_provider_kis_without_credentials_raises() -> None:
    settings = AppSettings(
        live_market_provider="kis",
        kis_app_key=None,
        kis_app_secret=None,
    )

    with pytest.raises(RuntimeError, match="KIS API credentials are required"):
        build_live_market_provider(settings)


def test_build_live_market_provider_kis_with_credentials_returns_kis_provider() -> None:
    settings = AppSettings(
        live_market_provider="kis",
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
    )

    provider = build_live_market_provider(settings)

    assert isinstance(provider, KISLiveMarketProvider)


def test_kis_live_provider_parses_rest_payloads() -> None:
    requests: list[tuple[str, str, dict[str, str], httpx.Headers]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                request.headers,
            )
        )
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/price":
            exchange = request.url.params.get("EXCD")
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {
                        "rsym": f"{exchange}AAA",
                        "base": "1.90",
                        "last": "2.34",
                        "rate": "23.16",
                        "tvol": "123456",
                        "ordy": "Y",
                    },
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/price-detail":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {
                        "base": "1.90",
                        "last": "2.34",
                        "tvol": "123456",
                        "e_ordyn": "Y",
                    },
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-asking-price":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {
                        "dymd": "20260324",
                        "dhms": "091011",
                        "last": "2.34",
                    },
                    "output2": {
                        "pask1": "2.35",
                        "pbid1": "2.33",
                        "vask1": "900",
                        "vbid1": "800",
                    },
                    "output3": {},
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/ranking/updown-rate":
            exchange = request.url.params.get("EXCD")
            rows = {
                "NAS": [{"symb": "AAA", "last": "2.34", "rate": "23.16", "tvol": "123456", "rank": "1"}],
                "NYS": [{"symb": "BBB", "last": "3.10", "rate": "15.00", "tvol": "50000", "rank": "1"}],
                "AMS": [{"symb": "CCC", "last": "1.75", "rate": "12.00", "tvol": "40000", "rank": "1"}],
            }[str(exchange)]
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"exchange": exchange},
                    "output2": rows,
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/ranking/trade-vol":
            exchange = request.url.params.get("EXCD")
            rows = {
                "NAS": [{"symb": "AAA", "last": "2.34", "rate": "23.16", "tvol": "123456", "rank": "1"}],
                "NYS": [{"symb": "BBB", "last": "3.10", "rate": "15.00", "tvol": "333333", "rank": "1"}],
                "AMS": [{"symb": "CCC", "last": "1.75", "rate": "12.00", "tvol": "222222", "rank": "1"}],
            }[str(exchange)]
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"exchange": exchange},
                    "output2": rows,
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    provider = KISLiveMarketProvider(
        app_key="kis-app",
        app_secret="kis-secret",
        client=client,
        market_div_code="J",
    )

    trade = provider.latest_trade("AAA")
    quote = provider.latest_quote("AAA")
    snapshot = provider.latest_snapshot("AAA")
    gainers = provider.top_gainers(limit=5)
    actives = provider.most_active(limit=5)

    assert trade is not None and trade.price == 2.34 and trade.source == "kis"
    assert quote is not None and quote.ask_price == 2.35 and quote.bid_size == 800
    assert snapshot is not None
    assert snapshot.market_status == "open"
    assert snapshot.previous_close == 1.90
    assert snapshot.volume == 123456
    assert snapshot.updated_at is not None
    assert snapshot.latest_trade is not None and snapshot.latest_trade.price == 2.34
    assert snapshot.latest_quote is not None and snapshot.latest_quote.ask_size == 900
    assert snapshot.raw["prevDay"]["c"] == 1.90
    assert snapshot.raw["day"]["v"] == 123456
    assert snapshot.raw["ticker"]["prevDay"]["c"] == 1.90
    assert snapshot.raw["ticker"]["day"]["v"] == 123456
    assert snapshot.raw["previous_close"] == 1.90
    assert snapshot.raw["volume"] == 123456
    assert snapshot.raw["updated_at"] is not None
    assert gainers and [row.symbol for row in gainers[:3]] == ["AAA", "BBB", "CCC"]
    assert actives and [row.symbol for row in actives[:3]] == ["BBB", "CCC", "AAA"]
    assert any(path == "/oauth2/tokenP" for _, path, _, _ in requests)
    assert any(path == "/uapi/overseas-price/v1/quotations/price" for _, path, _, _ in requests)
    assert any(path == "/uapi/overseas-price/v1/quotations/price-detail" for _, path, _, _ in requests)
    assert any(path == "/uapi/overseas-price/v1/quotations/inquire-asking-price" for _, path, _, _ in requests)
    assert any(path == "/uapi/overseas-stock/v1/ranking/updown-rate" for _, path, _, _ in requests)
    assert any(path == "/uapi/overseas-stock/v1/ranking/trade-vol" for _, path, _, _ in requests)
    assert any(headers.get("authorization") == "Bearer kis-access-token" for _, _, _, headers in requests if headers.get("tr_id"))
    assert quote.timestamp == datetime(2026, 3, 24, 13, 10, 11, tzinfo=timezone.utc)
    assert snapshot.updated_at == datetime(2026, 3, 24, 13, 10, 11, tzinfo=timezone.utc)
    assert snapshot.latest_trade is not None
    assert snapshot.latest_trade.timestamp == datetime(2026, 3, 24, 13, 10, 11, tzinfo=timezone.utc)


def test_kis_live_provider_retries_on_rate_limit() -> None:
    attempts = {"quote": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/price":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {
                        "rsym": "NASAAA",
                        "base": "1.90",
                        "last": "2.34",
                    },
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-asking-price":
            attempts["quote"] += 1
            if attempts["quote"] == 1:
                return httpx.Response(
                    500,
                    json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
                )
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"dymd": "20260324", "dhms": "091011", "last": "2.34"},
                    "output2": {"pbid1": "2.33", "pask1": "2.35", "vbid1": "800", "vask1": "900"},
                    "output3": {},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    provider = KISLiveMarketProvider(
        app_key="kis-app",
        app_secret="kis-secret",
        client=client,
        market_div_code="J",
    )

    quote = provider.latest_quote("AAA")

    assert quote is not None and quote.bid_price == 2.33 and quote.ask_price == 2.35
    assert attempts["quote"] == 2


def test_live_market_provider_emits_provider_request_metrics_jsonl(tmp_path) -> None:
    attempts = {"quote": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/price":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {
                        "rsym": "NASAAA",
                        "base": "1.90",
                        "last": "2.34",
                    },
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-asking-price":
            attempts["quote"] += 1
            if attempts["quote"] == 1:
                return httpx.Response(
                    500,
                    json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate limit"},
                )
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"dymd": "20260324", "dhms": "091011", "last": "2.34"},
                    "output2": {"pbid1": "2.33", "pask1": "2.35", "vbid1": "800", "vask1": "900"},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    settings = AppSettings(
        use_mock_market_data=False,
        live_market_provider="kis",
        live_observability_enabled=True,
        live_observability_path=tmp_path / "live_metrics.jsonl",
        cache_dir=tmp_path / "cache",
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
    )

    provider = build_live_market_provider(settings, client=client)
    quote = provider.latest_quote("AAA")

    assert quote is not None
    records = [
        json.loads(line)
        for line in settings.live_observability_path.read_text(encoding="utf-8").splitlines()
    ]
    provider_events = [record for record in records if record["metric_type"] == "provider_request"]

    assert any(
        record["path"] == "/uapi/overseas-price/v1/quotations/inquire-asking-price"
        and record["ok"] is False
        and record["msg_cd"] == "EGW00201"
        for record in provider_events
    )
    assert any(
        record["path"] == "/uapi/overseas-price/v1/quotations/inquire-asking-price"
        and record["ok"] is True
        for record in provider_events
    )
