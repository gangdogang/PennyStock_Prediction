from __future__ import annotations

import pandas as pd

from ..config import AppSettings
from ..models import PremarketSignal, SessionDecision


class RegularSessionEngine:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def analyze(
        self,
        ticks: pd.DataFrame,
        premarket_signals: list[PremarketSignal],
    ) -> list[SessionDecision]:
        if ticks.empty or not premarket_signals:
            return []

        regular = ticks[ticks["session"] == "regular"].copy()
        if regular.empty:
            return []

        premarket_by_symbol = {signal.symbol: signal for signal in premarket_signals}
        decisions: list[SessionDecision] = []

        for symbol, frame in regular.groupby("symbol"):
            signal = premarket_by_symbol.get(symbol)
            if signal is None:
                continue

            frame = frame.sort_values("timestamp").reset_index(drop=True)
            prices = frame["price"].astype(float)
            sizes = frame["size"].astype(float)
            anchored_vwap = float((prices * sizes).cumsum().iloc[-1] / sizes.cumsum().iloc[-1])

            open_range = frame.head(self.settings.regular_opening_range_minutes)
            opening_range_high = float(open_range["price"].max())
            opening_range_low = float(open_range["price"].min())
            regular_high = float(prices.max())
            regular_low = float(prices.min())
            regular_close = float(prices.iloc[-1])
            price_std = float(prices.std(ddof=0)) if len(prices) > 1 else 0.0
            extension_z = (
                (regular_close - anchored_vwap) / price_std if price_std > 0 else 0.0
            )

            reasons: list[str] = []
            if regular_close > anchored_vwap:
                reasons.append("close_above_anchored_vwap")
            if regular_high > signal.premarket_high:
                reasons.append("broke_pm_high")
            if regular_close > opening_range_high:
                reasons.append("opening_range_breakout")
            if abs(extension_z) < self.settings.regular_extension_z_threshold:
                reasons.append("extension_ok")

            enter = (
                regular_close > anchored_vwap
                and regular_high > signal.premarket_high
                and regular_close > opening_range_high
                and extension_z <= self.settings.regular_extension_z_threshold
            )
            watch = (
                regular_close > anchored_vwap
                and regular_high >= signal.premarket_high * 0.98
            )

            decision = "ENTER" if enter else "WATCH" if watch else "AVOID"
            if decision == "AVOID" and regular_close < anchored_vwap:
                reasons.append("failed_avwap")
            if decision == "AVOID" and regular_close < signal.premarket_vwap:
                reasons.append("below_pm_vwap")

            entry_reference = max(signal.premarket_high, anchored_vwap)
            mfe_pct = ((regular_high - entry_reference) / entry_reference) * 100
            mae_pct = ((regular_low - entry_reference) / entry_reference) * 100

            decisions.append(
                SessionDecision(
                    symbol=symbol,
                    decision=decision,
                    anchored_vwap=anchored_vwap,
                    opening_range_high=opening_range_high,
                    opening_range_low=opening_range_low,
                    regular_high=regular_high,
                    regular_low=regular_low,
                    regular_close=regular_close,
                    extension_z=extension_z,
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                    reasons=reasons,
                )
            )

        decisions.sort(
            key=lambda item: (
                {"ENTER": 0, "WATCH": 1, "AVOID": 2}.get(item.decision, 3),
                item.extension_z,
                item.symbol,
            )
        )
        return decisions
