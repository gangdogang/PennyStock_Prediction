from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .premkt_training_dataset import FEATURE_VERSION

FEATURE_PREFIX = "feature_"
TARGET_COLUMN = "label_winner"
NET_R_COLUMN = "label_net_r"
REQUIRED_COLUMNS = {
    "market_date",
    "symbol",
    "cutoff_at",
    TARGET_COLUMN,
    NET_R_COLUMN,
}


@dataclass(frozen=True)
class PremktModelArtifact:
    model: Any
    feature_columns: list[str]
    target_column: str
    fill_values: dict[str, float]
    model_type: str
    created_at: str
    notes: list[str]
    feature_version: str = FEATURE_VERSION


@dataclass(frozen=True)
class PremktModelTrainingResult:
    model_path: Path
    metrics_path: Path
    metrics: dict[str, Any]


class PremktModelTrainer:
    """Train a leakage-aware baseline model from premarket training CSV rows."""

    def train(
        self,
        *,
        dataset_path: Path,
        model_out: Path,
        metrics_out: Path,
        validation_start_date: str | None = None,
    ) -> PremktModelTrainingResult:
        dataset_path = Path(dataset_path)
        model_out = Path(model_out)
        metrics_out = Path(metrics_out)
        if not dataset_path.exists():
            raise FileNotFoundError(f"Premarket training dataset not found: {dataset_path}")

        raw_frame = pd.read_csv(dataset_path)
        feature_columns = _feature_columns(raw_frame.columns)
        _validate_input_columns(raw_frame.columns, feature_columns)

        frame = _labeled_rows(raw_frame)
        if frame.empty:
            raise ValueError("No rows with non-empty label_winner were found.")

        train_dates, validation_dates = split_market_dates(
            frame["market_date"],
            validation_start_date=validation_start_date,
        )
        train_frame = frame[frame["market_date"].isin(train_dates)].copy()
        validation_frame = frame[frame["market_date"].isin(validation_dates)].copy()
        if train_frame.empty or validation_frame.empty:
            raise ValueError("Chronological split produced an empty train or validation set.")

        x_train_raw = _numeric_features(train_frame, feature_columns)
        fill_values = _training_fill_values(x_train_raw)
        x_train = _apply_fill_values(x_train_raw, fill_values)
        x_validation = _apply_fill_values(
            _numeric_features(validation_frame, feature_columns),
            fill_values,
        )
        y_train = train_frame[TARGET_COLUMN].astype(int)
        y_validation = validation_frame[TARGET_COLUMN].astype(int)

        model, model_type = _fit_baseline_model(x_train, y_train)
        validation_scores = _positive_class_scores(model, x_validation)
        validation_predictions = model.predict(x_validation)

        created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        notes = [
            "Rows with blank or non-numeric label_winner are excluded before splitting.",
            "Split is chronological by unique market_date; no date appears in both train and validation.",
            "Feature NaN/blank values are filled with the training-set median; all-blank features fall back to 0.0.",
            "This phase validates a baseline premarket signal only; it is not trading performance evidence.",
        ]

        artifact = PremktModelArtifact(
            model=model,
            feature_columns=feature_columns,
            target_column=TARGET_COLUMN,
            fill_values=fill_values,
            model_type=model_type,
            created_at=created_at,
            notes=notes,
            feature_version=FEATURE_VERSION,
        )

        metrics = _build_metrics(
            created_at=created_at,
            dataset_path=dataset_path,
            model_path=model_out,
            raw_row_count=len(raw_frame),
            labeled_row_count=len(frame),
            excluded_unlabeled_row_count=len(raw_frame) - len(frame),
            train_frame=train_frame,
            validation_frame=validation_frame,
            train_dates=train_dates,
            validation_dates=validation_dates,
            feature_columns=feature_columns,
            model_type=model_type,
            y_validation=y_validation,
            validation_predictions=validation_predictions,
            validation_scores=validation_scores,
            notes=notes,
        )

        model_out.parent.mkdir(parents=True, exist_ok=True)
        metrics_out.parent.mkdir(parents=True, exist_ok=True)
        _joblib().dump(artifact, model_out)
        metrics_out.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return PremktModelTrainingResult(
            model_path=model_out,
            metrics_path=metrics_out,
            metrics=metrics,
        )


def split_market_dates(
    market_dates: Iterable[object],
    *,
    validation_start_date: str | None = None,
) -> tuple[list[str], list[str]]:
    unique_dates = sorted({_normalize_market_date(value) for value in market_dates})
    if len(unique_dates) < 2:
        raise ValueError("At least two unique market_date values are required for validation.")

    if validation_start_date:
        start = _normalize_market_date(validation_start_date)
        train_dates = [value for value in unique_dates if value < start]
        validation_dates = [value for value in unique_dates if value >= start]
    else:
        validation_count = max(1, math.ceil(len(unique_dates) * 0.20))
        validation_dates = unique_dates[-validation_count:]
        train_dates = unique_dates[:-validation_count]

    if not train_dates or not validation_dates:
        raise ValueError(
            "validation_start_date must leave at least one train date and one validation date."
        )
    return train_dates, validation_dates


def _validate_input_columns(columns: Iterable[str], feature_columns: list[str]) -> None:
    column_set = set(columns)
    missing = sorted(REQUIRED_COLUMNS - column_set)
    if missing:
        raise ValueError(f"Training dataset is missing required columns: {', '.join(missing)}")
    if not feature_columns:
        raise ValueError("Training dataset must contain at least one feature_* column.")


def _feature_columns(columns: Iterable[str]) -> list[str]:
    return [column for column in columns if column.startswith(FEATURE_PREFIX)]


def _labeled_rows(frame: pd.DataFrame) -> pd.DataFrame:
    labels = pd.to_numeric(frame[TARGET_COLUMN], errors="coerce")
    labeled = frame[labels.notna()].copy()
    labeled[TARGET_COLUMN] = labels[labels.notna()].astype(int)
    if not labeled[TARGET_COLUMN].isin([0, 1]).all():
        raise ValueError("label_winner must be binary 0/1 when present.")
    labeled["market_date"] = labeled["market_date"].map(_normalize_market_date)
    return labeled


def _numeric_features(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    return frame.loc[:, feature_columns].apply(pd.to_numeric, errors="coerce")


def _training_fill_values(features: pd.DataFrame) -> dict[str, float]:
    medians = features.median(numeric_only=True).reindex(features.columns).fillna(0.0)
    return {column: float(medians[column]) for column in features.columns}


def _apply_fill_values(features: pd.DataFrame, fill_values: dict[str, float]) -> pd.DataFrame:
    return features.fillna(fill_values).fillna(0.0)


def _fit_baseline_model(features: pd.DataFrame, target: pd.Series) -> tuple[Any, str]:
    sklearn = _sklearn_objects()
    if target.nunique() < 2:
        model = sklearn["DummyClassifier"](strategy="most_frequent")
        model.fit(features, target)
        return model, "DummyClassifier(most_frequent)"

    model = sklearn["Pipeline"](
        [
            ("scaler", sklearn["StandardScaler"]()),
            (
                "classifier",
                sklearn["LogisticRegression"](
                    class_weight="balanced",
                    max_iter=1000,
                    solver="liblinear",
                ),
            ),
        ]
    )
    model.fit(features, target)
    return model, "LogisticRegression(class_weight=balanced)"


def _positive_class_scores(model: Any, features: pd.DataFrame) -> list[float]:
    probabilities = model.predict_proba(features)
    classes = list(getattr(model, "classes_", []))
    if 1 not in classes:
        return [0.0 for _ in range(len(features))]
    positive_index = classes.index(1)
    return [float(value) for value in probabilities[:, positive_index]]


def _build_metrics(
    *,
    created_at: str,
    dataset_path: Path,
    model_path: Path,
    raw_row_count: int,
    labeled_row_count: int,
    excluded_unlabeled_row_count: int,
    train_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    train_dates: list[str],
    validation_dates: list[str],
    feature_columns: list[str],
    model_type: str,
    y_validation: pd.Series,
    validation_predictions: Any,
    validation_scores: list[float],
    notes: list[str],
) -> dict[str, Any]:
    sklearn = _sklearn_objects()
    validation_net_r = pd.to_numeric(validation_frame[NET_R_COLUMN], errors="coerce")
    top_count = max(1, math.ceil(len(validation_frame) * 0.10))
    ranked_scores = pd.Series(validation_scores, index=validation_frame.index)
    top_indices = ranked_scores.sort_values(ascending=False, kind="mergesort").index[:top_count]
    auc = None
    if y_validation.nunique() == 2:
        try:
            auc = _round_metric(sklearn["roc_auc_score"](y_validation, validation_scores))
        except ValueError:
            auc = None

    return {
        "created_at": created_at,
        "dataset_path": str(dataset_path),
        "model_path": str(model_path),
        "input_row_count": raw_row_count,
        "row_count": labeled_row_count,
        "excluded_unlabeled_row_count": excluded_unlabeled_row_count,
        "train_row_count": int(len(train_frame)),
        "validation_row_count": int(len(validation_frame)),
        "train_start_date": train_dates[0],
        "train_end_date": train_dates[-1],
        "validation_start_date": validation_dates[0],
        "validation_end_date": validation_dates[-1],
        "train_market_dates": train_dates,
        "validation_market_dates": validation_dates,
        "feature_columns": feature_columns,
        "target_column": TARGET_COLUMN,
        "model_type": model_type,
        "validation_accuracy": _round_metric(
            sklearn["accuracy_score"](y_validation, validation_predictions)
        ),
        "validation_auc": auc,
        "validation_precision_at_10pct": _round_metric(y_validation.loc[top_indices].mean()),
        "validation_top_decile_avg_label_net_r": _nullable_mean(
            validation_net_r.loc[top_indices]
        ),
        "validation_all_avg_label_net_r": _nullable_mean(validation_net_r),
        "notes": notes,
    }


def _nullable_mean(values: pd.Series) -> float | None:
    mean_value = values.dropna().mean()
    if pd.isna(mean_value):
        return None
    return _round_metric(mean_value)


def _round_metric(value: Any) -> float:
    return round(float(value), 6)


def _normalize_market_date(value: object) -> str:
    normalized = date.fromisoformat(str(value)[:10])
    return normalized.isoformat()


def _joblib() -> Any:
    try:
        import joblib
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "train-premkt-model requires joblib. Install the project with current dependencies."
        ) from exc
    return joblib


def _sklearn_objects() -> dict[str, Any]:
    try:
        from sklearn.dummy import DummyClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, roc_auc_score
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "train-premkt-model requires scikit-learn. Install the project with current dependencies."
        ) from exc
    return {
        "DummyClassifier": DummyClassifier,
        "LogisticRegression": LogisticRegression,
        "Pipeline": Pipeline,
        "StandardScaler": StandardScaler,
        "accuracy_score": accuracy_score,
        "roc_auc_score": roc_auc_score,
    }
