from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..formatting import prepare_display_frame, translate_label


def build_replay_detail_frame(
    premarket: pd.DataFrame,
    session: pd.DataFrame,
) -> pd.DataFrame:
    if premarket.empty or session.empty:
        return pd.DataFrame()
    premarket_by_symbol = premarket.set_index("symbol")
    rows: list[dict[str, Any]] = []
    for _, decision_row in session.iterrows():
        symbol = str(decision_row.get("symbol", ""))
        if not symbol or symbol not in premarket_by_symbol.index:
            continue
        signal = premarket_by_symbol.loc[symbol]
        if (
            decision_row.get("decision") == "ENTER"
            and float(decision_row.get("regular_close", 0.0)) > float(signal.get("premarket_high", 0.0))
        ):
            label = "continuation"
        elif (
            float(decision_row.get("regular_high", 0.0)) > float(signal.get("premarket_high", 0.0))
            and float(decision_row.get("regular_close", 0.0)) < float(decision_row.get("anchored_vwap", 0.0))
        ):
            label = "fakeout"
        else:
            label = "fade"
        rows.append(
            {
                "symbol": symbol,
                "리플레이 판정": translate_label(label),
                "정규장 판단": translate_label(decision_row.get("decision")),
                "MFE%": decision_row.get("mfe_pct"),
                "MAE%": decision_row.get("mae_pct"),
            }
        )
    return pd.DataFrame(rows)


def render_report(frame: pd.DataFrame, premarket: pd.DataFrame, session: pd.DataFrame) -> None:
    if frame.empty:
        st.info("리플레이 리포트가 없습니다. 먼저 replay pipeline을 실행하세요.")
        return
    row = frame.iloc[0]
    cols = st.columns(4)
    cols[0].metric("라벨", translate_label(row.get("label", "-")))
    cols[1].metric("기대값", f"{row.get('expectancy', 0.0):.2f}")
    cols[2].metric("수익 요인", f"{row.get('profit_factor', 0.0):.2f}")
    cols[3].metric("Precision@K", f"{row.get('precision_at_k', 0.0):.2f}")
    replay_grade = row.get("replay_grade")
    if isinstance(replay_grade, dict):
        reason = str(replay_grade.get("grade_reason") or "-")
        if bool(replay_grade.get("decision_grade")):
            st.info(f"Replay decision grade: True · {reason}")
        else:
            st.warning(f"Replay decision grade: False · {reason}")

    label = str(row.get("label", ""))
    if label == "continuation":
        st.success("이번 리플레이는 프리장 강세가 정규장 종가까지 이어진 지속 상승형 케이스가 포함되어 있습니다.")
    elif label == "fakeout":
        st.warning("이번 리플레이는 고점 돌파가 나왔지만 AVWAP 아래로 밀린 가짜 돌파 성격이 강합니다.")
    elif label == "fade":
        st.warning("이번 리플레이는 프리장 강세가 정규장으로 이어지지 못한 fade 성격이 강합니다.")

    summary = pd.DataFrame(
        [
            {"metric": "avg_mfe_pct", "value": row.get("average_mfe_pct", 0.0)},
            {"metric": "avg_mae_pct", "value": row.get("average_mae_pct", 0.0)},
            {"metric": "symbols", "value": row.get("symbol_count", 0)},
        ]
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)

    detail = build_replay_detail_frame(premarket, session)
    if not detail.empty:
        st.markdown("**종목별 리플레이 판정**")
        st.dataframe(prepare_display_frame(detail), use_container_width=True, hide_index=True)
