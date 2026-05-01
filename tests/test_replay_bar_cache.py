from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from penny_stock_radar.services.replay_bar_cache import ReplayBarSeriesCache

EASTERN = ZoneInfo("America/New_York")


class FakeScanner:
    def _analysis(
        self,
        *,
        market_phase,
        pct_change,
        dollar_volume,
        spread_pct,
        predicted,
        watchlist_score,
    ):
        score = round((pct_change or 0.0) + (dollar_volume / 1000.0), 6)
        return "OPENING_RANGE_CANDIDATE", score, [f"predicted:{int(predicted)}"]


def test_replay_bar_cursor_carries_forward_visible_bars_and_cumulative_values() -> None:
    cache = ReplayBarSeriesCache.from_rows(
        [
            _row("AAA", "2026-04-10T08:01:00-04:00", 1.0, 2.0, 200, bid=1.99, ask=2.01),
            _row("AAA", "2026-04-10T07:59:00-04:00", 1.0, 1.0, 100),
            _row("BBB", "2026-04-10T08:00:00-04:00", 3.0, 3.0, 10, bid=2.99, ask=3.01),
            _row("CCC", "2026-04-10T08:00:00", 4.0, 4.0, 5, bid=3.99, ask=4.01),
        ]
    )
    cutoff_at = datetime(2026, 4, 10, 8, 0, tzinfo=EASTERN)
    cursor = cache.cursor()

    assert cache.loaded_bar_count == 4
    assert cache.loaded_symbol_count == 3
    assert cache.has_missing_bid_ask is True
    assert cache.series_by_symbol["CCC"].bars[0].bar_at.tzinfo is not None
    assert cache.simulated_times(cutoff_at) == [
        datetime(2026, 4, 10, 8, 0, tzinfo=EASTERN),
        datetime(2026, 4, 10, 8, 1, tzinfo=EASTERN),
    ]

    rows_at_800 = {
        row.symbol: row
        for row in cursor.activity_at(
            simulated_time=datetime(2026, 4, 10, 8, 0, tzinfo=EASTERN),
            scanner=FakeScanner(),
            watchlist_rank={"AAA": 1, "BBB": 2, "CCC": 3},
            watchlist_score={"AAA": 4.0, "BBB": 3.0, "CCC": 2.0},
            predictions_by_symbol={"AAA": object()},
        )
    }
    rows_at_801 = {
        row.symbol: row
        for row in cursor.activity_at(
            simulated_time=datetime(2026, 4, 10, 8, 1, tzinfo=EASTERN),
            scanner=FakeScanner(),
            watchlist_rank={"AAA": 1, "BBB": 2, "CCC": 3},
            watchlist_score={"AAA": 4.0, "BBB": 3.0, "CCC": 2.0},
            predictions_by_symbol={"AAA": object()},
        )
    }

    assert rows_at_800["AAA"].market_data_at == datetime(2026, 4, 10, 7, 59, tzinfo=EASTERN)
    assert rows_at_800["AAA"].volume == 100
    assert rows_at_800["AAA"].dollar_volume == 100
    assert rows_at_800["AAA"].has_live_quote is False
    assert rows_at_800["AAA"].predicted is True
    assert rows_at_801["AAA"].market_data_at == datetime(2026, 4, 10, 8, 1, tzinfo=EASTERN)
    assert rows_at_801["AAA"].volume == 300
    assert rows_at_801["AAA"].dollar_volume == 500
    assert rows_at_801["BBB"].market_data_at == datetime(2026, 4, 10, 8, 0, tzinfo=EASTERN)
    assert rows_at_801["BBB"].created_at == datetime(2026, 4, 10, 8, 1, tzinfo=EASTERN)
    assert rows_at_801["BBB"].volume == 10
    assert rows_at_801["BBB"].dollar_volume == 30
    assert rows_at_801["BBB"].spread_pct == pytest.approx((3.01 - 2.99) / 3.0)


def _row(
    symbol: str,
    bar_at: str,
    open_price: float,
    close_price: float,
    volume: float,
    *,
    bid: float | None = None,
    ask: float | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market_date": "2026-04-10",
        "market_phase": "premarket",
        "bar_at": bar_at,
        "open_price": open_price,
        "high_price": max(open_price, close_price),
        "low_price": min(open_price, close_price),
        "close_price": close_price,
        "volume": volume,
        "bid_price": bid,
        "ask_price": ask,
        "spread_pct": None,
    }
