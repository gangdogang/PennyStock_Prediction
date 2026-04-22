from __future__ import annotations

import pandas as pd
import streamlit as st

from ..formatting import prepare_display_frame
from .shared import numeric_series


def render_universe(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("후보군 스냅샷이 없습니다. 먼저 `psradar build-universe`를 실행하세요.")
        return

    passed_only = st.checkbox("통과 종목만 보기", value=True, key="universe_passed_only")
    symbol_filter = st.text_input("심볼 검색", value="", key="universe_symbol_filter")

    filtered = frame.copy()
    if passed_only and "passed_filters" in filtered.columns:
        filtered = filtered[filtered["passed_filters"] == 1]
    if symbol_filter:
        filtered = filtered[filtered["symbol"].str.contains(symbol_filter.upper(), na=False)]

    m1, m2, m3 = st.columns(3)
    m1.metric("행 수", len(filtered))
    m2.metric(
        "통과 종목 수",
        int(filtered["passed_filters"].sum()) if "passed_filters" in filtered else len(filtered),
    )
    m3.metric(
        "중간 가격",
        f"{filtered['price'].median():.2f}"
        if "price" in filtered and not filtered.empty
        else "-",
    )

    display = prepare_display_frame(filtered)
    cols = [
        "symbol",
        "company_name",
        "exchange",
        "price",
        "market_cap",
        "float_shares",
        "sector",
        "passed_filters",
        "filter_reasons",
    ]
    available = [column for column in cols if column in display.columns]
    st.dataframe(display[available], use_container_width=True, hide_index=True)


def render_watchlist(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("관심종목이 없습니다. 먼저 `psradar build-watchlist`를 실행하세요.")
        return

    frame = frame.copy()
    frame["total_score"] = numeric_series(frame, "total_score")
    frame["catalyst_score"] = numeric_series(frame, "catalyst_score")
    min_score = st.slider(
        "최소 총점",
        min_value=0.0,
        max_value=float(max(frame["total_score"].max(), 1.0)),
        value=0.0,
        step=0.25,
        key="watchlist_min_score",
    )
    symbol_filter = st.text_input("심볼 검색", value="", key="watchlist_symbol_filter")

    filtered = frame.copy()
    filtered = filtered[filtered["total_score"] >= min_score]
    if symbol_filter:
        filtered = filtered[filtered["symbol"].str.contains(symbol_filter.upper(), na=False)]

    c1, c2, c3 = st.columns(3)
    c1.metric("종목 수", len(filtered))
    c2.metric("최고 점수", f"{filtered['total_score'].max():.2f}" if not filtered.empty else "-")
    c3.metric(
        "재료 평균 점수",
        f"{filtered['catalyst_score'].mean():.2f}" if not filtered.empty else "-",
    )

    display = prepare_display_frame(filtered)
    preferred = [
        "symbol",
        "total_score",
        "catalyst_score",
        "technical_score",
        "sympathy_score",
        "market_context_score",
        "social_score",
        "themes",
        "reasons",
    ]
    available = [column for column in preferred if column in display.columns]
    st.dataframe(display[available], use_container_width=True, hide_index=True)


def render_social(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("소셜 신호가 없습니다. 먼저 `psradar analyze-social`을 실행하세요.")
        return

    frame = frame.copy()
    frame["social_score"] = numeric_series(frame, "social_score")
    frame["mention_velocity"] = numeric_series(frame, "mention_velocity")
    frame["cross_platform_count"] = numeric_series(frame, "cross_platform_count")
    min_score = st.slider(
        "최소 소셜 점수",
        min_value=0.0,
        max_value=float(max(frame["social_score"].max(), 1.0)),
        value=0.0,
        step=0.25,
        key="social_min_score",
    )
    filtered = frame[frame["social_score"] >= min_score].copy()
    c1, c2, c3 = st.columns(3)
    c1.metric("종목 수", len(filtered))
    c2.metric(
        "최대 언급 속도",
        f"{filtered['mention_velocity'].max():.2f}" if not filtered.empty else "-",
    )
    c3.metric(
        "최대 플랫폼 수",
        int(filtered["cross_platform_count"].max()) if not filtered.empty else 0,
    )

    display = prepare_display_frame(filtered)
    preferred = [
        "symbol",
        "social_score",
        "mention_count",
        "mention_velocity",
        "unique_authors",
        "cross_platform_count",
        "reasons",
    ]
    available = [column for column in preferred if column in display.columns]
    st.dataframe(display[available], use_container_width=True, hide_index=True)
