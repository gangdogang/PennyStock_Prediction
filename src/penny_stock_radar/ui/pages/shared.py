from __future__ import annotations

from typing import Any

import pandas as pd


def first_row(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    subset = frame[frame["symbol"] == symbol]
    if subset.empty:
        return {}
    return subset.iloc[0].to_dict()


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    series = pd.to_numeric(frame[column], errors="coerce")
    return series.fillna(0.0)
