from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import AppSettings
from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_replay_report,
    fetch_latest_scan_id,
    fetch_latest_watchlist,
    insert_premarket_signals,
    insert_replay_report,
    insert_session_decisions,
)
from ..providers.market_data import (
    MockSymbolSpec,
    MockTickGenerator,
    ReplayCSVMarketDataProvider,
)
from .premarket_monitor import PremarketMonitor
from .regular_session_engine import RegularSessionEngine
from .replay_evaluator import ReplayEvaluator


class ReplayPipeline:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.provider = ReplayCSVMarketDataProvider()
        self.generator = MockTickGenerator()
        self.premarket_monitor = PremarketMonitor(settings)
        self.regular_engine = RegularSessionEngine(settings)
        self.evaluator = ReplayEvaluator()

    def generate_mock_replay(
        self,
        output_path: Path | None = None,
        symbol_limit: int | None = None,
    ) -> Path:
        watchlist_rows = fetch_latest_watchlist(
            self.settings.database_path,
            limit=symbol_limit or self.settings.watchlist_limit,
        )
        universe_rows = {
            row["symbol"]: row for row in fetch_latest_passed_universe(self.settings.database_path)
        }
        if not watchlist_rows:
            raise ValueError("No watchlist available. Build a watchlist before generating replay data.")

        specs: list[MockSymbolSpec] = []
        for index, row in enumerate(watchlist_rows):
            symbol = row["symbol"]
            universe_row = universe_rows.get(symbol)
            base_price = float(universe_row["price"]) if universe_row and universe_row["price"] else 1.5
            if index == 0:
                scenario = "continuation"
            elif index == 1:
                scenario = "fade"
            else:
                scenario = "fakeout"
            specs.append(
                MockSymbolSpec(
                    symbol=symbol,
                    base_price=base_price,
                    scenario=scenario,
                    baseline_avg_volume=40_000.0 + (index * 10_000.0),
                )
            )

        ticks = self.generator.generate_session(specs)
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.settings.replay_dir / f"mock_session_{timestamp}.csv"
        self.provider.save_ticks(ticks, output_path)
        return output_path

    def analyze_replay(
        self,
        replay_path: Path,
        export_json: Path | None = None,
    ) -> dict[str, object]:
        scan_row = fetch_latest_scan_id(self.settings.database_path)
        if scan_row is None:
            raise ValueError("No scan run exists. Build the universe and watchlist first.")

        ticks = self.provider.load_ticks(replay_path)
        premarket_signals = self.premarket_monitor.analyze(ticks)
        session_decisions = self.regular_engine.analyze(ticks, premarket_signals)
        report, labels = self.evaluator.evaluate(premarket_signals, session_decisions)

        insert_premarket_signals(
            self.settings.database_path, scan_row["scan_id"], premarket_signals
        )
        insert_session_decisions(
            self.settings.database_path, scan_row["scan_id"], session_decisions
        )
        insert_replay_report(self.settings.database_path, scan_row["scan_id"], report)

        payload = {
            "replay_path": str(replay_path),
            "premarket_signals": [signal.model_dump(mode="json") for signal in premarket_signals],
            "session_decisions": [decision.model_dump(mode="json") for decision in session_decisions],
            "labels": labels,
            "report": report.model_dump(mode="json"),
        }
        if export_json is not None:
            export_json.parent.mkdir(parents=True, exist_ok=True)
            export_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def latest_report(self) -> dict[str, object] | None:
        row = fetch_latest_replay_report(self.settings.database_path)
        if row is None:
            return None
        return dict(row)
