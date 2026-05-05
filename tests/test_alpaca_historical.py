from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from penny_stock_radar.config import AppSettings
from penny_stock_radar.db import (
    fetch_historical_l1_quotes,
    init_database,
    insert_historical_l1_quotes,
)
from penny_stock_radar.models import HistoricalL1Quote
from penny_stock_radar.services.alpaca_historical import (
    ALPACA_DIAGNOSTIC_WARNING,
    ALPACA_HISTORICAL_QUOTES_SOURCE,
    AlpacaHistoricalDataService,
    build_quote_windows,
    build_quote_windows_from_strategy_run_dir,
    build_quote_windows_from_trade_log,
)


def test_alpaca_historical_importer_paginates_and_dedupes_quotes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_l1_quotes(
        db_path,
        [
            HistoricalL1Quote(
                symbol="AAA",
                market_date="2026-04-10",
                timestamp=datetime(2026, 4, 10, 13, 31, tzinfo=timezone.utc),
                bid_price=1.01,
                ask_price=1.05,
                source="fixture",
            )
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = dict(request.url.params)
        assert request.headers["APCA-API-KEY-ID"] == "alpaca-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "alpaca-secret"
        assert request.url.path == "/v2/stocks/quotes"
        assert params["symbols"] == "AAA,BBB"
        assert params["feed"] == "iex"
        assert params["limit"] == "10000"
        if params.get("page_token") == "next-page":
            return httpx.Response(
                200,
                json={
                    "quotes": {
                        "AAA": [
                            {
                                "t": "2026-04-10T13:32:00.123456789Z",
                                "bp": "1.02",
                                "ap": "1.06",
                            }
                        ]
                    },
                    "next_page_token": None,
                },
            )
        assert "page_token" not in params
        return httpx.Response(
            200,
            json={
                "quotes": {
                    "AAA": [
                        {"t": "2026-04-10T13:30:00Z", "bp": "1.00", "ap": "1.04"},
                        {"t": "2026-04-10T13:30:00Z", "bp": "1.00", "ap": "1.04"},
                        {"t": "2026-04-10T13:31:00Z", "bp": "1.01", "ap": "1.05"},
                        {"t": "not-a-timestamp", "bp": "1.03", "ap": "1.07"},
                    ],
                    "BBB": [
                        {"t": "2026-04-10T13:30:30Z", "bp": "2.01", "ap": "2.05"}
                    ],
                },
                "next_page_token": "next-page",
            },
        )

    settings = AppSettings(db_path=db_path)
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://data.alpaca.markets",
    )
    service = AlpacaHistoricalDataService(
        settings,
        client=client,
        api_key="alpaca-key",
        api_secret="alpaca-secret",
    )
    windows = build_quote_windows(["bbb", "AAA", "aaa"], "2026-04-10", "09:30", "09:33")

    summary = service.import_historical_quotes(windows, max_pages_per_window=3)
    aaa_rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="AAA")
    bbb_rows = fetch_historical_l1_quotes(db_path, market_date="2026-04-10", symbol="BBB")

    assert len(requests) == 2
    assert dict(requests[1].url.params)["page_token"] == "next-page"
    assert summary.market_date == "2026-04-10"
    assert summary.market_dates == ["2026-04-10"]
    assert summary.requested_symbols == 2
    assert summary.windows == 1
    assert summary.pages == 2
    assert summary.rows == 5
    assert summary.inserted_rows == 3
    assert summary.db_duplicate_rows == 1
    assert summary.in_memory_duplicate_rows == 1
    assert summary.duplicate_rows == 2
    assert summary.skipped_rows == 1
    assert summary.diagnostic_only is True
    assert summary.warning == ALPACA_DIAGNOSTIC_WARNING
    assert ALPACA_DIAGNOSTIC_WARNING in summary.warnings
    assert [row["source"] for row in aaa_rows] == [
        ALPACA_HISTORICAL_QUOTES_SOURCE,
        "fixture",
        ALPACA_HISTORICAL_QUOTES_SOURCE,
    ]
    assert [row["bid_price"] for row in aaa_rows] == [1.00, 1.01, 1.02]
    assert [row["ask_price"] for row in aaa_rows] == [1.04, 1.05, 1.06]
    assert aaa_rows[2]["quote_at"] == "2026-04-10T13:32:00.123456+00:00"
    assert len(bbb_rows) == 1
    assert bbb_rows[0]["bid_price"] == 2.01
    assert bbb_rows[0]["ask_price"] == 2.05
    assert bbb_rows[0]["source"] == ALPACA_HISTORICAL_QUOTES_SOURCE


def test_alpaca_quote_window_helpers_build_from_trade_log_and_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "strategy_run"
    run_dir.mkdir()
    trade_log = run_dir / "paper_trade_log.csv"
    trade_log.write_text(
        "\n".join(
            [
                "event,bucket,symbol,market_date,entry_at,status,opened_at",
                "ENTRY,predictor_weighted,aaa,2026-04-10,2026-04-10T09:31:00-04:00,,",
                "ENTRY,momentum_only,bbb,2026-04-10,2026-04-10T09:32:00-04:00,,",
                "EXIT,predictor_weighted,ccc,2026-04-10,2026-04-10T09:33:00-04:00,CLOSED,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    windows = build_quote_windows_from_trade_log(
        trade_log,
        bucket="predictor_weighted",
        minutes_before=2,
        minutes_after=3,
    )
    run_dir_windows = build_quote_windows_from_strategy_run_dir(
        run_dir,
        bucket="predictor_weighted",
        minutes_before=2,
        minutes_after=3,
    )
    split_windows = build_quote_windows(
        ["ccc", "aaa", "bbb"],
        "2026-04-10",
        "09:30",
        "09:35",
        symbols_per_window=2,
    )

    assert windows == run_dir_windows
    assert len(windows) == 1
    assert windows[0].symbols == ("AAA",)
    assert windows[0].market_date == "2026-04-10"
    assert windows[0].start_at.isoformat() == "2026-04-10T09:29:00-04:00"
    assert windows[0].end_at.isoformat() == "2026-04-10T09:34:00-04:00"
    assert [window.symbols for window in split_windows] == [("AAA", "BBB"), ("CCC",)]
    assert split_windows[0].start_at.isoformat() == "2026-04-10T09:30:00-04:00"
    assert split_windows[0].end_at.isoformat() == "2026-04-10T09:35:00-04:00"


def test_alpaca_trade_log_window_helper_supports_closed_position_export_shape(tmp_path: Path) -> None:
    trade_log = tmp_path / "paper_trade_log.csv"
    trade_log.write_text(
        "\n".join(
            [
                "bucket,symbol,status,market_date,opened_at",
                "predictor_weighted,aaa,CLOSED,2026-04-10,2026-04-10T09:31:30-04:00",
                "predictor_weighted,bbb,OPEN,2026-04-10,2026-04-10T09:32:30-04:00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    windows = build_quote_windows_from_trade_log(
        trade_log,
        bucket="predictor_weighted",
        minutes_before=0.5,
        minutes_after=0.5,
    )

    assert len(windows) == 1
    assert windows[0].symbols == ("AAA",)
    assert windows[0].start_at.isoformat() == "2026-04-10T09:31:00-04:00"
    assert windows[0].end_at.isoformat() == "2026-04-10T09:32:00-04:00"
