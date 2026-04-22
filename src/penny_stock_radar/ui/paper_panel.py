from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..services.paper_trading import PAPER_BUCKET_LABELS, PAPER_STRATEGY_LABELS
from .formatting import (
    coerce_reason_list,
    metric_integer,
    metric_number,
    prepare_display_frame,
    translate_label,
)


def render_paper_trading_panel(
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
    if run_frame.empty:
        st.info("모의투자 기록이 없습니다. `psradar run-paper-trading` 또는 `psradar paper-trader`를 먼저 실행하세요.")
        return

    row = run_frame.iloc[0]
    open_positions = (
        positions[positions["status"] == "OPEN"].copy()
        if not positions.empty and "status" in positions.columns
        else pd.DataFrame()
    )
    closed_positions = (
        positions[positions["status"] == "CLOSED"].copy()
        if not positions.empty and "status" in positions.columns
        else pd.DataFrame()
    )

    top = st.columns(6)
    bucket_key = str(row.get("bucket") or "predictor_weighted")
    bucket_text = PAPER_BUCKET_LABELS.get(bucket_key, bucket_key)
    top[0].metric("상태", f"{translate_label(row.get('status'))} · {bucket_text}")
    top[1].metric("현금", metric_number(row.get("cash_balance"), 2))
    top[2].metric("에쿼티", metric_number(row.get("equity"), 2))
    top[3].metric("실현손익", metric_number(row.get("realized_pnl"), 2))
    top[4].metric("미실현손익", metric_number(row.get("unrealized_pnl"), 2))
    top[5].metric("누적 수익률", metric_number(row.get("total_return_pct"), 2) + "%")

    bottom = st.columns(6)
    bottom[0].metric("보유 포지션", len(open_positions))
    bottom[1].metric("종료 트레이드", metric_integer(row.get("closed_trade_count")))
    bottom[2].metric("승률", metric_number(row.get("win_rate"), 2) + "%")
    bottom[3].metric("Profit Factor", metric_number(row.get("profit_factor"), 2))
    bottom[4].metric("손익비", metric_number(row.get("reward_risk_ratio"), 2))
    bottom[5].metric("최대 낙폭", metric_number(row.get("max_drawdown_pct"), 2) + "%")

    notes = [_paper_note_text(item) for item in coerce_reason_list(row.get("notes"))]
    if notes:
        st.info("\n\n".join(notes[:4]))

    if not positions.empty and "strategy_bucket" in positions.columns:
        bucket_frame = positions.copy()
        bucket_frame["strategy_bucket"] = bucket_frame["strategy_bucket"].fillna("").replace("", "unknown")
        if "day_regime" not in bucket_frame.columns:
            bucket_frame["day_regime"] = "unknown"
        else:
            bucket_frame["day_regime"] = bucket_frame["day_regime"].fillna("unknown")
        bucket_summary = (
            bucket_frame.groupby(["strategy_bucket", "day_regime"], dropna=False)
            .agg(
                trade_count=("symbol", "count"),
                total_pnl=("total_pnl", "sum"),
                average_pnl=("total_pnl", "mean"),
            )
            .reset_index()
            .sort_values(["total_pnl", "trade_count"], ascending=[False, False])
        )
        if not bucket_summary.empty:
            st.markdown("**Bucket Attribution**")
            st.dataframe(prepare_display_frame(bucket_summary), use_container_width=True, hide_index=True)

    if not strategy_runs.empty:
        st.markdown("**전략 비교**")
        comparison = strategy_runs.copy()
        if "strategy_name" not in comparison.columns:
            comparison["strategy_name"] = "unknown"
        comparison["strategy"] = comparison["strategy_name"].map(PAPER_STRATEGY_LABELS).fillna(
            comparison["strategy_name"]
        )
        if "bucket" in comparison.columns:
            comparison["portfolio_bucket"] = (
                comparison["bucket"].map(PAPER_BUCKET_LABELS).fillna(comparison["bucket"]).fillna("predictor_weighted")
            )
        display = prepare_display_frame(comparison)
        preferred = [
            "strategy",
            "portfolio_bucket",
            "equity",
            "total_return_pct",
            "closed_trade_count",
            "win_rate",
            "profit_factor",
            "reward_risk_ratio",
            "max_drawdown_pct",
            "updated_at",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)

    if not backtest_kpis.empty:
        st.markdown("**백테스팅 KPI**")
        display = prepare_display_frame(backtest_kpis)
        preferred = [
            "strategy_label",
            "bucket_label",
            "expectancy_r",
            "sharpe_ratio",
            "sharpe_ci_low",
            "sharpe_ci_high",
            "max_drawdown_pct",
            "mdd_ci_low",
            "mdd_ci_high",
            "avg_hold_minutes",
            "one_winner_dependency_pct",
            "avg_stop_slippage_pct",
            "time_under_water_minutes",
            "theme_concentration_pct",
            "cost_adjusted_return_pct",
            "excess_sharpe_vs_baseline",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)

    metrics_left, metrics_right = st.columns(2)
    with metrics_left:
        st.markdown("**Regime Split**")
        if regime_split.empty:
            st.info("아직 regime split 리포트가 없습니다.")
        else:
            display = prepare_display_frame(regime_split)
            preferred = [
                "strategy_label",
                "bucket_label",
                "regime_bucket",
                "trade_count",
                "win_rate",
                "average_pnl",
                "expectancy_r",
            ]
            available = [column for column in preferred if column in display.columns]
            st.dataframe(display[available], use_container_width=True, hide_index=True)

    with metrics_right:
        st.markdown("**Predictor KPI**")
        if predictor_kpis.empty:
            st.info("아직 predictor KPI 리포트가 없습니다.")
        else:
            display = prepare_display_frame(predictor_kpis)
            preferred = [
                "strategy_label",
                "bucket_label",
                "candidate_survival_rate_pct",
                "predictor_hit_rate_pct",
                "edge_decay_day1_avg_r",
                "edge_decay_day2_avg_r",
                "edge_decay_day3_avg_r",
            ]
            available = [column for column in preferred if column in display.columns]
            st.dataframe(display[available], use_container_width=True, hide_index=True)

    if not execution_quality.empty:
        st.markdown("**Execution Quality**")
        display = prepare_display_frame(execution_quality)
        preferred = [
            "strategy_label",
            "bucket_label",
            "market_phase",
            "order_count",
            "partial_fill_count",
            "avg_buy_slippage_pct",
            "avg_sell_slippage_pct",
            "avg_slippage_pct",
            "total_transaction_cost",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)

    chart_left, chart_right = st.columns((1.2, 1.0))
    with chart_left:
        st.markdown("**에쿼티 곡선**")
        if snapshots.empty:
            st.info("아직 저장된 에쿼티 곡선이 없습니다.")
        elif "created_at" not in snapshots.columns:
            st.info("에쿼티 곡선 시각 컬럼이 없어 차트를 표시할 수 없습니다.")
        else:
            chart = snapshots.copy()
            chart["created_at"] = pd.to_datetime(chart["created_at"], errors="coerce")
            chart = chart.dropna(subset=["created_at"]).set_index("created_at")
            cols = [column for column in ["equity", "realized_pnl", "unrealized_pnl"] if column in chart.columns]
            if cols:
                st.line_chart(chart[cols])
            else:
                st.info("표시할 곡선 데이터가 없습니다.")

    with chart_right:
        st.markdown("**종목별 손익**")
        if closed_positions.empty:
            st.info("아직 종료된 트레이드가 없습니다.")
        elif not {"symbol", "total_pnl"}.issubset(closed_positions.columns):
            st.info("종목별 손익에 필요한 컬럼이 아직 없습니다.")
        else:
            pnl_frame = (
                closed_positions.groupby("symbol", as_index=True)["total_pnl"]
                .sum()
                .sort_values(ascending=False)
                .to_frame()
            )
            st.bar_chart(pnl_frame)

    st.divider()

    show_open_only = st.checkbox("보유 포지션만 보기", value=False, key="paper_open_only")
    positions_frame = open_positions if show_open_only else positions
    if not positions_frame.empty:
        positions_display = positions_frame.copy()
        positions_display["entry_basis"] = positions_display.get("entry_label", "-")
        display = prepare_display_frame(positions_display)
        preferred = [
            "symbol",
            "status",
            "entry_phase",
            "entry_basis",
            "quantity",
            "average_entry_price",
            "last_price",
            "market_value",
            "total_pnl",
            "stop_price",
            "entry_reasons",
            "exit_reasons",
        ]
        available = [column for column in preferred if column in display.columns]
        st.markdown("**포지션 현황**")
        st.dataframe(display[available], use_container_width=True, hide_index=True)
    else:
        st.info("표시할 포지션이 없습니다.")

    order_limit = int(
        st.slider("주문 로그 개수", min_value=5, max_value=100, value=20, step=5, key="paper_order_limit")
    )
    if not orders.empty:
        orders_display = orders.head(order_limit).copy()
        display = prepare_display_frame(orders_display)
        preferred = [
            "created_at",
            "symbol",
            "action",
            "intent",
            "market_phase",
            "quantity",
            "price",
            "notional",
            "analysis_label",
            "realized_pnl",
            "realized_pnl_pct",
            "reasons",
        ]
        available = [column for column in preferred if column in display.columns]
        st.markdown("**최근 주문 로그**")
        st.dataframe(display[available], use_container_width=True, hide_index=True)
    else:
        st.info("저장된 주문 로그가 없습니다.")

    if not snapshots.empty:
        snapshot_limit = min(len(snapshots), 30)
        display = prepare_display_frame(snapshots.tail(snapshot_limit))
        preferred = [
            "created_at",
            "market_phase",
            "cash_balance",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "total_return_pct",
            "max_drawdown_pct",
            "win_rate",
            "profit_factor",
            "reward_risk_ratio",
            "notes",
        ]
        available = [column for column in preferred if column in display.columns]
        st.markdown("**에쿼티 스냅샷 로그**")
        st.dataframe(display[available], use_container_width=True, hide_index=True)


def _paper_note_text(value: str) -> str:
    if not value:
        return "-"
    if value.startswith("focus:"):
        parts = value.split(":", 3)
        if len(parts) == 4:
            _, symbol, label, score = parts
            return f"`{symbol}`이 현재 집중 후보입니다. 판독은 `{translate_label(label)}` 이고 점수는 `{score}` 입니다."
    if value.startswith("entry:"):
        return f"`{value.split(':', 1)[1]}` 신규 진입이 발생했습니다."
    if value.startswith("add:"):
        return f"`{value.split(':', 1)[1]}` 불타기가 발생했습니다."
    if value.startswith("exit:"):
        return f"`{value.split(':', 1)[1]}` 포지션이 청산되었습니다."
    if value.startswith("session_exit:"):
        return f"`{value.split(':', 1)[1]}` 포지션이 장 종료로 정리되었습니다."
    if value == "hold":
        return "이번 루프에서는 신규 체결 없이 기존 포지션만 감시했습니다."
    return value.replace("_", " ")
