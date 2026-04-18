from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from penny_stock_radar.config import AppSettings
from penny_stock_radar.providers.broker import BrokerOrderRequest, BrokerReplaceRequest
from penny_stock_radar.providers.kis_mock_broker import KISMockBrokerAdapter


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
        [_kis_master_row(exchange_code="NAS", symbol="TSLA", english_name="Tesla Inc")],
    )
    _write_kis_master_file(
        tmp_path / "NYSMST.COD",
        [_kis_master_row(exchange_code="NYS", symbol="IBM", english_name="IBM")],
    )
    _write_kis_master_file(
        tmp_path / "AMSMST.COD",
        [_kis_master_row(exchange_code="AMS", symbol="SPY", english_name="SPY")],
    )


def test_kis_mock_broker_submits_replaces_cancels_and_reuses_cached_token(tmp_path: Path) -> None:
    _write_master_bundle(tmp_path)
    requests: list[tuple[str, str, dict[str, str], dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body.decode("utf-8")) if body else None
        requests.append((request.method, request.url.path, dict(request.url.params), payload))
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-mock-token",
                    "access_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/trading/daytime-order":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {"ODNO": "12345678"},
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl":
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": {"ODNO": "87654321"},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        kis_mock_app_key="mock-app",
        kis_mock_app_secret="mock-secret",
        kis_mock_account_number="12345678",
        kis_mock_account_product_code="01",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
        cache_dir=tmp_path / "cache",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=settings.kis_mock_base_url,
    )
    adapter = KISMockBrokerAdapter(settings, client=client)

    submitted = adapter.submit_order(
        BrokerOrderRequest(
            client_order_id="tsla-submit-1",
            symbol="TSLA",
            side="buy",
            quantity=3,
            limit_price=199.25,
            intent="ENTRY",
            strategy_bucket="predicted_starter",
            market_phase="premarket",
        )
    )
    replaced = adapter.replace_order(
        submitted,
        BrokerReplaceRequest(
            client_order_id="tsla-replace-1",
            new_quantity=5,
            new_limit_price=198.75,
        ),
    )
    canceled = adapter.cancel_order(submitted, client_order_id="tsla-cancel-1")

    assert submitted.broker_order_id == "12345678"
    assert submitted.exchange_code == "NASD"
    assert submitted.status == "ACKNOWLEDGED"
    assert replaced.status == "REPLACED"
    assert replaced.quantity == 5
    assert replaced.limit_price == 198.75
    assert replaced.original_broker_order_id == "12345678"
    assert canceled.status == "CANCELED"
    assert canceled.original_broker_order_id == "12345678"
    token_calls = [row for row in requests if row[1] == "/oauth2/tokenP"]
    assert len(token_calls) == 1
    order_calls = [row for row in requests if row[1] == "/uapi/overseas-stock/v1/trading/daytime-order"]
    assert order_calls[0][3]["OVRS_EXCG_CD"] == "NASD"
    assert order_calls[0][3]["ORD_QTY"] == "3"
    assert order_calls[0][3]["ORD_DVSN"] == "00"


def test_kis_mock_broker_blocks_regular_session_daytime_orders(tmp_path: Path, caplog) -> None:
    _write_master_bundle(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-mock-token",
                    "access_token_expired": "2026-12-31 23:59:59",
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        kis_mock_app_key="mock-app",
        kis_mock_app_secret="mock-secret",
        kis_mock_account_number="12345678",
        kis_mock_account_product_code="01",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
        cache_dir=tmp_path / "cache",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=settings.kis_mock_base_url,
    )
    adapter = KISMockBrokerAdapter(settings, client=client)
    adapter._current_market_session = lambda now=None: "regular"  # type: ignore[method-assign]

    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match="outside the US regular session"):
            adapter.submit_order(
                BrokerOrderRequest(
                    client_order_id="tsla-submit-1",
                    symbol="TSLA",
                    side="buy",
                    quantity=3,
                    limit_price=199.25,
                    intent="ENTRY",
                    strategy_bucket="predicted_starter",
                    market_phase="regular",
                )
            )

    assert requests == []
    assert "kis_mock daytime-order blocked during regular session" in caplog.text


def test_kis_mock_broker_fetches_orders_fills_positions_and_account(tmp_path: Path) -> None:
    _write_master_bundle(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "kis-mock-token",
                    "access_token_expired": "2026-12-31 23:59:59",
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/trading/inquire-nccs":
            return httpx.Response(
                200,
                headers={"tr_cont": ""},
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": [
                        {
                            "odno": "1111",
                            "pdno": "TSLA",
                            "sll_buy_dvsn_cd": "02",
                            "ord_qty": "5",
                            "tot_ccld_qty": "2",
                            "nccs_qty": "3",
                            "ovrs_ord_unpr": "199.00",
                            "ovrs_excg_cd": "NASD",
                        }
                    ],
                    "ctx_area_fk200": "",
                    "ctx_area_nk200": "",
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/trading/inquire-ccnl":
            return httpx.Response(
                200,
                headers={"tr_cont": ""},
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output": [
                        {
                            "odno": "1111",
                            "pdno": "TSLA",
                            "sll_buy_dvsn_cd": "02",
                            "ft_ord_qty": "5",
                            "ft_ccld_qty": "5",
                            "ft_ord_unpr3": "199.00",
                            "ft_ccld_unpr3": "198.95",
                            "ovrs_excg_cd": "NASD",
                        }
                    ],
                    "ctx_area_fk200": "",
                    "ctx_area_nk200": "",
                },
            )
        if request.url.path == "/uapi/overseas-stock/v1/trading/inquire-balance":
            return httpx.Response(
                200,
                headers={"tr_cont": ""},
                json={
                    "rt_cd": "0",
                    "msg_cd": "0",
                    "msg1": "success",
                    "output1": [
                        {
                            "ovrs_pdno": "TSLA",
                            "ovrs_excg_cd": "NASD",
                            "ovrs_cblc_qty": "4",
                            "ord_psbl_qty": "4",
                            "pchs_avg_pric": "190.50",
                            "now_pric2": "199.10",
                            "frcr_evlu_amt2": "796.40",
                            "tr_crcy_cd": "USD",
                        }
                    ],
                    "output2": [
                        {
                            "frcr_dncl_amt_2": "5000.50",
                            "frcr_buy_mgn_amt": "4000.25",
                            "tot_evlu_pfls_amt": "5796.90",
                        }
                    ],
                    "ctx_area_fk200": "",
                    "ctx_area_nk200": "",
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = AppSettings(
        kis_mock_app_key="mock-app",
        kis_mock_app_secret="mock-secret",
        kis_mock_account_number="12345678",
        kis_mock_account_product_code="01",
        kis_nasdaq_master_path=tmp_path / "NASMST.COD",
        kis_nyse_master_path=tmp_path / "NYSMST.COD",
        kis_amex_master_path=tmp_path / "AMSMST.COD",
        cache_dir=tmp_path / "cache",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=settings.kis_mock_base_url,
    )
    adapter = KISMockBrokerAdapter(settings, client=client)

    open_orders = adapter.fetch_open_orders()
    fills = adapter.fetch_fills(market_date="20260418")
    positions = adapter.fetch_positions()
    account = adapter.fetch_account()

    assert len(open_orders) == 1
    assert open_orders[0].status == "WORKING"
    assert open_orders[0].remaining_quantity == 3
    assert len(fills) == 1
    assert fills[0].status == "FILLED"
    assert fills[0].avg_fill_price == 198.95
    assert len(positions) == 1
    assert positions[0].symbol == "TSLA"
    assert positions[0].market_value == 796.40
    assert account.cash_balance == 5000.50
    assert account.buying_power == 4000.25
    assert account.total_equity == 5796.90
