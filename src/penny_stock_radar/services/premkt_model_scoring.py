from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from ..db import fetch_historical_minute_bars
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
        notes: list[str] = []
        if market_date is None:
            return self._fallback_score(
                symbol=symbol,
                notes=["model_scoring_unavailable: market_date is required."],
            )

        market_date_str = _coerce_market_date(market_date)
        feature_cutoff = cutoff_at or datetime.combine(
            date.fromisoformat(market_date_str),
            self.cutoff_time,
            tzinfo=EASTERN,
        )
        rows = fetch_historical_minute_bars(
            self.database_path,
            market_date=market_date_str,
            symbol=symbol,
        )
        feature_rows = [
            row for row in rows if _parse_bar_at(row["bar_at"]) < feature_cutoff
        ]
        if not feature_rows:
            return self._fallback_score(
                symbol=symbol,
                notes=[
                    "model_scoring_unavailable: no historical minute bars before cutoff."
                ],
            )

        row = build_premarket_feature_row(
            symbol=symbol.upper(),
            market_date=market_date_str,
            cutoff_at=feature_cutoff,
            feature_rows=feature_rows,
        )
        missing = [
            column
            for column in self.feature_columns
            if column not in row or _is_missing(row[column])
        ]
        if missing:
            notes.append(
                "missing_features_filled: "
                + ", ".join(f"{name}={self.fill_values.get(name, 0.0)}" for name in missing)
            )

        features = pd.DataFrame(
            [
                {
                    column: row.get(column, self.fill_values.get(column, 0.0))
                    for column in self.feature_columns
                }
            ],
            columns=self.feature_columns,
        ).apply(pd.to_numeric, errors="coerce")
        features = features.fillna(self.fill_values).fillna(0.0)
        try:
            ml_score = _score_model_probability(self.artifact.model, features) * 100.0
        except Exception as exc:
            return self._fallback_score(
                symbol=symbol,
                notes=[f"model_scoring_unavailable: {type(exc).__name__}: {exc}"],
            )

        return PremktModelScore(
            symbol=symbol.upper(),
            ml_score=round(max(0.0, min(ml_score, 100.0)), 2),
            model_path=str(self.model_path),
            model_feature_version=self.feature_version,
            missing_feature_names=missing,
            scoring_notes=notes,
        )

    def _fallback_score(self, *, symbol: str, notes: list[str]) -> PremktModelScore:
        return PremktModelScore(
            symbol=symbol.upper(),
            ml_score=None,
            model_path=str(self.model_path),
            model_feature_version=self.feature_version,
            missing_feature_names=[],
            scoring_notes=notes,
        )


def _score_model_probability(model: Any, features: pd.DataFrame) -> float:
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(features)
        classes = list(getattr(model, "classes_", []))
        if classes and 1 in classes:
            positive_index = classes.index(1)
        else:
            positive_index = 1 if len(probabilities[0]) > 1 else 0
        return float(probabilities[0][positive_index])

    prediction = model.predict(features)[0]
    return float(prediction)


def _coerce_market_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


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
