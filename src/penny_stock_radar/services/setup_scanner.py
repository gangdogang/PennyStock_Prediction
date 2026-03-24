from __future__ import annotations

import pandas as pd
import yfinance as yf

from ..models import SetupSignal


class SetupScanner:
    """Daily technical setup scanner for Milestone 2."""

    def scan_symbols(self, symbols: list[str]) -> dict[str, SetupSignal]:
        if not symbols:
            return {}

        frame = yf.download(
            tickers=symbols,
            period="6mo",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if getattr(frame, "empty", True):
            return {}

        signals: dict[str, SetupSignal] = {}
        for symbol in symbols:
            symbol_frame = self._extract_symbol_frame(frame, symbol, len(symbols) == 1)
            if symbol_frame is None or symbol_frame.empty:
                continue
            signal = self._build_signal(symbol, symbol_frame)
            signals[symbol] = signal
        return signals

    def _extract_symbol_frame(
        self,
        frame: pd.DataFrame,
        symbol: str,
        single_symbol: bool,
    ) -> pd.DataFrame | None:
        try:
            if single_symbol:
                symbol_frame = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
            else:
                symbol_frame = frame[symbol][["Open", "High", "Low", "Close", "Volume"]].copy()
        except Exception:
            return None
        return symbol_frame.dropna()

    def _build_signal(self, symbol: str, frame: pd.DataFrame) -> SetupSignal:
        close = frame["Close"]
        high = frame["High"]
        low = frame["Low"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        sma20 = close.rolling(20).mean()
        atr10 = tr.rolling(10).mean()
        atr20 = tr.rolling(20).mean()

        latest_close = float(close.iloc[-1])
        latest_sma20 = float(sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) else None
        latest_atr10 = float(atr10.iloc[-1]) if pd.notna(atr10.iloc[-1]) else None
        latest_atr20 = float(atr20.iloc[-1]) if pd.notna(atr20.iloc[-1]) else None

        above_sma20 = latest_sma20 is not None and latest_close > latest_sma20

        recent_window = frame.tail(10)
        prior_window = frame.iloc[-30:-10] if len(frame) >= 30 else frame.head(0)
        recent_range = None
        prior_range = None
        if not recent_window.empty:
            recent_range = (
                float(recent_window["High"].max() - recent_window["Low"].min()) / latest_close
            )
        if not prior_window.empty:
            prior_range = (
                float(prior_window["High"].max() - prior_window["Low"].min()) / latest_close
            )

        volatility_contraction = False
        if latest_atr10 is not None and latest_atr20 is not None:
            volatility_contraction = latest_atr10 <= latest_atr20
            if recent_range is not None and prior_range is not None:
                volatility_contraction = volatility_contraction and recent_range < prior_range

        notes: list[str] = []
        score = 0.0
        if above_sma20:
            score += 1.0
            notes.append("above_20dma")
        if volatility_contraction:
            score += 1.0
            notes.append("volatility_contraction")

        return SetupSignal(
            symbol=symbol,
            close=latest_close,
            sma20=latest_sma20,
            atr10=latest_atr10,
            atr20=latest_atr20,
            above_sma20=above_sma20,
            volatility_contraction=volatility_contraction,
            setup_score=score,
            notes=notes,
        )
