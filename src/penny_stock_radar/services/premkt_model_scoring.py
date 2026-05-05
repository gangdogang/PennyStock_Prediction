from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from ..db import fetch_historical_minute_bars_for_symbols
from .replay_bar_cache import PreparedMinuteBar, ReplayBarSeriesCache
from .premkt_training_dataset import (
    EASTERN,
    FEATURE_VERSION,
    build_premarket_feature_row,
    _parse_bar_at,
)


@dataclass(frozen=True)
class PremktModelScore:
    symbol: str
    ml_score: float | None
    model_path: str
    model_feature_version: str
    missing_feature_names: list[str]
    scoring_notes: list[str]

    @property
    def missing_feature_count(self) -> int:
        return len(self.missing_feature_names)


class PremktModelScorer:
    """Load a trained premarket artifact and score aligned feature rows."""

    def __init__(
        self,
        *,
        model_path: Path,
        database_path: Path,
        cutoff_time: time = time(8, 0),
    ) -> None:
        self.model_path = Path(model_path)
        self.database_path = Path(database_path)
        self.cutoff_time = cutoff_time
        self.artifact = _joblib().load(self.model_path)
        self.feature_columns = list(getattr(self.artifact, "feature_columns", []))
        if not self.feature_columns:
            raise ValueError("Premarket model artifact has no feature_columns.")
        self.fill_values = {
            str(key): float(value)
            for key, value in dict(getattr(self.artifact, "fill_values", {})).items()
        }
        self.feature_version = str(
            getattr(self.artifact, "feature_version", FEATURE_VERSION)
        )

    def score_symbol(
        self,
        *,
        symbol: str,
        market_date: date | str | None,
        cutoff_at: datetime | None,
    ) -> PremktModelScore:
        scores = self.score_symbols(
            symbols=[symbol],
            market_date=market_date,
            cutoff_at=cutoff_at,
        )
        return scores.get(symbol.upper()) or self.fallback_score(
            symbol=symbol,
            notes=["model_scoring_unavailable: no historical minute bars before cutoff."],
        )

    def score_symbols(
        self,
        *,
        symbols: list[str],
        market_date: date | str | None,
        cutoff_at: datetime | None,
        bar_cache: ReplayBarSeriesCache | None = None,
    ) -> dict[str, PremktModelScore]:
        normalized_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        if not normalized_symbols:
            return {}
        notes: list[str] = []
        if market_date is None:
            return {
                symbol: self.fallback_score(
                    symbol=symbol,
                    notes=["model_scoring_unavailable: market_date is required."],
                )
                for symbol in normalized_symbols
            }

        market_date_str = _coerce_market_date(market_date)
        feature_cutoff = cutoff_at or datetime.combine(
            date.fromisoformat(market_date_str),
            self.cutoff_time,
            tzinfo=EASTERN,
        )
        rows_by_symbol = self._rows_by_symbol(
            market_date=market_date_str,
            symbols=normalized_symbols,
            feature_cutoff=feature_cutoff,
            bar_cache=bar_cache,
        )

        results: dict[str, PremktModelScore] = {}
        feature_payloads: list[dict[str, object]] = []
        feature_symbols: list[str] = []
        missing_by_symbol: dict[str, list[str]] = {}
        notes_by_symbol: dict[str, list[str]] = {}
        for symbol in normalized_symbols:
            feature_rows = [
                row
                for row in rows_by_symbol.get(symbol, [])
                if _parse_bar_at(row["bar_at"]) < feature_cutoff
            ]
            if not feature_rows:
                results[symbol] = self.fallback_score(
                    symbol=symbol,
                    notes=[
                        "model_scoring_unavailable: no historical minute bars before cutoff."
                    ],
                )
                continue
            row = build_premarket_feature_row(
                symbol=symbol,
                market_date=market_date_str,
                cutoff_at=feature_cutoff,
                feature_rows=feature_rows,
            )
            missing = [
                column
                for column in self.feature_columns
                if column not in row or _is_missing(row[column])
            ]
            row_notes = list(notes)
            if missing:
                row_notes.append(
                    "missing_features_filled: "
                    + ", ".join(f"{name}={self.fill_values.get(name, 0.0)}" for name in missing)
                )
            missing_by_symbol[symbol] = missing
            notes_by_symbol[symbol] = row_notes
            feature_symbols.append(symbol)
            feature_payloads.append(
                {
                    column: row.get(column, self.fill_values.get(column, 0.0))
                    for column in self.feature_columns
                }
            )
        if not feature_payloads:
            return results

        features = pd.DataFrame(feature_payloads, columns=self.feature_columns).apply(
            pd.to_numeric,
            errors="coerce",
        )
        features = features.fillna(self.fill_values).fillna(0.0)
        try:
            probabilities = _score_model_probabilities(self.artifact.model, features)
        except Exception as exc:
            for symbol in feature_symbols:
                results[symbol] = self.fallback_score(
                    symbol=symbol,
                    notes=[f"model_scoring_unavailable: {type(exc).__name__}: {exc}"],
                )
            return results

        for symbol, probability in zip(feature_symbols, probabilities, strict=False):
            ml_score = probability * 100.0
            results[symbol] = PremktModelScore(
                symbol=symbol,
                ml_score=round(max(0.0, min(ml_score, 100.0)), 2),
                model_path=str(self.model_path),
                model_feature_version=self.feature_version,
                missing_feature_names=missing_by_symbol.get(symbol, []),
                scoring_notes=notes_by_symbol.get(symbol, []),
            )
        return results

    def fallback_score(self, *, symbol: str, notes: list[str]) -> PremktModelScore:
        return PremktModelScore(
            symbol=symbol.upper(),
            ml_score=None,
            model_path=str(self.model_path),
            model_feature_version=self.feature_version,
            missing_feature_names=[],
            scoring_notes=notes,
        )

    def _rows_by_symbol(
        self,
        *,
        market_date: str,
        symbols: list[str],
        feature_cutoff: datetime,
        bar_cache: ReplayBarSeriesCache | None,
    ) -> dict[str, list[Any]]:
        rows_by_symbol: dict[str, list[Any]] = {symbol: [] for symbol in symbols}
        if bar_cache is not None:
            for symbol in symbols:
                series = bar_cache.series_by_symbol.get(symbol)
                if series is None:
                    continue
                rows_by_symbol[symbol] = [
                    _prepared_bar_to_feature_row(bar)
                    for bar in series.bars
                    if bar.bar_at < feature_cutoff
                ]
            return rows_by_symbol

        rows = fetch_historical_minute_bars_for_symbols(
            self.database_path,
            market_date=market_date,
            symbols=symbols,
        )
        for row in rows:
            rows_by_symbol.setdefault(str(row["symbol"]).upper(), []).append(row)
        return rows_by_symbol


def _score_model_probabilities(model: Any, features: pd.DataFrame) -> list[float]:
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(features)
        classes = list(getattr(model, "classes_", []))
        if classes and 1 in classes:
            positive_index = classes.index(1)
        else:
            positive_index = 1 if len(probabilities[0]) > 1 else 0
        return [float(row[positive_index]) for row in probabilities]

    return [float(prediction) for prediction in model.predict(features)]


def _coerce_market_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _prepared_bar_to_feature_row(bar: PreparedMinuteBar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "market_date": bar.market_date,
        "bar_at": bar.bar_at.isoformat(),
        "open_price": bar.open_price,
        "high_price": bar.high_price,
        "low_price": bar.low_price,
        "close_price": bar.close_price,
        "volume": bar.volume,
    }


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _joblib() -> Any:
    try:
        import joblib
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Premarket model scoring requires joblib. Install the project dependencies."
        ) from exc
    return joblib
