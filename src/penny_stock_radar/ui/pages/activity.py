from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..formatting import (
    coerce_reason_list,
    prepare_display_frame,
)
from .shared import numeric_series, sort_by_available


def live_empty_message(meta: dict[str, Any], market_phase: str) -> str:
    if not meta.get("live_data_expected"):
        return (
            "지금은 미국장 프리장/정규장이 아니어서 저장된 실시간 movers 랭킹이 비어 있을 수 있습니다."
        )
    if market_phase == "premarket":
        return (
            "오늘 프리장 시간에 저장된 실시간 랭킹이 아직 없습니다. "
            "미국 동부 04:00-09:30(한국 17:00-22:30) 사이에 `scan-market-activity --phase auto` 또는 실시간 모드가 돌아야 채워집니다."
        )
    return "아직 저장된 정규장 실시간 랭킹이 없습니다. `scan-market-activity --phase regular` 또는 실시간 모드를 실행하면 채워집니다."


def best_live_rank(*values: object) -> int:
    ranks = []
    for value in values:
        try:
            rank = int(value)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            ranks.append(rank)
    return min(ranks) if ranks else 9999


def live_momentum_score(
    *,
    best_rank: object,
    label: object,
    behavioral_score: object,
    trap_score: object,
) -> float:
    score = 0.0
    rank = best_live_rank(best_rank)
    if rank <= 1:
        score += 4.0
    elif rank <= 3:
        score += 3.0
    elif rank <= 5:
        score += 2.25
    elif rank <= 10:
        score += 1.25
    elif rank <= 15:
        score += 0.5

    normalized_label = str(label or "")
    if normalized_label in {"OPENING_RANGE_CANDIDATE", "CONDITIONAL_ENTRY"}:
        score += 1.25
    elif normalized_label == "NEWS_CHECK_FIRST":
        score += 0.75
    elif normalized_label == "WAIT_PULLBACK":
        score += 0.4
    elif normalized_label == "NO_CHASE":
        score -= 0.75

    try:
        behavioral = float(behavioral_score)
    except (TypeError, ValueError):
        behavioral = 0.0
    if behavioral >= 70.0:
        score += 0.75
    elif behavioral >= 60.0:
        score += 0.35

    try:
        trap = float(trap_score)
    except (TypeError, ValueError):
        trap = 0.0
    if trap >= 70.0:
        score -= 1.0
    elif trap >= 55.0:
        score -= 0.4

    return round(max(score, 0.0), 2)


def render_live_activity_panel(
    title: str,
    frame: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    empty_message: str,
) -> None:
    st.markdown(f"**{title}**")
    if frame.empty:
        st.info(empty_message)
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("실시간 추적 종목", len(frame))
    c2.metric(
        "예측 적중 수",
        int(
            outcomes["outcome"].isin(["matched_both", "matched_pct", "matched_volume"]).sum()
        )
        if not outcomes.empty and "outcome" in outcomes.columns
        else 0,
    )
    c3.metric(
        "새 리더 수",
        int((outcomes["outcome"] == "unpredicted_leader").sum())
        if not outcomes.empty and "outcome" in outcomes.columns
        else 0,
    )

    left, right = st.columns(2)

    with left:
        st.markdown("상승률 상위")
        top_pct = prepare_live_activity_display(sort_by_available(frame, ["pct_rank", "symbol"]))
        display = prepare_display_frame(top_pct)
        preferred = [
            "pct_rank",
            "symbol",
            "predicted",
            "last_price",
            "pct_change",
            "volume",
            "dollar_volume",
            "spread_pct",
            "leader_persistence_score",
            "pullback_absorption_score",
            "trap_score",
            "trade_call",
            "entry_basis",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available].head(10), use_container_width=True, hide_index=True)

    with right:
        st.markdown("거래량 상위")
        top_volume = prepare_live_activity_display(sort_by_available(frame, ["volume_rank", "symbol"]))
        display = prepare_display_frame(top_volume)
        preferred = [
            "volume_rank",
            "symbol",
            "predicted",
            "volume",
            "dollar_volume",
            "pct_change",
            "spread_pct",
            "leader_persistence_score",
            "pullback_absorption_score",
            "trap_score",
            "trade_call",
            "entry_basis",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available].head(10), use_container_width=True, hide_index=True)

    if not outcomes.empty:
        st.markdown("예측 vs 실제")
        enriched_outcomes = outcomes.copy()
        if "analysis_label" in enriched_outcomes.columns:
            enriched_outcomes["trade_call"] = enriched_outcomes["analysis_label"]
        enriched_outcomes["best_live_rank"] = enriched_outcomes.apply(
            lambda row: best_live_rank(row.get("pct_rank"), row.get("volume_rank")),
            axis=1,
        )
        enriched_outcomes = sort_by_available(
            enriched_outcomes,
            ["best_live_rank", "pct_rank", "volume_rank", "predicted", "symbol"],
            ascending=[True, True, True, False, True],
        )
        display = prepare_display_frame(enriched_outcomes)
        preferred = [
            "best_live_rank",
            "symbol",
            "predicted",
            "watchlist_rank",
            "pct_rank",
            "volume_rank",
            "pct_change",
            "dollar_volume",
            "analysis_label",
            "trade_call",
            "outcome",
            "reasons",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)


def prepare_live_activity_display(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["trade_call"] = result.apply(live_trade_call, axis=1)
    result["entry_basis"] = result.apply(live_entry_basis, axis=1)
    return result


def live_trade_call(row: pd.Series) -> str:
    label = str(row.get("analysis_label") or "")
    return label or "-"


def live_entry_basis(row: pd.Series) -> str:
    label = str(row.get("analysis_label") or "")
    reasons = coerce_reason_list(row.get("reasons"))
    pieces: list[str] = []

    if any(reason in reasons for reason in ("live_dollar_volume_strong", "live_dollar_volume_ok")):
        pieces.append("거래대금이 받쳐줌")
    if "spread_ok" in reasons:
        pieces.append("스프레드가 비교적 안정적")
    if any(reason in reasons for reason in ("pct_leader", "pct_momentum")):
        pieces.append("모멘텀이 살아 있음")
    if any(reason in reasons for reason in ("leader_persistence_strong", "top5_leader_persistence")):
        pieces.append("상위 5위권 리더 자리를 유지 중")
    if "leader_reclaim" in reasons:
        pieces.append("잠깐 밀린 뒤 다시 리더군으로 복귀 중")
    if "pullback_absorption" in reasons:
        pieces.append("눌림에서 매도 물량을 흡수하는 흐름")
    if "watchlist_predicted" in reasons:
        pieces.append("프리장 전 레이더와 맥락이 맞음")
    if "context_missing" in reasons:
        pieces.append("다만 재료 맥락 확인이 아직 부족함")
    if "late_extension" in reasons:
        pieces.append("이미 많이 뻗어 늦을 수 있음")
    if "spread_wide" in reasons:
        pieces.append("스프레드가 넓어 체결 리스크가 큼")
    if "trap_warning" in reasons:
        pieces.append("가짜 돌파 위험이 커져 추격을 더 보수적으로 봐야 함")

    if label == "CONDITIONAL_ENTRY":
        pieces.append("지금 즉시 추격이 아니라 첫 눌림 후 재돌파에서만")
    elif label == "OPENING_RANGE_CANDIDATE":
        pieces.append("시초 range 유지 후 고점 재돌파가 전제")
    elif label == "NEWS_CHECK_FIRST":
        pieces.append("뉴스/공시 확인 전 신규 진입 보류")
    elif label == "WAIT_PULLBACK":
        pieces.append("VWAP 또는 분봉 고점 재회복 전 대기")
    elif label == "NO_CHASE":
        pieces.append("현재 자리 추격 금지")

    if not pieces:
        return "-"
    return " / ".join(dict.fromkeys(pieces))


def render_premarket(
    frame: pd.DataFrame,
    live_frame: pd.DataFrame,
    outcomes: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    if frame.empty:
        st.info("프리장 신호가 없습니다. 먼저 replay pipeline을 실행하세요.")
    else:
        frame = frame.copy()
        frame["quality_score"] = numeric_series(frame, "quality_score")
        frame["premarket_rvol"] = numeric_series(frame, "premarket_rvol")
        frame["dollar_volume"] = numeric_series(frame, "dollar_volume")
        min_quality = st.slider(
            "최소 품질 점수",
            min_value=0.0,
            max_value=float(max(frame["quality_score"].max(), 1.0)),
            value=0.0,
            step=0.25,
            key="premarket_min_quality",
        )
        filtered = frame[frame["quality_score"] >= min_quality].copy()
        c1, c2, c3 = st.columns(3)
        c1.metric("신호 수", len(filtered))
        c2.metric(
            "최대 RVOL", f"{filtered['premarket_rvol'].max():.2f}" if not filtered.empty else "-"
        )
        c3.metric(
            "최대 거래대금",
            f"{filtered['dollar_volume'].max():,.0f}" if not filtered.empty else "-",
        )

        display = prepare_display_frame(filtered)
        preferred = [
            "symbol",
            "quality_score",
            "premarket_rvol",
            "dollar_volume",
            "tps",
            "spread_pct",
            "round_level_break",
            "tape_anomaly",
            "reasons",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)

    st.divider()
    render_live_activity_panel(
        "프리장 실시간 Top Movers",
        live_frame,
        outcomes,
        empty_message=live_empty_message(meta, "premarket"),
    )


def render_session(
    frame: pd.DataFrame,
    live_frame: pd.DataFrame,
    outcomes: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    if frame.empty:
        st.info("정규장 판단 결과가 없습니다. 먼저 replay pipeline을 실행하세요.")
    else:
        frame = frame.copy()
        if "decision" not in frame.columns:
            frame["decision"] = "UNKNOWN"
        decision_filter = st.multiselect(
            "판단 필터",
            options=sorted(frame["decision"].dropna().unique().tolist()),
            default=sorted(frame["decision"].dropna().unique().tolist()),
            key="session_decision_filter",
        )
        filtered = frame[frame["decision"].isin(decision_filter)].copy()
        c1, c2, c3 = st.columns(3)
        c1.metric("판단 수", len(filtered))
        c2.metric("진입", int((filtered["decision"] == "ENTER").sum()))
        c3.metric("관찰", int((filtered["decision"] == "WATCH").sum()))

        display = prepare_display_frame(filtered)
        preferred = [
            "symbol",
            "decision",
            "anchored_vwap",
            "extension_z",
            "mfe_pct",
            "mae_pct",
            "reasons",
        ]
        available = [column for column in preferred if column in display.columns]
        st.dataframe(display[available], use_container_width=True, hide_index=True)

    st.divider()
    render_live_activity_panel(
        "정규장 실시간 Top Movers",
        live_frame,
        outcomes,
        empty_message=live_empty_message(meta, "regular"),
    )
