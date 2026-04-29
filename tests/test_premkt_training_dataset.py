from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from penny_stock_radar.cli import app
from penny_stock_radar.db import init_database, insert_historical_minute_bars
from penny_stock_radar.models import HistoricalMinuteBar
from penny_stock_radar.services.premkt_training_dataset import (
    PREMKT_TRAINING_COLUMNS,
    PremktTrainingDatasetBuilder,
)

EASTERN = ZoneInfo("America/New_York")


def test_premkt_training_dataset_uses_only_pre_cutoff_bars_for_features(
    tmp_path: Path,
) -> None:
    db_path = _build_fixture_db(tmp_path)
    output_path = tmp_path / "premkt_training.csv"
    builder = PremktTrainingDatasetBuilder(db_path)

    summary = builder.build_csv(
        start_date="2026-04-10",
        end_date="2026-04-10",
        output_path=output_path,
    )
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    aaa = next(row for row in rows if row["symbol"] == "AAA")

    assert summary.row_count == 2
    assert summary.unlabeled_row_count == 1
    assert list(rows[0].keys()) == PREMKT_TRAINING_COLUMNS
    assert float(aaa["feature_premarket_volume"]) == 300.0
    assert float(aaa["feature_premarket_dollar_volume"]) == 325.0
    assert float(aaa["feature_premarket_return_pct"]) == 10.0
    assert float(aaa["feature_premarket_high_pct"]) == 20.0
    assert float(aaa["feature_premarket_low_pct"]) == -5.0
    assert float(aaa["feature_premarket_last_price"]) == 1.1
    assert float(aaa["feature_premarket_vwap"]) == 1.083333
    assert float(aaa["feature_premarket_vwap_extension_pct"]) == 1.538462
    assert int(aaa["feature_premarket_bar_count"]) == 2


def test_premkt_training_dataset_uses_post_cutoff_bars_for_labels(
    tmp_path: Path,
) -> None:
    db_path = _build_fixture_db(tmp_path)
    rows = PremktTrainingDatasetBuilder(db_path).build_rows(
        start_date="2026-04-10",
        end_date="2026-04-10",
    )
    aaa = next(row for row in rows if row["symbol"] == "AAA")
    bbb = next(row for row in rows if row["symbol"] == "BBB")

    assert aaa["cutoff_at"] == "2026-04-10T08:00:00-04:00"
    assert aaa["label_future_return_pct"] == 81.818182
    assert aaa["label_max_gain_pct"] == 100.0
    assert aaa["label_max_loss_pct"] == -0.909091
    assert aaa["label_winner"] == 1
    assert aaa["label_net_r"] == 16.363636
    assert bbb["label_future_return_pct"] is None
    assert bbb["label_winner"] is None


def test_build_premkt_training_dataset_command_writes_expected_csv(
    tmp_path: Path,
) -> None:
    db_path = _build_fixture_db(tmp_path)
    output_path = tmp_path / "ml" / "premkt_training.csv"

    result = CliRunner().invoke(
        app,
        [
            "build-premkt-training-dataset",
            "--start-date",
            "2026-04-10",
            "--end-date",
            "2026-04-10",
            "--db-path",
            str(db_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert "wrote 2 rows" in result.stdout
    assert output_path.exists()
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert list(rows[0].keys()) == PREMKT_TRAINING_COLUMNS


def _build_fixture_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    insert_historical_minute_bars(
        db_path,
        [
            _bar("AAA", "2026-04-10T07:58:00-04:00", 1.00, 1.10, 0.95, 1.05, 100),
            _bar("AAA", "2026-04-10T07:59:00-04:00", 1.05, 1.20, 1.00, 1.10, 200),
            _bar("AAA", "2026-04-10T08:00:00-04:00", 1.10, 2.00, 1.09, 1.90, 9999),
            _bar("AAA", "2026-04-10T08:01:00-04:00", 1.90, 2.20, 1.50, 2.00, 100),
            _bar("BBB", "2026-04-10T07:59:00-04:00", 2.00, 2.10, 1.90, 2.05, 50),
            _bar("CCC", "2026-04-10T08:01:00-04:00", 3.00, 3.20, 2.90, 3.10, 75),
        ],
    )
    return db_path


def _bar(
    symbol: str,
    timestamp: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
) -> HistoricalMinuteBar:
    bar_at = datetime.fromisoformat(timestamp).astimezone(EASTERN)
    return HistoricalMinuteBar(
        symbol=symbol,
        market_date=bar_at.date().isoformat(),
        market_phase="premarket",
        timestamp=bar_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        source="fixture",
    )
