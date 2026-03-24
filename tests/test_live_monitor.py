from __future__ import annotations

from datetime import datetime, timezone

from penny_stock_radar.config import AppSettings
from penny_stock_radar.providers.live_market import (
    LiveQuote,
    LiveSnapshot,
    LiveTrade,
    NullLiveMarketProvider,
)
from penny_stock_radar.services.live_monitor import LiveMonitor


class _FakeProvider:
    source_name = "fake"

    def is_available(self) -> bool:
        return True

    def latest_snapshot(self, symbol: str) -> LiveSnapshot | None:
        return LiveSnapshot(
            symbol=symbol,
            source="fake",
            latest_trade=LiveTrade(
                symbol=symbol,
                price=1.25,
                size=500,
                timestamp=datetime(2026, 3, 24, 13, 45, tzinfo=timezone.utc),
                source="fake",
            ),
            latest_quote=LiveQuote(
                symbol=symbol,
                bid_price=1.24,
                ask_price=1.26,
                bid_size=100,
                ask_size=150,
                timestamp=datetime(2026, 3, 24, 13, 45, tzinfo=timezone.utc),
                source="fake",
            ),
            market_status="open",
            updated_at=datetime(2026, 3, 24, 13, 45, tzinfo=timezone.utc),
        )


def test_live_monitor_fetch_formats_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "penny_stock_radar.services.live_monitor.build_live_market_provider",
        lambda settings: _FakeProvider(),
    )

    monitor = LiveMonitor(AppSettings())
    rows = monitor.fetch(["AAA", "BBB"], limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "AAA"
    assert row.source == "fake"
    assert row.trade_price == 1.25
    assert row.bid_price == 1.24
    assert row.ask_price == 1.26
    assert row.market_phase == "정규장"
    assert row.spread_pct is not None and row.spread_pct > 0


def test_live_monitor_provider_status_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "penny_stock_radar.services.live_monitor.build_live_market_provider",
        lambda settings: NullLiveMarketProvider("missing credentials"),
    )

    monitor = LiveMonitor(AppSettings())
    available, reason = monitor.provider_status()

    assert available is False
    assert reason == "missing credentials"
