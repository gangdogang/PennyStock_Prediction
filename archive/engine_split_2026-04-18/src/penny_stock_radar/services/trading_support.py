from __future__ import annotations

from datetime import datetime, time
import re
from statistics import median
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..models import MarketActivity, PaperPosition

EASTERN = ZoneInfo("America/New_York")
BLOCKED_MARKET_STATUSES = {
    "halt",
    "halted",
    "luld_pause",
    "luld_volatility_pause",
    "pause",
    "paused",
    "suspend",
    "suspended",
    "trading_halt",
    "trading_pause",
    "volatility_halt",
    "volatility_pause",
}
STATE_NOTE_PREFIX = "state:"

REGIME_LIMITS = {
    "cold": {"starter": 2, "add": 0},
    "normal": {"starter": 3, "add": 1},
    "hot": {"starter": 5, "add": 2},
}


def best_live_rank(row: MarketActivity) -> int:
    candidates = [value for value in (row.pct_rank, row.volume_rank) if value and value > 0]
    return min(candidates) if candidates else 9999


def predicted_rank_band(rank: int | None) -> str:
    if rank is None or rank <= 0:
        return "unpredicted"
    if rank <= 3:
        return "1-3"
    if rank <= 5:
        return "4-5"
    return "6+"


def classify_day_regime(settings: AppSettings, activity: list[MarketActivity]) -> str:
    if not activity:
        return "cold"
    leaders = sorted(activity, key=lambda row: (best_live_rank(row), row.symbol))[:10]
    predicted_hit_breadth = sum(1 for row in leaders if row.predicted and best_live_rank(row) <= 10)
    spreads = [float(row.spread_pct) for row in leaders if row.spread_pct is not None]
    dollar_volume = [float(row.dollar_volume) for row in leaders if row.dollar_volume is not None]
    median_spread = median(spreads) if spreads else settings.premarket_max_spread_pct * 2
    median_dollar_volume = median(dollar_volume) if dollar_volume else 0.0
    if (
        predicted_hit_breadth >= 3
        and median_spread <= settings.premarket_max_spread_pct * 0.75
        and median_dollar_volume >= settings.premarket_min_dollar_volume * 4
    ):
        return "hot"
    if (
        predicted_hit_breadth <= 1
        or median_spread > settings.premarket_max_spread_pct * 1.10
        or median_dollar_volume < settings.premarket_min_dollar_volume * 1.5
    ):
        return "cold"
    return "normal"


def regime_limits(regime: str) -> dict[str, int]:
    return REGIME_LIMITS.get(regime, REGIME_LIMITS["normal"]).copy()


def market_date(value: datetime) -> str:
    return value.astimezone(EASTERN).date().isoformat()


def current_day_pnl(
    *,
    now: datetime,
    positions: list[PaperPosition],
) -> float:
    current_market_date = market_date(now)
    pnl = 0.0
    for row in positions:
        if market_date(row.opened_at) != current_market_date:
            continue
        pnl += row.total_pnl
    return pnl


def current_open_risk(positions: list[PaperPosition]) -> float:
    total = 0.0
    for row in positions:
        stop = row.planned_stop_price or row.stop_price
        last_price = row.last_price or row.average_entry_price
        if stop is None or last_price <= stop:
            continue
        total += (last_price - stop) * row.quantity
    return total


def daily_loss_lock_marker(market_date_value: str) -> str:
    return f"{STATE_NOTE_PREFIX}daily_loss_lock:{market_date_value}"


def state_markers(notes: list[str]) -> list[str]:
    return [note for note in notes if note.startswith(STATE_NOTE_PREFIX)]


def has_daily_loss_lock(notes: list[str], market_date_value: str) -> bool:
    return daily_loss_lock_marker(market_date_value) in notes


def live_quote_is_valid(
    bid_price: float | None,
    ask_price: float | None,
) -> bool:
    if bid_price is None or ask_price is None:
        return False
    if bid_price <= 0 or ask_price <= 0:
        return False
    return ask_price >= bid_price


def normalize_market_status(status: str | None) -> str:
    if not status:
        return ""
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", status.strip())
    normalized = normalized.lower().replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", normalized).strip("_")


def market_status_blocks_trading(status: str | None) -> bool:
    normalized = normalize_market_status(status)
    if normalized in BLOCKED_MARKET_STATUSES:
        return True
    return any(
        token in normalized
        for token in ("halt", "pause", "paused", "suspend", "suspended", "luld")
    )


def effective_market_data_timestamp(
    *,
    snapshot_updated_at: datetime | None,
    trade_timestamp: datetime | None,
    trade_price: float | None,
    quote_timestamp: datetime | None,
    bid_price: float | None,
    ask_price: float | None,
) -> datetime | None:
    component_timestamps: list[datetime] = []
    if trade_timestamp is not None and trade_price is not None and trade_price > 0:
        component_timestamps.append(trade_timestamp)
    if quote_timestamp is not None and live_quote_is_valid(bid_price, ask_price):
        component_timestamps.append(quote_timestamp)
    if component_timestamps:
        return min(component_timestamps)
    return snapshot_updated_at


def halt_suspected(
    row: MarketActivity,
    *,
    market_phase: str,
    halt_seconds: int,
) -> bool:
    if market_phase not in {"premarket", "regular"}:
        return False
    if row.data_age_seconds is None:
        return False
    return row.data_age_seconds > float(max(halt_seconds, 1))


def is_premarket_entry_window(now: datetime) -> bool:
    eastern = now.astimezone(EASTERN)
    if eastern.weekday() >= 5:
        return False
    session_time = eastern.timetz().replace(tzinfo=None)
    return time(4, 0) <= session_time < time(9, 30)


def is_winner_add_window(now: datetime) -> bool:
    eastern = now.astimezone(EASTERN)
    if eastern.weekday() >= 5:
        return False
    session_time = eastern.timetz().replace(tzinfo=None)
    return time(9, 30) <= session_time <= time(10, 30)
