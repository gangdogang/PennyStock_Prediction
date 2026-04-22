from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..formatting import (
    coerce_reason_list,
    metric_integer,
    metric_number,
    translate_label,
    translate_reason,
)
from .overview_model import (
    build_leaderboard_display_frame,
    build_overview_metrics,
    prepare_leaderboard,
)
from .shared import first_row


def render_command_center(
    universe: pd.DataFrame,
    watchlist: pd.DataFrame,
    premarket: pd.DataFrame,
    live_premarket: pd.DataFrame,
    session: pd.DataFrame,
    live_regular: pd.DataFrame,
    social: pd.DataFrame,
    replay: pd.DataFrame,
    paper_run: pd.DataFrame,
    paper_positions: pd.DataFrame,
) -> None:
    leaderboard = prepare_leaderboard(
        watchlist,
        premarket,
        session,
        social,
        live_premarket=live_premarket,
        live_regular=live_regular,
    )
    if leaderboard.empty:
        st.info("No dashboard-ready data yet. Run the pipeline first.")
        return

    metrics = build_overview_metrics(leaderboard, session, replay, paper_run, paper_positions)
    cols = st.columns(4)
    for column, (label, value) in zip(cols, metrics.headline, strict=False):
        column.metric(label, value)

    if metrics.paper:
        paper_cols = st.columns(5)
        for column, (label, value) in zip(paper_cols, metrics.paper, strict=False):
            column.metric(label, value)

    left, right = st.columns((1.35, 1.0))

    with left:
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown("<h3 class='section-title'>오늘의 리더보드</h3>", unsafe_allow_html=True)
        st.markdown(
            '<div class="small-note">관심종목, 프리장, 정규장, 소셜 정보를 합친 종합 우선순위입니다.</div>',
            unsafe_allow_html=True,
        )

        chart_frame = leaderboard.head(8)[["symbol", "radar_score"]].set_index("symbol")
        st.bar_chart(chart_frame)

        display = build_leaderboard_display_frame(leaderboard)
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        symbols = leaderboard["symbol"].tolist()
        default_index = 0 if symbols else None
        selected = st.selectbox("집중 분석 종목", options=symbols, index=default_index)
        render_symbol_focus(selected, universe, watchlist, premarket, session, social, leaderboard)


def render_symbol_focus(
    symbol: str,
    universe: pd.DataFrame,
    watchlist: pd.DataFrame,
    premarket: pd.DataFrame,
    session: pd.DataFrame,
    social: pd.DataFrame,
    leaderboard: pd.DataFrame,
) -> None:
    leader_row = first_row(leaderboard, symbol)
    universe_row = first_row(universe, symbol)
    watchlist_row = first_row(watchlist, symbol)
    premarket_row = first_row(premarket, symbol)
    session_row = first_row(session, symbol)
    social_row = first_row(social, symbol)

    st.markdown('<div class="focus-card">', unsafe_allow_html=True)
    decision = session_row.get("decision") if session_row else None
    st.markdown(
        f"<h3 class='section-title'>{symbol} {decision_pill(decision)}</h3>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='small-note'>이 종목이 아직 진입 가능한지 가장 빠르게 판단하는 요약입니다.</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns(3)
    cols[0].metric(
        "현재가",
        metric_number(universe_row.get("price") if universe_row else None, 2),
    )
    cols[1].metric(
        "셋업 점수",
        metric_number(watchlist_row.get("total_score") if watchlist_row else None, 2),
    )
    cols[2].metric(
        "프리장 품질",
        metric_number(premarket_row.get("quality_score") if premarket_row else None, 2),
    )

    cols = st.columns(3)
    cols[0].metric(
        "유통주식수",
        metric_integer(universe_row.get("float_shares") if universe_row else None),
    )
    cols[1].metric(
        "최대 유리폭(MFE%)",
        metric_number(session_row.get("mfe_pct") if session_row else None, 2),
    )
    cols[2].metric(
        "소셜 점수",
        metric_number(social_row.get("social_score") if social_row else None, 2),
    )

    info_parts = []
    if universe_row:
        sector = universe_row.get("sector") or "-"
        industry = universe_row.get("industry") or "-"
        info_parts.append(f"섹터: `{sector}`")
        info_parts.append(f"업종: `{industry}`")
    if leader_row and leader_row.get("radar_score") is not None:
        info_parts.append(f"레이더 점수: `{float(leader_row['radar_score']):.2f}`")
    if premarket_row and premarket_row.get("dollar_volume") is not None:
        info_parts.append(f"프리장 거래대금: `{float(premarket_row['dollar_volume']):,.0f}`")
    if info_parts:
        st.markdown("  \n".join(info_parts))

    render_interpretation_summary(symbol, session_row, premarket_row, watchlist_row, social_row)
    render_reason_block("후보 선정 이유", watchlist_row.get("reasons") if watchlist_row else None)
    render_reason_block(
        "프리장 판단 이유", premarket_row.get("reasons") if premarket_row else None
    )
    render_reason_block(
        "정규장 판단 이유", session_row.get("reasons") if session_row else None
    )
    render_reason_block("소셜 판단 이유", social_row.get("reasons") if social_row else None)
    st.markdown("</div>", unsafe_allow_html=True)


def decision_pill(decision: str | None) -> str:
    if decision in {"ENTER", "CONDITIONAL_ENTRY", "OPENING_RANGE_CANDIDATE"}:
        klass = "pill-enter"
    elif decision in {"WATCH", "WAIT_PULLBACK", "NEWS_CHECK_FIRST"}:
        klass = "pill-watch"
    elif decision in {"AVOID", "NO_CHASE"}:
        klass = "pill-avoid"
    else:
        klass = "pill-neutral"
        decision = decision or "NO SIGNAL"
    return f'<span class="pill {klass}">{translate_label(decision)}</span>'


def render_reason_block(title: str, value: Any) -> None:
    reasons = coerce_reason_list(value)
    st.markdown(f"**{title}**")
    if not reasons:
        st.markdown("<div class='small-note'>기록된 신호가 없습니다.</div>", unsafe_allow_html=True)
        return
    for reason in reasons:
        st.markdown(f"- {translate_reason(reason)}")


def render_interpretation_summary(
    symbol: str,
    session_row: dict[str, Any],
    premarket_row: dict[str, Any],
    watchlist_row: dict[str, Any],
    social_row: dict[str, Any],
) -> None:
    decision = translate_label(session_row.get("decision")) if session_row else "-"
    lines = [f"`{symbol}`의 현재 정규장 해석은 **{decision}** 입니다."]

    if watchlist_row and watchlist_row.get("total_score") is not None:
        lines.append(f"프리장 전 셋업 점수는 `{float(watchlist_row['total_score']):.2f}` 입니다.")
    if premarket_row and premarket_row.get("quality_score") is not None:
        lines.append(f"프리장 품질 점수는 `{float(premarket_row['quality_score']):.2f}` 입니다.")
    if premarket_row and premarket_row.get("spread_pct") is not None:
        lines.append(f"저장된 프리장 스프레드는 `{float(premarket_row['spread_pct']):.2f}%` 입니다.")

    highlight_reasons: list[str] = []
    for row in (watchlist_row, premarket_row, session_row, social_row):
        if row and row.get("reasons"):
            highlight_reasons.extend(coerce_reason_list(row["reasons"]))
    if highlight_reasons:
        translated = [translate_reason(reason) for reason in highlight_reasons[:4]]
        lines.append("핵심 근거: " + ", ".join(translated))

    st.info("\n\n".join(lines))
