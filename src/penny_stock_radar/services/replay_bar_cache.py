from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..models import MarketActivity

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PreparedMinuteBar:
    symbol: str
    market_date: str
    market_phase: str
    bar_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    bid_price: float | None
    ask_price: float | None
    bid_price_missing: bool
    ask_price_missing: bool
    spread_pct: float | None

    @classmethod
    def from_row(cls, row: Any) -> PreparedMinuteBar:
        return cls(
            symbol=str(row["symbol"]).upper(),
            market_date=str(row["market_date"]),
            market_phase=str(row["market_phase"] or "premarket"),
            bar_at=_parse_dt(row["bar_at"]),
            open_price=float(row["open_price"] or 0.0),
            high_price=float(row["high_price"] or 0.0),
            low_price=float(row["low_price"] or 0.0),
            close_price=float(row["close_price"] or 0.0),
            volume=float(row["volume"] or 0.0),
            bid_price=_positive_float(row["bid_price"]),
            ask_price=_positive_float(row["ask_price"]),
            bid_price_missing=row["bid_price"] is None,
            ask_price_missing=row["ask_price"] is None,
            spread_pct=float(row["spread_pct"]) if row["spread_pct"] is not None else None,
        )


@dataclass(frozen=True)
class PreparedSymbolSeries:
    symbol: str
    bars: tuple[PreparedMinuteBar, ...]
    first_open: float | None
    cumulative_volume: tuple[float, ...]
    cumulative_dollar_volume: tuple[float, ...]

    @classmethod
    def from_bars(cls, symbol: str, bars: list[PreparedMinuteBar]) -> PreparedSymbolSeries:
        sorted_bars = tuple(_ensure_ordered_bars(bars))
        first_open = _positive_float(sorted_bars[0].open_price) if sorted_bars else None
        cumulative_volume: list[float] = []
        cumulative_dollar_volume: list[float] = []
        volume_total = 0.0
        dollar_total = 0.0
        for bar in sorted_bars:
            volume_total += bar.volume
            dollar_total += bar.close_price * bar.volume
            cumulative_volume.append(volume_total)
            cumulative_dollar_volume.append(dollar_total)
        return cls(
            symbol=symbol,
            bars=sorted_bars,
            first_open=first_open,
            cumulative_volume=tuple(cumulative_volume),
            cumulative_dollar_volume=tuple(cumulative_dollar_volume),
        )


@dataclass(frozen=True)
class PreparedBarSnapshot:
    series: PreparedSymbolSeries
    index: int

    @property
    def current(self) -> PreparedMinuteBar:
        return self.series.bars[self.index]

    @property
    def volume(self) -> float:
        return self.series.cumulative_volume[self.index]

    @property
    def dollar_volume(self) -> float:
        return self.series.cumulative_dollar_volume[self.index]


@dataclass(frozen=True)
class ReplayBarSeriesCache:
    series_by_symbol: Mapping[str, PreparedSymbolSeries]

    @classmethod
    def from_rows(cls, rows: list[Any]) -> ReplayBarSeriesCache:
        grouped: dict[str, list[PreparedMinuteBar]] = {}
        for row in rows:
            bar = PreparedMinuteBar.from_row(row)
            grouped.setdefault(bar.symbol, []).append(bar)
        return cls(
            series_by_symbol={
                symbol: PreparedSymbolSeries.from_bars(symbol, grouped[symbol])
                for symbol in sorted(grouped)
            }
        )

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.series_by_symbol)

    @property
    def loaded_bar_count(self) -> int:
        return sum(len(series.bars) for series in self.series_by_symbol.values())

    @property
    def loaded_symbol_count(self) -> int:
        return len(self.series_by_symbol)

    def __bool__(self) -> bool:
        return self.loaded_bar_count > 0

    @property
    def has_missing_bid_ask(self) -> bool:
        return any(
            bar.bid_price_missing or bar.ask_price_missing
            for series in self.series_by_symbol.values()
            for bar in series.bars
        )

    def simulated_times(self, cutoff_at: datetime) -> list[datetime]:
        return sorted(
            {
                bar.bar_at
                for series in self.series_by_symbol.values()
                for bar in series.bars
                if bar.bar_at >= cutoff_at
            }
        )

    def cursor(self) -> ReplayBarCursor:
        return ReplayBarCursor(self)


class ReplayBarCursor:
    def __init__(self, cache: ReplayBarSeriesCache) -> None:
        self._symbol_cursors = {
            symbol: _SymbolCursor(series)
            for symbol, series in cache.series_by_symbol.items()
        }
        self._activity_cache: dict[tuple[str, int], MarketActivity] = {}
        self._latest_snapshots: dict[str, PreparedBarSnapshot] = {}

    def activity_at(
        self,
        *,
        simulated_time: datetime,
        scanner: Any,
        watchlist_rank: Mapping[str, int],
        watchlist_score: Mapping[str, float],
        predictions_by_symbol: Mapping[str, object],
    ) -> list[MarketActivity]:
        rows: list[MarketActivity] = []
        for symbol, cursor in self._symbol_cursors.items():
            snapshot = cursor.advance_to(simulated_time)
            if snapshot is None:
                continue
            self._latest_snapshots[symbol] = snapshot
            cache_key = (symbol, snapshot.index)
            cached = self._activity_cache.get(cache_key)
            if cached is None:
                cached = _activity_from_snapshot(
                    snapshot=snapshot,
                    simulated_time=simulated_time,
                    scanner=scanner,
                    watchlist_rank=watchlist_rank,
                    watchlist_score=watchlist_score,
                    predicted=symbol in predictions_by_symbol,
                )
                self._activity_cache[cache_key] = cached
            elif cached.created_at != simulated_time:
                cached = cached.model_copy(update={"created_at": simulated_time})
            rows.append(cached)
        return rows

    def latest_snapshot(self, symbol: str) -> PreparedBarSnapshot | None:
        return self._latest_snapshots.get(symbol.upper())


class _SymbolCursor:
    def __init__(self, series: PreparedSymbolSeries) -> None:
        self._series = series
        self._index = -1

    def advance_to(self, simulated_time: datetime) -> PreparedBarSnapshot | None:
        next_index = self._index + 1
        while next_index < len(self._series.bars):
            if self._series.bars[next_index].bar_at > simulated_time:
                break
            self._index = next_index
            next_index += 1
        if self._index < 0:
            return None
        return PreparedBarSnapshot(series=self._series, index=self._index)


def _activity_from_snapshot(
    *,
    snapshot: PreparedBarSnapshot,
    simulated_time: datetime,
    scanner: Any,
    watchlist_rank: Mapping[str, int],
    watchlist_score: Mapping[str, float],
    predicted: bool,
) -> MarketActivity:
    current = snapshot.current
    first_open = snapshot.series.first_open
    last_price = _positive_float(current.close_price)
    spread_pct = _spread_pct(current, bid=current.bid_price, ask=current.ask_price)
    pct_change = (
        ((last_price / first_open) - 1.0) * 100.0
        if first_open is not None and last_price is not None
        else None
    )
    label, score, reasons = scanner._analysis(
        market_phase=current.market_phase,
        pct_change=pct_change,
        dollar_volume=snapshot.dollar_volume,
        spread_pct=spread_pct,
        predicted=predicted,
        watchlist_score=watchlist_score.get(current.symbol),
    )
    return MarketActivity(
        symbol=current.symbol,
        market_phase=current.market_phase,
        source="historical_minute_bars",
        last_price=last_price,
        bid_price=current.bid_price,
        ask_price=current.ask_price,
        bar_high_price=_positive_float(current.high_price),
        bar_low_price=_positive_float(current.low_price),
        previous_close=first_open,
        pct_change=pct_change,
        volume=snapshot.volume,
        dollar_volume=snapshot.dollar_volume,
        trade_size=int(current.volume or 0),
        spread_pct=spread_pct,
        market_status=None,
        market_data_at=current.bar_at,
        data_age_seconds=0.0,
        has_live_trade=last_price is not None,
        has_live_quote=(
            current.bid_price is not None
            and current.ask_price is not None
            and current.ask_price >= current.bid_price
        ),
        watchlist_rank=watchlist_rank.get(current.symbol),
        watchlist_score=watchlist_score.get(current.symbol),
        predicted=predicted,
        analysis_label=label,
        analysis_score=score,
        reasons=reasons,
        created_at=simulated_time,
    )


def _parse_dt(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def _ensure_ordered_bars(bars: list[PreparedMinuteBar]) -> list[PreparedMinuteBar]:
    if all(left.bar_at <= right.bar_at for left, right in zip(bars, bars[1:], strict=False)):
        return bars
    return sorted(bars, key=lambda row: row.bar_at)


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0 else None


def _spread_pct(
    bar: PreparedMinuteBar,
    *,
    bid: float | None,
    ask: float | None,
) -> float | None:
    if bar.spread_pct is not None:
        return bar.spread_pct
    if bid is not None and ask is not None and ask >= bid:
        midpoint = (bid + ask) / 2.0
        return (ask - bid) / midpoint if midpoint > 0 else None
    return None
