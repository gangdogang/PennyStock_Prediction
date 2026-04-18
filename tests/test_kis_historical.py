from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

import httpx

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    fetch_historical_l1_quotes,
    fetch_historical_minute_bars,
    init_database,
    insert_historical_minute_bars,
)
from penny_stock_radar.models import HistoricalMinuteBar
from penny_stock_radar.services.kis_historical import KISHistoricalDataService


def _write_kis_master_file(path: Path, rows: list[list[str]]) -> None:
    payload = "\n".join("\t".join(row) for row in rows) + "\n"
    path.write_bytes(payload.encode("cp949"))


def _kis_master_row(
    *,
    exchange_code: str,
    symbol: str,
    english_name: str,
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
        "2",
        "USD",
        "",
        "",
        "1.00",
        "",
        "",
        "0400",
        "2000",
        "N",
        "",
        "",
        "",
        "",
        "004",
        "",
    ]


def _write_master_bundle(tmp_path: Path) -> None:
    _write_kis_master_file(
        tmp_path / "NASMST.COD",
        [_kis_master_row(exchange_code="NAS", symbol="AAA", english_name="AAA Corp")],
    )
    _write_kis_master_file(
        tmp_path / "NYSMST.COD",
        [_kis_master_row(exchange_code="NYS", symbol="BBB", english_name="BBB Corp")],
    )
    _write_kis_master_file(
        tmp_path / "AMSMST.COD",
        [_kis_master_row(exchange_code="AMS", symbol="CCC", english_name="CCC Corp")],
    )


def test_kis_historical_service_backfills_minute_bars_without_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _write_master_bundle(tmp_path)
    requests: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice":
            params = dict(request.url.params)
            requests.append((request.url.path, params))
            if params.get("NEXT") == "1":
                return httpx.Response(
                    200,
                    json={"rt_cd": "0", "msg_cd": "0", "msg1": "success", "output1": {}, "output2": []},
                )
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"next": "1"},
                    "output2": [
                        {
                            "xymd": "20260410",
                            "xhms": "040000",
                            "open": "1.00",
                            "high": "1.05",
                            "low": "0.99",
                            "last": "1.03",
                            "evol": "1000",
                        },
                        {
                            "xymd": "20260410",
                            "xhms": "040100",
                            "open": "1.03",
                            "high": "1.08",
                            "low": "1.02",
                            "last": "1.07",
                            "evol": "2000",
                        },
                        {
                            "xymd": "20260409",
                            "xhms": "195900",
                            "open": "0.95",
                            "high": "0.96",
                            "low": "0.94",
                            "last": "0.95",
                            "evol": "500",
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    service = KISHistoricalDataService(settings, client=client)

    summary_first = service.backfill_minute_bars("2026-04-10", symbols=["AAA"], max_pages=2)
    summary_second = service.backfill_minute_bars("2026-04-10", symbols=["AAA"], max_pages=2)
    rows = fetch_historical_minute_bars(db_path, market_date="2026-04-10", symbol="AAA")

    assert summary_first.inserted_rows == 2
    assert summary_second.inserted_rows == 0
    assert [row["market_phase"] for row in rows] == ["premarket", "premarket"]
    assert [row["close_price"] for row in rows] == [1.03, 1.07]
    assert all(row["source"] == "kis_minute_api" for row in rows)
    assert any(path.endswith("inquire-time-itemchartprice") for path, _ in requests)


def test_kis_historical_service_backfill_normalizes_existing_timestamp_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _write_master_bundle(tmp_path)
    requests: list[tuple[str, dict[str, str]]] = []

    insert_historical_minute_bars(
        db_path,
        [
            HistoricalMinuteBar(
                symbol="AAA",
                market_date="2026-04-10",
                market_phase="premarket",
                timestamp=datetime(2026, 4, 10, 8, 0, 0, 500000, tzinfo=timezone.utc),
                open_price=1.00,
                high_price=1.05,
                low_price=0.99,
                close_price=1.03,
                volume=1000,
                source="fixture",
            )
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice":
            params = dict(request.url.params)
            requests.append((request.url.path, params))
            if params.get("NEXT") == "1":
                return httpx.Response(
                    200,
                    json={"rt_cd": "0", "msg_cd": "0", "msg1": "success", "output1": {}, "output2": []},
                )
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {"next": "1"},
                    "output2": [
                        {
                            "xymd": "20260410",
                            "xhms": "040000",
                            "open": "1.00",
                            "high": "1.05",
                            "low": "0.99",
                            "last": "1.03",
                            "evol": "1000",
                        },
                        {
                            "xymd": "20260410",
                            "xhms": "040100",
                            "open": "1.03",
                            "high": "1.08",
                            "low": "1.02",
                            "last": "1.07",
                            "evol": "2000",
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    service = KISHistoricalDataService(settings, client=client)

    summary = service.backfill_minute_bars("2026-04-10", symbols=["AAA"], max_pages=2)
    rows = fetch_historical_minute_bars(db_path, market_date="2026-04-10", symbol="AAA")

    assert summary.inserted_rows == 1
    assert len(rows) == 2
    assert [row["close_price"] for row in rows] == [1.03, 1.07]
    assert any(path.endswith("inquire-time-itemchartprice") for path, _ in requests)


def test_kis_historical_service_captures_l1_quotes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _write_master_bundle(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_expired": "2026-12-31 23:59:59",
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
                        "dymd": "20260410",
                        "dhms": "093015",
                        "last": "1.02",
                    },
                    "output2": {
                        "pbid1": "1.01",
                        "pask1": "1.03",
                    },
                    "output3": {},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    service = KISHistoricalDataService(settings, client=client)
    service._now_eastern = lambda: datetime(2026, 4, 10, 9, 35, tzinfo=timezone.utc)
    service._utc_now = lambda: datetime(2026, 4, 10, 13, 35, tzinfo=timezone.utc)

    summary = service.capture_l1_quotes(symbols=["AAA"])
    rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="AAA")

    assert summary.inserted_rows == 1
    assert summary.rows == 1
    assert summary.distinct_minute_keys == 1
    assert summary.snapshot_mismatch_count == 0
    assert summary.stale_timestamp_fallback_count == 0
    assert summary.duplicate_minute_bucket_count == 0
    assert len(rows) == 1
    assert rows[0]["bid_price"] == 1.01
    assert rows[0]["ask_price"] == 1.03
    assert rows[0]["last_price"] == 1.02
    assert rows[0]["source"] == "kis_l1_snapshot"
    assert "/uapi/overseas-price/v1/quotations/inquire-asking-price" in requests


def test_kis_historical_service_warns_on_l1_timestamp_drift_but_still_saves_row(
    tmp_path: Path,
    caplog,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _write_master_bundle(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_expired": "2026-12-31 23:59:59",
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
                        "dymd": "20260410",
                        "dhms": "093015",
                        "last": "1.02",
                    },
                    "output2": {
                        "pbid1": "1.01",
                        "pask1": "1.03",
                    },
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    service = KISHistoricalDataService(settings, client=client)
    service._utc_now = lambda: datetime(2026, 4, 10, 16, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.WARNING, logger="penny_stock_radar.services.kis_historical"):
        summary = service.capture_l1_quotes(symbols=["AAA"])

    rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="AAA")

    assert summary.inserted_rows == 1
    assert len(rows) == 1
    assert "kis_l1 timestamp drift detected" in caplog.text


def test_kis_historical_service_tracks_duplicate_minute_bucket_diagnostics(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    _write_master_bundle(tmp_path)
    requests = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-access-token",
                    "access_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-price/v1/quotations/inquire-asking-price":
            requests["count"] += 1
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": {
                        "dymd": "20260410",
                        "dhms": "093015" if requests["count"] == 1 else "093045",
                        "last": "1.02",
                    },
                    "output2": {
                        "pbid1": "1.01",
                        "pask1": "1.03",
                    },
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        db_path=db_path,
        kis_app_key="kis-app",
        kis_app_secret="kis-secret",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openapi.koreainvestment.com:9443",
    )
    service = KISHistoricalDataService(settings, client=client)
    service._now_eastern = lambda: datetime(2026, 4, 10, 9, 35, tzinfo=timezone.utc)
    service._utc_now = lambda: datetime(2026, 4, 10, 13, 35, tzinfo=timezone.utc)

    first = service.capture_l1_quotes(symbols=["AAA"])
    second = service.capture_l1_quotes(symbols=["AAA"])

    assert first.inserted_rows == 1
    assert first.distinct_minute_keys == 1
    assert second.inserted_rows == 1
    assert second.rows == 1
    assert second.distinct_minute_keys == 0
    assert second.duplicate_minute_bucket_count == 1
