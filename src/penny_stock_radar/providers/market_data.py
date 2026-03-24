from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from ..models import MarketTick

EASTERN = ZoneInfo("America/New_York")


class MarketDataProvider(Protocol):
    def load_ticks(self, path: Path) -> pd.DataFrame: ...


class ReplayCSVMarketDataProvider:
    def load_ticks(self, path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(EASTERN)
        return frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    def save_ticks(self, ticks: list[MarketTick], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timestamp",
                    "symbol",
                    "session",
                    "price",
                    "size",
                    "bid",
                    "ask",
                    "side",
                    "baseline_avg_volume",
                ],
            )
            writer.writeheader()
            for tick in ticks:
                writer.writerow(
                    {
                        "timestamp": tick.timestamp.astimezone(EASTERN).isoformat(),
                        "symbol": tick.symbol,
                        "session": tick.session,
                        "price": tick.price,
                        "size": tick.size,
                        "bid": tick.bid,
                        "ask": tick.ask,
                        "side": tick.side,
                        "baseline_avg_volume": tick.baseline_avg_volume,
                    }
                )


@dataclass
class MockSymbolSpec:
    symbol: str
    base_price: float
    scenario: str
    baseline_avg_volume: float = 40_000.0


class MockTickGenerator:
    """Generates deterministic replay data for premarket and regular-session development."""

    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    def generate_session(
        self,
        specs: list[MockSymbolSpec],
        session_date: date | None = None,
    ) -> list[MarketTick]:
        if session_date is None:
            session_date = datetime.now(EASTERN).date()

        ticks: list[MarketTick] = []
        for index, spec in enumerate(specs):
            rng = random.Random(self.seed + index)
            ticks.extend(self._generate_symbol_ticks(spec, session_date, rng))
        ticks.sort(key=lambda tick: (tick.symbol, tick.timestamp))
        return ticks

    def _generate_symbol_ticks(
        self,
        spec: MockSymbolSpec,
        session_date: date,
        rng: random.Random,
    ) -> list[MarketTick]:
        timestamps = []
        premarket_start = datetime.combine(session_date, time(8, 0), tzinfo=EASTERN)
        regular_start = datetime.combine(session_date, time(9, 30), tzinfo=EASTERN)

        for minute in range(90):
            timestamps.append(("premarket", premarket_start + timedelta(minutes=minute)))
        for minute in range(60):
            timestamps.append(("regular", regular_start + timedelta(minutes=minute)))

        if spec.scenario == "continuation":
            premarket_trend = 0.0045
            regular_trend = 0.0032
            spread = 0.015
            size_base = 5500
        elif spec.scenario == "fade":
            premarket_trend = 0.0050
            regular_trend = -0.0035
            spread = 0.025
            size_base = 4800
        else:
            premarket_trend = 0.0020
            regular_trend = -0.0010
            spread = 0.05
            size_base = 2200

        price = spec.base_price
        ticks: list[MarketTick] = []
        for idx, (session, ts) in enumerate(timestamps):
            session_multiplier = 1.0 if session == "premarket" else 1.4
            drift = premarket_trend if session == "premarket" else regular_trend
            noise = (rng.random() - 0.5) * 0.01

            if spec.scenario == "fakeout" and session == "regular" and idx > 100:
                drift -= 0.003
            if spec.scenario == "continuation" and session == "regular" and idx > 100:
                drift += 0.0015

            price = max(0.2, price * (1 + drift + noise))
            size = max(100, int(size_base * session_multiplier * (0.8 + rng.random() * 0.4)))

            # Simulate periodic round-number attacks around 1, 2, 3, ...
            round_push = 0.0
            if abs(price - round(price)) < 0.05:
                round_push = 0.01 if spec.scenario != "fakeout" else -0.005
            price = max(0.2, price * (1 + round_push))

            effective_spread = spread * (1.8 if spec.scenario == "fakeout" and session == "premarket" else 1.0)
            bid = max(0.01, price * (1 - effective_spread / 2))
            ask = price * (1 + effective_spread / 2)
            side = "buy" if drift + round_push >= 0 else "sell"

            ticks.append(
                MarketTick(
                    timestamp=ts,
                    symbol=spec.symbol,
                    session=session,
                    price=round(price, 4),
                    size=size,
                    bid=round(bid, 4),
                    ask=round(ask, 4),
                    side=side,
                    baseline_avg_volume=spec.baseline_avg_volume,
                )
            )
        return ticks
