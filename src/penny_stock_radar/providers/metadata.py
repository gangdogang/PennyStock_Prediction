from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from ..models import MetadataRecord


class YFinanceMetadataProvider:
    """Best-effort metadata provider for Milestone 1.

    This intentionally focuses on EOD/basic intraday discovery. It is not suitable for
    tick-by-tick premarket tape analysis.
    """

    def fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Timestamp.utcnow is deprecated and will be removed",
                category=Warning,
            )
            frame = yf.download(
                tickers=symbols,
                period="5d",
                interval="1d",
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True,
            )
        prices: dict[str, float] = {}
        if getattr(frame, "empty", True):
            return prices

        if len(symbols) == 1:
            close_series = frame.get("Close")
            if close_series is not None and not close_series.dropna().empty:
                prices[symbols[0]] = float(close_series.dropna().iloc[-1])
            return prices

        for symbol in symbols:
            try:
                close_series = frame[symbol]["Close"].dropna()
            except Exception:
                continue
            if not close_series.empty:
                prices[symbol] = float(close_series.iloc[-1])
        return prices

    def fetch_metadata(
        self,
        symbols: list[str],
        max_workers: int,
        prices: dict[str, float] | None = None,
    ) -> dict[str, MetadataRecord]:
        prices = prices or {}
        metadata: dict[str, MetadataRecord] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._fetch_one, symbol, prices.get(symbol)): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    record = future.result()
                except Exception:
                    continue
                metadata[symbol] = record
        return metadata

    def _fetch_one(self, symbol: str, fallback_price: float | None) -> MetadataRecord:
        ticker = yf.Ticker(symbol)
        fast_info = {}
        info = {}

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Timestamp.utcnow is deprecated and will be removed",
                category=Warning,
            )
            try:
                fast_info = dict(ticker.fast_info)
            except Exception:
                fast_info = {}

            try:
                info = ticker.info or {}
            except Exception:
                info = {}

        price = (
            fast_info.get("lastPrice")
            or fast_info.get("regularMarketPrice")
            or info.get("regularMarketPrice")
            or info.get("currentPrice")
            or fallback_price
        )
        market_cap = fast_info.get("marketCap") or info.get("marketCap")
        shares_outstanding = (
            fast_info.get("shares")
            or info.get("sharesOutstanding")
            or info.get("shares")
        )
        float_shares = info.get("floatShares")
        quote_type = info.get("quoteType")

        return MetadataRecord(
            symbol=symbol,
            price=float(price) if price is not None else None,
            market_cap=int(market_cap) if market_cap else None,
            float_shares=int(float_shares) if float_shares else None,
            shares_outstanding=int(shares_outstanding) if shares_outstanding else None,
            quote_type=str(quote_type) if quote_type else None,
            sector=info.get("sectorDisp") or info.get("sector"),
            industry=info.get("industryDisp") or info.get("industry"),
        )
