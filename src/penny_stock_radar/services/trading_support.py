from __future__ import annotations

from datetime import datetime, time
from statistics import median
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..models import MarketActivity

EASTERN = ZoneInfo("America/New_York")

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
