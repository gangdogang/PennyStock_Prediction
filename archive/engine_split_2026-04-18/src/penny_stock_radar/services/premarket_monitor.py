from __future__ import annotations

import math

import pandas as pd

from ..config import AppSettings
from ..models import PremarketSignal


class PremarketMonitor:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def analyze(self, ticks: pd.DataFrame) -> list[PremarketSignal]:
        if ticks.empty:
            return []

        signals: list[PremarketSignal] = []
        premarket = ticks[ticks["session"] == "premarket"].copy()
        if premarket.empty:
            return []

        for symbol, frame in premarket.groupby("symbol"):
            frame = frame.sort_values("timestamp")
            price = frame["price"].astype(float)
            size = frame["size"].astype(float)
            mid = (frame["bid"].astype(float) + frame["ask"].astype(float)) / 2
            spread_pct = (((frame["ask"] - frame["bid"]) / mid.replace(0, pd.NA)).fillna(0)).mean()
            total_seconds = max(
                (frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]).total_seconds(),
                60.0,
            )
            dollar_volume = float((price * size).sum())
            trade_count = int(len(frame))
            tps = trade_count / total_seconds
            size_mean = float(size.mean())
            size_std = float(size.std(ddof=0)) if trade_count > 1 else 0.0
            size_cv = size_std / size_mean if size_mean else 0.0
            vwap = float((price * size).sum() / size.sum())
            baseline = float(frame["baseline_avg_volume"].dropna().iloc[0]) if frame["baseline_avg_volume"].notna().any() else max(size.sum(), 1.0)
            premarket_rvol = float(size.sum() / baseline) if baseline else 0.0

            round_break = self._round_level_break(price)
            tape_anomaly = trade_count >= self.settings.premarket_min_trade_count and size_cv <= 0.30 and tps >= 0.01
            close_above_vwap = float(price.iloc[-1]) > vwap

            reasons: list[str] = []
            score = 0.0
            if dollar_volume >= self.settings.premarket_min_dollar_volume:
                score += 2.0
                reasons.append("dollar_volume_ok")
            if trade_count >= self.settings.premarket_min_trade_count:
                score += 1.0
                reasons.append("trade_count_ok")
            if premarket_rvol >= 2.0:
                score += 1.0
                reasons.append("rvol_ok")
            if spread_pct <= self.settings.premarket_max_spread_pct:
                score += 1.0
                reasons.append("spread_ok")
            if close_above_vwap:
                score += 1.0
                reasons.append("close_above_pm_vwap")
            if round_break:
                score += 0.5
                reasons.append("round_level_break")
            if tape_anomaly:
                score += 1.0
                reasons.append("tape_anomaly")

            signals.append(
                PremarketSignal(
                    symbol=symbol,
                    premarket_high=float(price.max()),
                    premarket_low=float(price.min()),
                    premarket_close=float(price.iloc[-1]),
                    premarket_vwap=vwap,
                    premarket_rvol=premarket_rvol,
                    dollar_volume=dollar_volume,
                    trade_count=trade_count,
                    tps=tps,
                    size_mean=size_mean,
                    size_std=size_std,
                    size_cv=size_cv,
                    spread_pct=float(spread_pct),
                    round_level_break=round_break,
                    tape_anomaly=tape_anomaly,
                    quality_score=score,
                    reasons=reasons,
                )
            )

        signals.sort(key=lambda signal: (-signal.quality_score, signal.symbol))
        return signals

    def _round_level_break(self, prices: pd.Series) -> bool:
        price_min = float(prices.min())
        price_max = float(prices.max())
        start = math.floor(price_min)
        end = math.ceil(price_max)
        for level in range(max(1, start), end + 1):
            if price_min < level <= price_max:
                return True
        return False
