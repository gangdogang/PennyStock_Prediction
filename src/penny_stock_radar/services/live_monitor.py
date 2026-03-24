from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..providers.live_market import LiveSnapshot, build_live_market_provider

EASTERN = ZoneInfo("America/New_York")


@dataclass(slots=True)
class LiveSnapshotView:
    symbol: str
    source: str
    market_phase: str
    trade_price: float | None
    trade_size: int | None
    bid_price: float | None
    ask_price: float | None
    spread_pct: float | None
    market_status: str | None
    updated_at: datetime | None


class LiveMonitor:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def provider_status(self) -> tuple[bool, str]:
        provider = build_live_market_provider(self.settings)
        available = provider.is_available()
        reason = getattr(provider, "reason", provider.source_name)
        self._close_provider(provider)
        return available, reason

    def fetch(self, symbols: list[str], limit: int = 5) -> list[LiveSnapshotView]:
        provider = build_live_market_provider(self.settings)
        if not provider.is_available():
            reason = getattr(provider, "reason", "No live provider available.")
            self._close_provider(provider)
            raise RuntimeError(reason)

        views: list[LiveSnapshotView] = []
        try:
            for symbol in symbols[:limit]:
                snapshot = provider.latest_snapshot(symbol)
                if snapshot is None:
                    continue
                views.append(self._to_view(snapshot))
        finally:
            self._close_provider(provider)
        return views

    def _to_view(self, snapshot: LiveSnapshot) -> LiveSnapshotView:
        bid_price = snapshot.latest_quote.bid_price if snapshot.latest_quote else None
        ask_price = snapshot.latest_quote.ask_price if snapshot.latest_quote else None
        updated_at = snapshot.updated_at or (
            snapshot.latest_trade.timestamp if snapshot.latest_trade else None
        )
        return LiveSnapshotView(
            symbol=snapshot.symbol,
            source=snapshot.source,
            market_phase=self._market_phase(updated_at),
            trade_price=snapshot.latest_trade.price if snapshot.latest_trade else None,
            trade_size=snapshot.latest_trade.size if snapshot.latest_trade else None,
            bid_price=bid_price,
            ask_price=ask_price,
            spread_pct=self._spread_pct(bid_price, ask_price),
            market_status=snapshot.market_status,
            updated_at=updated_at,
        )

    def _market_phase(self, updated_at: datetime | None) -> str:
        current = updated_at.astimezone(EASTERN) if updated_at else datetime.now(EASTERN)
        if current.weekday() >= 5:
            return "주말/휴장"

        session_time = current.timetz().replace(tzinfo=None)
        if time(4, 0) <= session_time < time(9, 30):
            return "프리장"
        if time(9, 30) <= session_time < time(16, 0):
            return "정규장"
        if time(16, 0) <= session_time < time(20, 0):
            return "애프터장"
        return "장외/대기"

    def _spread_pct(self, bid: float | None, ask: float | None) -> float | None:
        if bid is None or ask is None:
            return None
        midpoint = (bid + ask) / 2
        if midpoint <= 0:
            return None
        return (ask - bid) / midpoint

    def _close_provider(self, provider: object) -> None:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
