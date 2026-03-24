from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from penny_stock_radar.config import AppSettings
from penny_stock_radar.models import PremarketSignal
from penny_stock_radar.providers.market_data import (
    MockSymbolSpec,
    MockTickGenerator,
    ReplayCSVMarketDataProvider,
)
from penny_stock_radar.services.premarket_monitor import PremarketMonitor
from penny_stock_radar.services.regular_session_engine import RegularSessionEngine
from penny_stock_radar.services.replay_evaluator import ReplayEvaluator


def test_mock_tick_generator_and_replay_provider_round_trip(tmp_path) -> None:
    generator = MockTickGenerator(seed=13)
    ticks = generator.generate_session(
        [
            MockSymbolSpec(symbol="AAA", base_price=1.2, scenario="continuation"),
            MockSymbolSpec(symbol="BBB", base_price=0.8, scenario="fade"),
        ],
        session_date=date(2026, 3, 24),
    )
    assert ticks

    provider = ReplayCSVMarketDataProvider()
    csv_path = tmp_path / "mock.csv"
    provider.save_ticks(ticks, csv_path)
    frame = provider.load_ticks(csv_path)

    assert not frame.empty
    assert set(frame["symbol"]) == {"AAA", "BBB"}
    assert {"premarket", "regular"} <= set(frame["session"])


def test_premarket_monitor_regular_engine_and_evaluator_work_together() -> None:
    settings = AppSettings()
    generator = MockTickGenerator(seed=21)
    ticks = generator.generate_session(
        [MockSymbolSpec(symbol="AAA", base_price=1.1, scenario="continuation")],
        session_date=date(2026, 3, 24),
    )
    frame = pd.DataFrame([tick.model_dump(mode="python") for tick in ticks])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])

    premarket_signals = PremarketMonitor(settings).analyze(frame)
    assert len(premarket_signals) == 1
    signal = premarket_signals[0]
    assert signal.symbol == "AAA"
    assert signal.quality_score > 0
    assert signal.premarket_rvol > 1

    decisions = RegularSessionEngine(settings).analyze(frame, premarket_signals)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.symbol == "AAA"
    assert decision.decision in {"ENTER", "WATCH", "AVOID"}

    report, labels = ReplayEvaluator().evaluate(premarket_signals, decisions)
    assert report.symbol_count == 1
    assert "AAA" in labels
    assert labels["AAA"] in {"continuation", "fade", "fakeout"}
