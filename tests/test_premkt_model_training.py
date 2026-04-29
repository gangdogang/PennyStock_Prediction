from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.services.premkt_model_training import PremktModelTrainer


EXPECTED_METRIC_KEYS = {
    "created_at",
    "dataset_path",
    "model_path",
    "row_count",
    "train_row_count",
    "validation_row_count",
    "train_start_date",
    "train_end_date",
    "validation_start_date",
    "validation_end_date",
    "feature_columns",
    "target_column",
    "model_type",
    "validation_accuracy",
    "validation_auc",
    "validation_precision_at_10pct",
    "validation_top_decile_avg_label_net_r",
    "validation_all_avg_label_net_r",
    "notes",
}


def test_premkt_model_training_uses_chronological_market_date_split(
    tmp_path: Path,
) -> None:
    dataset_path = _write_training_csv(tmp_path)

    result = PremktModelTrainer().train(
        dataset_path=dataset_path,
        model_out=tmp_path / "premkt_model.joblib",
        metrics_out=tmp_path / "metrics.json",
    )
    metrics = result.metrics

    assert metrics["train_market_dates"] == [
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
        "2026-04-04",
    ]
    assert metrics["validation_market_dates"] == ["2026-04-05"]
    assert metrics["train_end_date"] < metrics["validation_start_date"]


def test_premkt_model_training_keeps_market_dates_disjoint_with_explicit_validation_start(
    tmp_path: Path,
) -> None:
    dataset_path = _write_training_csv(tmp_path)

    result = PremktModelTrainer().train(
        dataset_path=dataset_path,
        model_out=tmp_path / "premkt_model.joblib",
        metrics_out=tmp_path / "metrics.json",
        validation_start_date="2026-04-04",
    )
    metrics = result.metrics

    train_dates = set(metrics["train_market_dates"])
    validation_dates = set(metrics["validation_market_dates"])
    assert train_dates.isdisjoint(validation_dates)
    assert metrics["train_market_dates"] == ["2026-04-01", "2026-04-02", "2026-04-03"]
    assert metrics["validation_market_dates"] == ["2026-04-04", "2026-04-05"]


def test_premkt_model_training_excludes_unlabeled_rows_and_non_feature_columns(
    tmp_path: Path,
) -> None:
    dataset_path = _write_training_csv(tmp_path)

    result = PremktModelTrainer().train(
        dataset_path=dataset_path,
        model_out=tmp_path / "premkt_model.joblib",
        metrics_out=tmp_path / "metrics.json",
    )
    metrics = result.metrics

    assert metrics["input_row_count"] == 11
    assert metrics["excluded_unlabeled_row_count"] == 1
    assert metrics["row_count"] == 10
    assert metrics["train_row_count"] + metrics["validation_row_count"] == metrics["row_count"]
    assert metrics["feature_columns"] == ["feature_alpha", "feature_beta", "feature_sparse"]
    assert "market_date" not in metrics["feature_columns"]
    assert "symbol" not in metrics["feature_columns"]
    assert "cutoff_at" not in metrics["feature_columns"]
    assert "label_net_r" not in metrics["feature_columns"]
    assert "rank" not in metrics["feature_columns"]


def test_train_premkt_model_cli_writes_expected_metrics_keys(tmp_path: Path) -> None:
    dataset_path = _write_training_csv(tmp_path)
    model_path = tmp_path / "ml" / "premkt_model.joblib"
    metrics_path = tmp_path / "ml" / "premkt_model_metrics.json"

    result = CliRunner().invoke(
        app,
        [
            "train-premkt-model",
            "--dataset",
            str(dataset_path),
            "--model-out",
            str(model_path),
            "--metrics-out",
            str(metrics_path),
        ],
    )

    assert result.exit_code == 0
    assert model_path.exists()
    assert metrics_path.exists()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert EXPECTED_METRIC_KEYS <= set(metrics)
    assert metrics["target_column"] == "label_winner"
    assert metrics["model_type"] == "LogisticRegression(class_weight=balanced)"


def _write_training_csv(tmp_path: Path) -> Path:
    path = tmp_path / "premkt_training.csv"
    fieldnames = [
        "market_date",
        "symbol",
        "cutoff_at",
        "rank",
        "feature_alpha",
        "feature_beta",
        "feature_sparse",
        "label_future_return_pct",
        "label_winner",
        "label_net_r",
    ]
    rows = [
        _row("2026-04-01", "AAA", 0.10, 10, 0, -0.20),
        _row("2026-04-01", "BBB", 1.10, 90, 1, 0.40),
        _row("2026-04-02", "AAA", 0.20, 20, 0, -0.10),
        _row("2026-04-02", "BBB", 1.20, 85, 1, 0.50),
        _row("2026-04-03", "AAA", 0.15, 22, 0, -0.30),
        _row("2026-04-03", "BBB", 1.25, 88, 1, 0.60),
        _row("2026-04-03", "CCC", 9.99, 99, "", ""),
        _row("2026-04-04", "AAA", 0.25, 30, 0, -0.05),
        _row("2026-04-04", "BBB", 1.35, 92, 1, 0.70),
        _row("2026-04-05", "AAA", 1.45, 95, 1, 0.80),
        _row("2026-04-05", "BBB", 0.30, 25, 0, -0.15),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(
    market_date: str,
    symbol: str,
    feature_alpha: float,
    feature_beta: float,
    label_winner: int | str,
    label_net_r: float | str,
) -> dict[str, object]:
    return {
        "market_date": market_date,
        "symbol": symbol,
        "cutoff_at": f"{market_date}T08:00:00-04:00",
        "rank": 1,
        "feature_alpha": feature_alpha,
        "feature_beta": feature_beta,
        "feature_sparse": "",
        "label_future_return_pct": label_net_r,
        "label_winner": label_winner,
        "label_net_r": label_net_r,
    }
