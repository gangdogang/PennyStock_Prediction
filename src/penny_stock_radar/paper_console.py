from __future__ import annotations

from rich.table import Table

from .models import PaperOrder, PaperPosition, PaperTradingRun
from .services.paper_trading import PAPER_BUCKET_LABELS, PAPER_STRATEGY_LABELS


def build_paper_summary_tables(
    *,
    run: PaperTradingRun,
    positions: list[PaperPosition],
    orders: list[PaperOrder],
    strategy_runs: list[PaperTradingRun],
    backtest_kpis: list[dict[str, str]],
    regime_split_rows: list[dict[str, str]],
    predictor_kpi_rows: list[dict[str, str]],
    execution_quality_rows: list[dict[str, str]],
    order_limit: int,
) -> list[Table]:
    return [
        _summary_table(run),
        _strategy_comparison_table(strategy_runs),
        _positions_table(positions),
        _orders_table(orders, order_limit=order_limit),
        _backtest_kpi_table(backtest_kpis),
        _regime_split_table(regime_split_rows),
        _predictor_kpi_table(predictor_kpi_rows),
        _execution_quality_table(execution_quality_rows),
    ]


def _summary_table(run: PaperTradingRun) -> Table:
    table = Table(title="Paper Trading Summary")
    table.add_column("Run")
    table.add_column("Status")
    table.add_column("Cash")
    table.add_column("Equity")
    table.add_column("Realized")
    table.add_column("Unrealized")
    table.add_column("Return%")
    table.add_column("Win Rate")
    table.add_column("Profit Factor")
    table.add_column("R/R")
    table.add_row(
        run.run_id[:8],
        (
            f"{PAPER_STRATEGY_LABELS.get(run.strategy_name, run.strategy_name)}"
            f" · {PAPER_BUCKET_LABELS.get(run.bucket, run.bucket)} · {run.status}"
        ),
        f"{run.cash_balance:.2f}",
        f"{run.equity:.2f}",
        f"{run.realized_pnl:.2f}",
        f"{run.unrealized_pnl:.2f}",
        f"{run.total_return_pct:.2f}",
        f"{run.win_rate:.2f}",
        f"{run.profit_factor:.2f}",
        f"{run.reward_risk_ratio:.2f}",
    )
    return table


def _strategy_comparison_table(strategy_runs: list[PaperTradingRun]) -> Table:
    table = Table(title="Paper Strategy Comparison")
    table.add_column("Strategy")
    table.add_column("Bucket")
    table.add_column("Equity")
    table.add_column("Return%")
    table.add_column("Closed")
    table.add_column("Win Rate")
    table.add_column("Profit Factor")
    table.add_column("Max DD%")
    for strategy_run in strategy_runs:
        table.add_row(
            PAPER_STRATEGY_LABELS.get(strategy_run.strategy_name, strategy_run.strategy_name),
            PAPER_BUCKET_LABELS.get(strategy_run.bucket, strategy_run.bucket),
            f"{strategy_run.equity:.2f}",
            f"{strategy_run.total_return_pct:.2f}",
            str(strategy_run.closed_trade_count),
            f"{strategy_run.win_rate:.2f}",
            f"{strategy_run.profit_factor:.2f}",
            f"{strategy_run.max_drawdown_pct:.2f}",
        )
    return table


def _positions_table(positions: list[PaperPosition]) -> Table:
    table = Table(title="Paper Positions")
    table.add_column("Symbol")
    table.add_column("Status")
    table.add_column("Qty")
    table.add_column("Avg")
    table.add_column("Last")
    table.add_column("PnL")
    table.add_column("Stop")
    for row in positions:
        table.add_row(
            row.symbol,
            row.status,
            str(row.quantity),
            f"{row.average_entry_price:.4f}",
            _format_optional_number(row.last_price, 4),
            f"{row.total_pnl:.2f}",
            _format_optional_number(row.stop_price, 4),
        )
    return table


def _orders_table(orders: list[PaperOrder], *, order_limit: int) -> Table:
    table = Table(title="Recent Paper Orders")
    table.add_column("Time")
    table.add_column("Symbol")
    table.add_column("Action")
    table.add_column("Intent")
    table.add_column("Qty")
    table.add_column("Price")
    table.add_column("PnL")
    for row in orders[-order_limit:]:
        table.add_row(
            str(row.created_at),
            row.symbol,
            row.action,
            row.intent,
            str(row.quantity),
            f"{row.price:.4f}",
            _format_optional_number(row.realized_pnl, 2),
        )
    return table


def _backtest_kpi_table(rows: list[dict[str, str]]) -> Table:
    table = Table(title="Backtesting KPIs")
    table.add_column("Strategy")
    table.add_column("Bucket")
    table.add_column("Expectancy R")
    table.add_column("Sharpe")
    table.add_column("Sharpe CI")
    table.add_column("MDD CI")
    table.add_column("Avg Hold Min")
    table.add_column("One Winner%")
    table.add_column("Stop Slip%")
    for row in rows:
        table.add_row(
            str(row.get("strategy_label", row.get("strategy_name", ""))),
            str(row.get("bucket_label", row.get("bucket", ""))),
            str(row.get("expectancy_r", "")),
            str(row.get("sharpe_ratio", "")),
            f"{row.get('sharpe_ci_low', '')} / {row.get('sharpe_ci_high', '')}",
            f"{row.get('mdd_ci_low', '')} / {row.get('mdd_ci_high', '')}",
            str(row.get("avg_hold_minutes", "")),
            str(row.get("one_winner_dependency_pct", "")),
            str(row.get("avg_stop_slippage_pct", "")),
        )
    return table


def _regime_split_table(rows: list[dict[str, str]]) -> Table:
    table = Table(title="Regime Split")
    table.add_column("Strategy")
    table.add_column("Bucket")
    table.add_column("Regime")
    table.add_column("Trades")
    table.add_column("Win Rate")
    table.add_column("Avg PnL")
    for row in rows:
        table.add_row(
            str(row.get("strategy_label", row.get("strategy_name", ""))),
            str(row.get("bucket_label", row.get("bucket", ""))),
            str(row.get("regime_bucket", "")),
            str(row.get("trade_count", "")),
            str(row.get("win_rate", "")),
            str(row.get("average_pnl", "")),
        )
    return table


def _predictor_kpi_table(rows: list[dict[str, str]]) -> Table:
    table = Table(title="Predictor KPIs")
    table.add_column("Strategy")
    table.add_column("Bucket")
    table.add_column("Survival%")
    table.add_column("Hit Rate%")
    table.add_column("Day1 R")
    table.add_column("Day2 R")
    table.add_column("Day3 R")
    for row in rows:
        table.add_row(
            str(row.get("strategy_label", row.get("strategy_name", ""))),
            str(row.get("bucket_label", row.get("bucket", ""))),
            str(row.get("candidate_survival_rate_pct", "")),
            str(row.get("predictor_hit_rate_pct", "")),
            str(row.get("edge_decay_day1_avg_r", "")),
            str(row.get("edge_decay_day2_avg_r", "")),
            str(row.get("edge_decay_day3_avg_r", "")),
        )
    return table


def _execution_quality_table(rows: list[dict[str, str]]) -> Table:
    table = Table(title="Execution Quality")
    table.add_column("Strategy")
    table.add_column("Bucket")
    table.add_column("Phase")
    table.add_column("Orders")
    table.add_column("Partial")
    table.add_column("Buy Slip%")
    table.add_column("Sell Slip%")
    table.add_column("Txn Cost")
    for row in rows:
        table.add_row(
            str(row.get("strategy_label", row.get("strategy_name", ""))),
            str(row.get("bucket_label", row.get("bucket", ""))),
            str(row.get("market_phase", "")),
            str(row.get("order_count", "")),
            str(row.get("partial_fill_count", "")),
            str(row.get("avg_buy_slippage_pct", "")),
            str(row.get("avg_sell_slippage_pct", "")),
            str(row.get("total_transaction_cost", "")),
        )
    return table


def _format_optional_number(value: float | None, precision: int) -> str:
    if value is None:
        return "-"
    return f"{value:.{precision}f}"
