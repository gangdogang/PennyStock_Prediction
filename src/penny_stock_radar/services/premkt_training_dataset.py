from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..db import fetch_historical_minute_bars

EASTERN = ZoneInfo("America/New_York")

FEATURE_VERSION = "premkt_training_dataset_v1"

PREMKT_FEATURE_COLUMNS = [
    "feature_premarket_volume",
    "feature_premarket_dollar_volume",
    "feature_premarket_return_pct",
    "feature_premarket_high_pct",
    "feature_premarket_low_pct",
    "feature_premarket_last_price",
    "feature_premarket_vwap",
    "feature_premarket_vwap_extension_pct",
    "feature_premarket_bar_count",
]

PREMKT_TRAINING_COLUMNS = [
    "market_date",
    "symbol",
    "cutoff_at",
    *PREMKT_FEATURE_COLUMNS,
    "label_future_return_pct",
    "label_max_gain_pct",
    "label_max_loss_pct",
    "label_winner",
    "label_net_r",
]


@dataclass(frozen=True)
class PremktTrainingDatasetSummary:
    output_path: Path
    start_date: str
    end_date: str
    row_count: int
    unlabeled_row_count: int


class PremktTrainingDatasetBuilder:
    """Build point-in-time premarket feature rows from historical minute bars."""

    def __init__(
        self,
        database_path: Path,
        *,
        cutoff_time: time = time(8, 0),
        label_risk_pct: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        self.cutoff_time = cutoff_time
        self.label_risk_pct = label_risk_pct

    def build_csv(
        self,
        *,
        start_date: str,
        end_date: str,
        output_path: Path,
    ) -> PremktTrainingDatasetSummary:
        rows = self.build_rows(start_date=start_date, end_date=end_date)
        resolved = Path(output_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PREMKT_TRAINING_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return PremktTrainingDatasetSummary(
            output_path=resolved,
            start_date=start_date,
            end_date=end_date,
            row_count=len(rows),
            unlabeled_row_count=sum(1 for row in rows if row["label_future_return_pct"] is None),
        )

    def build_rows(self, *, start_date: str, end_date: str) -> list[dict[str, object]]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if end < start:
            raise ValueError("end_date must be on or after start_date")
        if not self.database_path.exists():
            raise FileNotFoundError(f"Historical minute DB not found: {self.database_path}")

        rows: list[dict[str, object]] = []
        for market_date in _date_range(start, end):
            cutoff_at = datetime.combine(market_date, self.cutoff_time, tzinfo=EASTERN)
            db_rows = fetch_historical_minute_bars(
                self.database_path,
                market_date=market_date.isoformat(),
            )
            rows.extend(self._build_market_date_rows(db_rows, cutoff_at=cutoff_at))
        return rows

    def _build_market_date_rows(
        self,
        db_rows: Iterable[Any],
        *,
        cutoff_at: datetime,
    ) -> list[dict[str, object]]:
        by_symbol: dict[str, list[Any]] = {}
        for row in db_rows:
            by_symbol.setdefault(str(row["symbol"]).upper(), []).append(row)

        dataset_rows: list[dict[str, object]] = []
        for symbol in sorted(by_symbol):
            symbol_rows = sorted(
                by_symbol[symbol],
                key=lambda row: _parse_bar_at(row["bar_at"]),
            )
            feature_rows = [
                row for row in symbol_rows if _parse_bar_at(row["bar_at"]) < cutoff_at
            ]
            if not feature_rows:
                continue
            label_rows = [
                row for row in symbol_rows if _parse_bar_at(row["bar_at"]) >= cutoff_at
            ]
            dataset_rows.append(
                self._build_symbol_row(
                    symbol=symbol,
                    market_date=str(feature_rows[-1]["market_date"]),
                    cutoff_at=cutoff_at,
                    feature_rows=feature_rows,
                    label_rows=label_rows,
                )
            )
        return dataset_rows

    def _build_symbol_row(
        self,
        *,
        symbol: str,
        market_date: str,
        cutoff_at: datetime,
        feature_rows: list[Any],
        label_rows: list[Any],
    ) -> dict[str, object]:
        last_price = _safe_positive_float(feature_rows[-1]["close_price"])
        row: dict[str, object] = {
            **build_premarket_feature_row(
                symbol=symbol,
                market_date=market_date,
                cutoff_at=cutoff_at,
                feature_rows=feature_rows,
            ),
            "label_future_return_pct": None,
            "label_max_gain_pct": None,
            "label_max_loss_pct": None,
            "label_winner": None,
            "label_net_r": None,
        }
        if label_rows:
            final_price = _safe_positive_float(label_rows[-1]["close_price"])
            max_high = max(_float(label_row["high_price"]) for label_row in label_rows)
            min_low = min(_float(label_row["low_price"]) for label_row in label_rows)
            future_return_pct = _pct(final_price, last_price)
            row.update(
                {
                    "label_future_return_pct": future_return_pct,
                    "label_max_gain_pct": _pct(max_high, last_price),
                    "label_max_loss_pct": _pct(min_low, last_price),
                    "label_winner": int((future_return_pct or 0.0) > 0.0),
                    "label_net_r": _round(
                        future_return_pct / self.label_risk_pct
                        if future_return_pct is not None and self.label_risk_pct > 0
                        else None
                    ),
                }
            )
        return row


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_premarket_feature_row(
    *,
    symbol: str,
    market_date: str,
    cutoff_at: datetime,
    feature_rows: list[Any],
) -> dict[str, object]:
    first_open = _safe_positive_float(feature_rows[0]["open_price"])
    last_price = _safe_positive_float(feature_rows[-1]["close_price"])
    volume = sum(_float(row["volume"]) for row in feature_rows)
    dollar_volume = sum(_float(row["close_price"]) * _float(row["volume"]) for row in feature_rows)
    vwap = dollar_volume / volume if volume > 0 else None
    high_price = max(_float(row["high_price"]) for row in feature_rows)
    low_price = min(_float(row["low_price"]) for row in feature_rows)

    return {
        "market_date": market_date,
        "symbol": symbol,
        "cutoff_at": cutoff_at.isoformat(),
        "feature_premarket_volume": _round(volume),
        "feature_premarket_dollar_volume": _round(dollar_volume),
        "feature_premarket_return_pct": _pct(last_price, first_open),
        "feature_premarket_high_pct": _pct(high_price, first_open),
        "feature_premarket_low_pct": _pct(low_price, first_open),
        "feature_premarket_last_price": _round(last_price),
        "feature_premarket_vwap": _round(vwap),
        "feature_premarket_vwap_extension_pct": _pct(last_price, vwap),
        "feature_premarket_bar_count": len(feature_rows),
    }


def _parse_bar_at(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def _float(value: object) -> float:
    if value is None:
        return 0.0
    return float(value)


def _safe_positive_float(value: object) -> float | None:
    number = _float(value)
    return number if number > 0 else None


def _pct(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline <= 0:
        return None
    return _round(((value / baseline) - 1.0) * 100.0)


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)
