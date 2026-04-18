from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import AppSettings
from ..models import MarketActivity
from .engine_shared import collect_active_symbols
from .intraday_engine import IntradayTradingEngine
from .paper_reporting import PaperReportingService
from .paper_runtime import (
    BASELINE_PCT_STRATEGY,
    BASELINE_VOLUME_STRATEGY,
    MOMENTUM_ONLY_BUCKET,
    PAPER_BUCKET_LABELS,
    PAPER_STRATEGY_LABELS,
    PREDICTOR_WEIGHTED_BUCKET,
    PRIMARY_PAPER_STRATEGY,
    PaperTradingStepResult,
    write_csv,
)
from .paper_trading import PaperTradingEngine


class PaperTradingCoordinator:
    def __init__(
        self,
        settings: AppSettings,
        *,
        export_dir: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        advisor: object | None = None,
    ) -> None:
        self.settings = settings
        self.export_dir = export_dir or settings.paper_trade_dir
        self.strategy_name = PRIMARY_PAPER_STRATEGY
        self.primary_engine = IntradayTradingEngine(
            settings,
            strategy_name=PRIMARY_PAPER_STRATEGY,
            strategy_label=PAPER_STRATEGY_LABELS[PRIMARY_PAPER_STRATEGY],
            bucket=PREDICTOR_WEIGHTED_BUCKET,
            export_dir=self.export_dir,
            advisor=advisor,
            now_fn=now_fn,
            strategy_mode="adaptive",
        )
        momentum_only_settings = settings.model_copy(
            update={
                "paper_predictor_weight_k1": 0.0,
                "paper_predictor_weight_k2": 0.0,
            }
        )
        self.bucket_engines = [
            PaperTradingEngine(
                momentum_only_settings,
                strategy_name=PRIMARY_PAPER_STRATEGY,
                bucket=MOMENTUM_ONLY_BUCKET,
                export_dir=self.export_dir,
                advisor=advisor,
                now_fn=now_fn,
                strategy_mode="adaptive",
            )
        ]
        self.baseline_engines = [
            PaperTradingEngine(
                settings,
                strategy_name=BASELINE_PCT_STRATEGY,
                bucket=BASELINE_PCT_STRATEGY,
                export_dir=self.export_dir,
                advisor=None,
                now_fn=now_fn,
                strategy_mode="baseline_pct",
            ),
            PaperTradingEngine(
                settings,
                strategy_name=BASELINE_VOLUME_STRATEGY,
                bucket=BASELINE_VOLUME_STRATEGY,
                export_dir=self.export_dir,
                advisor=None,
                now_fn=now_fn,
                strategy_mode="baseline_volume",
            ),
        ]
        self.reporting = PaperReportingService(
            settings=settings,
            export_dir=self.export_dir,
            write_csv=write_csv,
            strategy_labels=PAPER_STRATEGY_LABELS,
            bucket_labels=PAPER_BUCKET_LABELS,
            primary_strategy_name=PRIMARY_PAPER_STRATEGY,
            predictor_weighted_bucket=PREDICTOR_WEIGHTED_BUCKET,
            momentum_only_bucket=MOMENTUM_ONLY_BUCKET,
            baseline_pct_strategy=BASELINE_PCT_STRATEGY,
        )

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        market_phase = self.primary_engine.scanner.resolve_market_phase(phase)
        rows = list(activity or [])
        if not rows and market_phase in {"premarket", "regular"}:
            extra_symbols = collect_active_symbols(
                [self.primary_engine, *self.bucket_engines, *self.baseline_engines]
            )
            scan_result = self.primary_engine.scanner.scan(
                phase=market_phase,
                top_limit=10,
                extra_symbols=tuple(extra_symbols),
            )
            rows = scan_result.activity
        return self.process_market_activity(
            market_phase=market_phase,
            activity=rows,
            export_csv=export_csv,
        )

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        primary_result = self.primary_engine.process_market_activity(
            market_phase=market_phase,
            activity=activity,
            export_csv=export_csv,
        )
        for engine in self.bucket_engines:
            engine.process_market_activity(
                market_phase=market_phase,
                activity=activity,
                export_csv=False,
            )
        for engine in self.baseline_engines:
            engine.process_market_activity(
                market_phase=market_phase,
                activity=activity,
                export_csv=False,
            )
        comparison_path = self.export_strategy_comparison()
        self.export_bucket_comparison()
        self.export_bucket_trade_diff()
        self.export_cohort_summary()
        self.export_execution_quality()
        self.export_trade_log()
        self.export_backtest_kpis()
        self.export_regime_split()
        self.export_predictor_kpis()
        primary_result.comparison_path = comparison_path
        return primary_result

    def export_strategy_comparison(self) -> Path:
        return self.reporting.export_strategy_comparison()

    def export_bucket_comparison(self) -> Path:
        return self.reporting.export_bucket_comparison()

    def export_bucket_trade_diff(self) -> Path:
        return self.reporting.export_bucket_trade_diff()

    def export_cohort_summary(self, run_id: str | None = None) -> Path:
        return self.reporting.export_cohort_summary(run_id)

    def export_execution_quality(self) -> Path:
        return self.reporting.export_execution_quality()

    def export_trade_log(self) -> Path:
        return self.reporting.export_trade_log()

    def export_backtest_kpis(self) -> Path:
        return self.reporting.export_backtest_kpis()

    def export_regime_split(self) -> Path:
        return self.reporting.export_regime_split()

    def export_predictor_kpis(self) -> Path:
        return self.reporting.export_predictor_kpis()
