from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #07111f;
            --panel: rgba(12, 23, 39, 0.86);
            --panel-2: rgba(16, 31, 52, 0.84);
            --text: #eef3f8;
            --muted: #9fb0c2;
            --accent: #6ee7ff;
            --accent-2: #ffb86b;
            --enter: #78f0a4;
            --watch: #ffe083;
            --avoid: #ff8f8f;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(110, 231, 255, 0.12), transparent 28%),
                radial-gradient(circle at top right, rgba(255, 184, 107, 0.09), transparent 22%),
                linear-gradient(180deg, #050b14 0%, #07111f 45%, #0a1322 100%);
            color: var(--text);
        }
        section[data-testid="stSidebar"] {
            background: rgba(7, 17, 31, 0.96);
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        .hero {
            padding: 1.15rem 1.25rem;
            border-radius: 1.25rem;
            background: linear-gradient(135deg, rgba(110, 231, 255, 0.16), rgba(255, 184, 107, 0.12));
            border: 1px solid rgba(255, 255, 255, 0.10);
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.20);
        }
        .hero h1 {
            margin: 0;
            font-size: 2.2rem;
            letter-spacing: -0.03em;
        }
        .hero p {
            margin: 0.35rem 0 0 0;
            color: var(--muted);
        }
        .panel {
            padding: 1rem 1rem 0.85rem 1rem;
            border-radius: 1rem;
            background: var(--panel);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.14);
        }
        .focus-card {
            padding: 1rem 1rem 0.5rem 1rem;
            border-radius: 1rem;
            background: var(--panel-2);
            border: 1px solid rgba(255, 255, 255, 0.10);
        }
        .section-title {
            margin: 0 0 0.35rem 0;
            font-size: 1.05rem;
            letter-spacing: -0.02em;
        }
        .small-note {
            color: var(--muted);
            font-size: 0.9rem;
        }
        .pill {
            display: inline-block;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 600;
            margin-right: 0.35rem;
            margin-bottom: 0.35rem;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .pill-enter { background: rgba(120, 240, 164, 0.16); color: var(--enter); }
        .pill-watch { background: rgba(255, 224, 131, 0.16); color: var(--watch); }
        .pill-avoid { background: rgba(255, 143, 143, 0.16); color: var(--avoid); }
        .pill-neutral { background: rgba(255, 255, 255, 0.08); color: var(--text); }
        div[data-testid="metric-container"] {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 0.9rem;
            padding: 0.6rem 0.8rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def table_or_empty(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return frame


def render_overview_cards(overview: dict[str, int]) -> None:
    cols = st.columns(5)
    cols[0].metric("스캔 횟수", overview["scan_runs"])
    cols[1].metric("후보군", overview["universe"])
    cols[2].metric("관심종목", overview["watchlist"])
    cols[3].metric("소셜 신호", overview["social"])
    cols[4].metric("모의주문", overview.get("paper_orders", 0))


def render_scan_status(meta: dict[str, Any]) -> None:
    selected_scan_id = str(meta.get("selected_scan_id") or "")
    latest_scan_id = str(meta.get("latest_scan_id") or "")
    market_phase = str(meta.get("market_phase") or "-")
    missing_core = [
        str(value).replace("_", " ") for value in meta.get("missing_core_sections") or []
    ]
    missing_supplemental = [
        str(value).replace("_", " ")
        for value in meta.get("missing_supplemental_sections") or []
    ]

    if not selected_scan_id:
        st.warning(
            "표시 가능한 complete scan이 아직 없습니다.\n\n"
            "Universe, watchlist, premarket, session, replay core 섹션이 채워진 뒤 다시 새로고침하세요."
        )
        return

    lines = [
        f"표시 중 scan: `{selected_scan_id[:14]}`",
        f"최신 raw scan: `{latest_scan_id[:14] or '-'}`",
        f"미국장 구간: `{market_phase}`",
    ]
    if meta.get("is_fallback") and latest_scan_id:
        lines.append("최신 raw scan이 incomplete라 마지막 complete scan으로 fallback했습니다.")
    if missing_core:
        lines.append("최신 raw scan의 누락 core 섹션: " + ", ".join(missing_core))
    if missing_supplemental:
        lines.append("현재 상태에서 비어 있는 supplemental 섹션: " + ", ".join(missing_supplemental))
    if not meta.get("live_data_expected"):
        lines.append("지금은 미국장 프리장/정규장이 아니어서 live movers / prediction audit 공백이 정상일 수 있습니다.")

    renderer = st.warning if meta.get("is_fallback") else st.info
    renderer("\n\n".join(lines))
