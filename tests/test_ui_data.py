from __future__ import annotations

from datetime import datetime, timedelta, timezone

from penny_stock_radar.db import (
    create_paper_trading_run,
    init_database,
    insert_paper_orders,
    upsert_paper_positions,
    upsert_paper_trading_run,
)
from penny_stock_radar.models import PaperOrder, PaperPosition
from penny_stock_radar.services.paper_reporting import paper_report_paths
from penny_stock_radar.ui import data as ui_data


def _clear_ui_caches() -> None:
    ui_data.load_latest_paper_run.clear()
    ui_data.load_paper_positions.clear()
    ui_data.load_paper_orders.clear()
    ui_data.load_paper_strategy_runs.clear()
    ui_data.load_paper_backtest_kpis.clear()
    ui_data.load_paper_regime_split.clear()
    ui_data.load_paper_predictor_kpis.clear()
    ui_data.load_paper_execution_quality.clear()


def test_paper_ui_data_loaders_use_primary_bucket_and_sort_latest_first(tmp_path) -> None:
    database_path = tmp_path / "radar.db"
    init_database(database_path)
    _clear_ui_caches()

    base_time = datetime(2026, 4, 18, 13, 30, tzinfo=timezone.utc)
    predictor_run = create_paper_trading_run(
        database_path,
        "auto_paper_v1",
        10_000,
        bucket="predictor_weighted",
    )
    predictor_run.updated_at = base_time
    upsert_paper_trading_run(database_path, predictor_run)

    momentum_run = create_paper_trading_run(
        database_path,
        "auto_paper_v1",
        10_000,
        bucket="momentum_only",
    )
    momentum_run.updated_at = base_time + timedelta(minutes=5)
    upsert_paper_trading_run(database_path, momentum_run)

    newer_predictor_run = create_paper_trading_run(
        database_path,
        "auto_paper_v1",
        10_000,
        bucket="predictor_weighted",
    )
    newer_predictor_run.updated_at = base_time + timedelta(minutes=10)
    upsert_paper_trading_run(database_path, newer_predictor_run)

    upsert_paper_positions(
        database_path,
        [
            PaperPosition(
                position_id="open-pos",
                run_id=newer_predictor_run.run_id,
                symbol="AAA",
                status="OPEN",
                entry_phase="regular",
                quantity=100,
                average_entry_price=1.25,
                last_price=1.40,
                cost_basis=125.0,
                market_value=140.0,
                total_pnl=15.0,
                updated_at=base_time + timedelta(minutes=12),
            ),
            PaperPosition(
                position_id="closed-pos",
                run_id=newer_predictor_run.run_id,
                symbol="BBB",
                status="CLOSED",
                entry_phase="premarket",
                quantity=50,
                average_entry_price=0.85,
                last_price=0.90,
                cost_basis=42.5,
                market_value=0.0,
                total_pnl=5.0,
                updated_at=base_time + timedelta(minutes=15),
                closed_at=base_time + timedelta(minutes=15),
            ),
        ],
    )
    insert_paper_orders(
        database_path,
        [
            PaperOrder(
                order_id="older-order",
                run_id=newer_predictor_run.run_id,
                symbol="AAA",
                market_phase="regular",
                action="BUY",
                intent="ENTRY",
                quantity=100,
                price=1.25,
                notional=125.0,
                created_at=base_time + timedelta(minutes=11),
            ),
            PaperOrder(
                order_id="newer-order",
                run_id=newer_predictor_run.run_id,
                symbol="BBB",
                market_phase="regular",
                action="SELL",
                intent="EXIT",
                quantity=50,
                price=0.90,
                notional=45.0,
                created_at=base_time + timedelta(minutes=16),
            ),
        ],
    )

    run_frame = ui_data.load_latest_paper_run(database_path)
    positions_frame = ui_data.load_paper_positions(database_path)
    orders_frame = ui_data.load_paper_orders(database_path)
    strategy_runs = ui_data.load_paper_strategy_runs(database_path)

    assert run_frame.iloc[0]["run_id"] == newer_predictor_run.run_id
    assert list(positions_frame["symbol"]) == ["AAA", "BBB"]
    assert list(orders_frame["order_id"]) == ["newer-order", "older-order"]
    assert set(strategy_runs["bucket"]) == {"predictor_weighted", "momentum_only"}


def test_paper_ui_data_csv_loaders_follow_shared_report_paths(tmp_path) -> None:
    export_dir = tmp_path / "paper"
    export_dir.mkdir(parents=True)
    _clear_ui_caches()

    paths = paper_report_paths(export_dir)
    paths.backtest_kpis.write_text("strategy_label,expectancy_r\nAdaptive,1.2\n", encoding="utf-8")
    paths.regime_split.write_text("strategy_label,regime_bucket\nAdaptive,trend\n", encoding="utf-8")
    paths.predictor_kpis.write_text("strategy_label,predictor_hit_rate_pct\nAdaptive,55\n", encoding="utf-8")
    paths.execution_quality.write_text("strategy_label,order_count\nAdaptive,4\n", encoding="utf-8")

    assert list(ui_data.load_paper_backtest_kpis(export_dir)["strategy_label"]) == ["Adaptive"]
    assert list(ui_data.load_paper_regime_split(export_dir)["regime_bucket"]) == ["trend"]
    assert list(ui_data.load_paper_predictor_kpis(export_dir)["predictor_hit_rate_pct"]) == [55]
    assert list(ui_data.load_paper_execution_quality(export_dir)["order_count"]) == [4]
