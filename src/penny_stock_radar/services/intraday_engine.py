from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import AppSettings
from ..models import MarketActivity
from .engine_shared import TradingEngineSpec
from .paper_trading import PRIMARY_PAPER_STRATEGY, PaperTradingEngine, PaperTradingStepResult


INTRADAY_ENGINE_SPEC = TradingEngineSpec(
    engine_id="intraday",
    strategy_name=PRIMARY_PAPER_STRATEGY,
    strategy_mode="adaptive",
    holding_period="same_day",
    description="Thin wrapper around the current intraday paper trading engine.",
)


class IntradayTradingEngine:
    def __init__(
        self,
        settings: AppSettings,
        *,
        strategy_name: str = PRIMARY_PAPER_STRATEGY,
        strategy_label: str | None = None,
        bucket: str | None = None,
        export_dir: Path | None = None,
        advisor: object | None = None,
        now_fn: Callable[[], datetime] | None = None,
        strategy_mode: str = "adaptive",
    ) -> None:
        self.spec = TradingEngineSpec(
            engine_id=INTRADAY_ENGINE_SPEC.engine_id,
            strategy_name=strategy_name,
            strategy_mode=strategy_mode,
            holding_period=INTRADAY_ENGINE_SPEC.holding_period,
            description=INTRADAY_ENGINE_SPEC.description,
            allows_overnight=INTRADAY_ENGINE_SPEC.allows_overnight,
        )
        self.strategy_label = strategy_label or strategy_name
        self.delegate = PaperTradingEngine(
            settings,
            strategy_name=strategy_name,
            bucket=bucket,
            strategy_mode=strategy_mode,
            export_dir=export_dir,
            advisor=advisor,
            now_fn=now_fn,
        )
        self.settings = settings
        self.strategy_name = self.delegate.strategy_name
        self.strategy_mode = self.delegate.strategy_mode
        self.bucket = self.delegate.bucket
        self.export_dir = self.delegate.export_dir
        self.scanner = self.delegate.scanner

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        return self.delegate.run_once(
            phase=phase,
            activity=activity,
            export_csv=export_csv,
        )

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        return self.delegate.process_market_activity(
            market_phase=market_phase,
            activity=activity,
            export_csv=export_csv,
        )

    def export_run_csv(self, run_id: str | None = None) -> Path:
        return self.delegate.export_run_csv(run_id=run_id)

    def active_position_symbols(self) -> tuple[str, ...]:
        return self.delegate.active_position_symbols()

    def predictor_weight_for_score(self, score: float | None) -> float:
        return self.delegate.predictor_weight_for_score(score)

    def __getattr__(self, name: str) -> object:
        return getattr(self.delegate, name)
