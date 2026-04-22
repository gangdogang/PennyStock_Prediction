from __future__ import annotations

import pandas as pd

from ..paper_panel import render_paper_trading_panel


def render_paper_trading(
    run_frame: pd.DataFrame,
    strategy_runs: pd.DataFrame,
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    snapshots: pd.DataFrame,
    backtest_kpis: pd.DataFrame,
    regime_split: pd.DataFrame,
    predictor_kpis: pd.DataFrame,
    execution_quality: pd.DataFrame,
) -> None:
    render_paper_trading_panel(
        run_frame=run_frame,
        strategy_runs=strategy_runs,
        positions=positions,
        orders=orders,
        snapshots=snapshots,
        backtest_kpis=backtest_kpis,
        regime_split=regime_split,
        predictor_kpis=predictor_kpis,
        execution_quality=execution_quality,
    )
