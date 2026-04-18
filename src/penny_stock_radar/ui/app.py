from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd
import streamlit as st

if __package__ in {None, ""}:
    SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    from penny_stock_radar.config import get_settings
    from penny_stock_radar.runtime_scripts import full_refresh_command
    from penny_stock_radar.services.market_activity import MarketActivityScanner
    from penny_stock_radar.services.live_monitor import LiveMonitor
    from penny_stock_radar.services.trade_plan import TradePlanService
    from penny_stock_radar.ui.formatting import (
        coerce_reason_list as _coerce_reason_list,
        metric_integer as _metric_integer,
        metric_number as _metric_number,
        prepare_display_frame as _prepare_display_frame,
        translate_label as _translate_label,
        translate_reason as _translate_reason,
    )
    from penny_stock_radar.ui.paper_panel import render_paper_trading_panel
    from penny_stock_radar.ui.data import (
        load_paper_backtest_kpis,
        load_paper_execution_quality,
        load_market_activity,
        load_latest_paper_run,
        load_paper_orders,
        load_paper_positions,
        load_paper_predictor_kpis,
        load_paper_regime_split,
        load_paper_snapshots,
        load_paper_strategy_runs,
        load_scan_selection_metadata,
        load_premarket,
        load_prediction_outcomes,
        load_replay_report,
        load_run_overview,
        load_session_decisions,
        load_social,
        load_universe,
        load_watchlist,
    )
else:
    from ..config import get_settings
    from ..runtime_scripts import full_refresh_command
    from ..services.market_activity import MarketActivityScanner
    from ..services.live_monitor import LiveMonitor
    from ..services.trade_plan import TradePlanService
    from .formatting import (
        coerce_reason_list as _coerce_reason_list,
        metric_integer as _metric_integer,
        metric_number as _metric_number,
        prepare_display_frame as _prepare_display_frame,
        translate_label as _translate_label,
        translate_reason as _translate_reason,
    )
    from .paper_panel import render_paper_trading_panel
    from .data import (
        load_paper_backtest_kpis,
        load_paper_execution_quality,
        load_market_activity,
        load_latest_paper_run,
        load_paper_orders,
        load_paper_positions,
        load_paper_predictor_kpis,
        load_paper_regime_split,
        load_paper_snapshots,
        load_paper_strategy_runs,
        load_scan_selection_metadata,
        load_premarket,
        load_prediction_outcomes,
        load_replay_report,
        load_run_overview,
        load_session_decisions,
        load_social,
        load_universe,
        load_watchlist,
    )


def _inject_styles() -> None:
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


def _table_or_empty(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return frame


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_full_refresh() -> tuple[bool, str]:
    root_dir = _project_root()
    command = full_refresh_command(root_dir)
    result = subprocess.run(
        command,
        cwd=root_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, (result.stderr or result.stdout or "Unknown error").strip()


def _sidebar_controls(settings_path: Path) -> tuple[Path, bool, int, int]:
    st.sidebar.title("레이더 설정")
    db_value = st.sidebar.text_input("SQLite DB 경로", value=str(settings_path))
    st.sidebar.caption("최신 저장 스냅샷을 읽는 전용 GUI입니다. CLI 파이프라인 실행 후 새로고침하세요.")
    if st.sidebar.button("전체 최신화 실행", use_container_width=True):
        with st.sidebar:
            with st.spinner("최신 데이터를 다시 계산하는 중입니다..."):
                ok, message = _run_full_refresh()
        if ok:
            st.cache_data.clear()
            st.sidebar.success("최신화가 완료되었습니다.")
            st.rerun()
        else:
            st.sidebar.error("최신화 실행 중 오류가 발생했습니다.")
            st.sidebar.code(message[:3000] if message else "No output")
    st.sidebar.divider()
    st.sidebar.subheader("실시간 모드")
    live_enabled = st.sidebar.toggle("실시간 자동 새로고침", value=False)
    refresh_seconds = int(
        st.sidebar.slider("새로고침 주기(초)", min_value=3, max_value=30, value=5, step=1)
    )
    live_limit = int(
        st.sidebar.slider("실시간 조회 종목 수", min_value=1, max_value=10, value=5, step=1)
    )
    return Path(db_value), live_enabled, refresh_seconds, live_limit


def _overview_cards(overview: dict[str, int]) -> None:
    cols = st.columns(5)
    cols[0].metric("스캔 횟수", overview["scan_runs"])
    cols[1].metric("후보군", overview["universe"])
    cols[2].metric("관심종목", overview["watchlist"])
    cols[3].metric("소셜 신호", overview["social"])
    cols[4].metric("모의주문", overview.get("paper_orders", 0))


def _render_scan_status(meta: dict[str, Any]) -> None:
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


def _live_empty_message(meta: dict[str, Any], market_phase: str) -> str:
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


def _prepare_leaderboard(
    watchlist: pd.DataFrame,
    premarket: pd.DataFrame,
    session: pd.DataFrame,
    social: pd.DataFrame,
    live_premarket: pd.DataFrame | None = None,
    live_regular: pd.DataFrame | None = None,
) -> pd.DataFrame:
    active_live = pd.DataFrame()
    if live_regular is not None and not live_regular.empty:
        active_live = live_regular
    elif live_premarket is not None and not live_premarket.empty:
        active_live = live_premarket

    symbols = set()
    for frame in (watchlist, premarket, session, social, active_live):
        if not frame.empty and "symbol" in frame.columns:
            symbols.update(frame["symbol"].dropna().astype(str).tolist())

    if not symbols:
        return pd.DataFrame()

    leaderboard = pd.DataFrame({"symbol": sorted(symbols)})

    if not watchlist.empty:
        cols = [
            "symbol",
            "total_score",
            "catalyst_score",
            "technical_score",
            "sympathy_score",
            "social_score",
            "reasons",
            "themes",
        ]
        available = [column for column in cols if column in watchlist.columns]
        subset = watchlist[available].rename(
            columns={
                "total_score": "watchlist_score",
                "social_score": "watchlist_social_score",
                "reasons": "watchlist_reasons",
                "themes": "watchlist_themes",
            }
        )
        leaderboard = leaderboard.merge(subset, on="symbol", how="left")

    if not premarket.empty:
        cols = [
            "symbol",
            "quality_score",
            "premarket_rvol",
            "dollar_volume",
            "spread_pct",
            "tape_anomaly",
            "reasons",
        ]
        available = [column for column in cols if column in premarket.columns]
        subset = premarket[available].rename(columns={"reasons": "premarket_reasons"})
        leaderboard = leaderboard.merge(subset, on="symbol", how="left")

    if not session.empty:
        cols = [
            "symbol",
            "decision",
            "extension_z",
            "mfe_pct",
            "mae_pct",
            "reasons",
        ]
        available = [column for column in cols if column in session.columns]
        subset = session[available].rename(columns={"reasons": "session_reasons"})
        leaderboard = leaderboard.merge(subset, on="symbol", how="left")

    if not social.empty:
        cols = [
            "symbol",
            "social_score",
            "mention_velocity",
            "mention_count",
            "cross_platform_count",
            "reasons",
        ]
        available = [column for column in cols if column in social.columns]
        subset = social[available].rename(
            columns={
                "social_score": "social_velocity_score",
                "reasons": "social_reasons",
            }
        )
        leaderboard = leaderboard.merge(subset, on="symbol", how="left")

    if not active_live.empty:
        cols = [
            "symbol",
            "pct_rank",
            "volume_rank",
            "analysis_label",
            "behavioral_score",
            "leader_persistence_score",
            "pullback_absorption_score",
            "trap_score",
            "pct_change",
            "dollar_volume",
        ]
        available = [column for column in cols if column in active_live.columns]
        subset = active_live[available].copy()
        subset["live_best_rank"] = subset.apply(
            lambda row: _best_live_rank(row.get("pct_rank"), row.get("volume_rank")),
            axis=1,
        )
        subset["live_momentum_score"] = subset.apply(
            lambda row: _live_momentum_score(
                best_rank=row.get("live_best_rank"),
                label=row.get("analysis_label"),
                behavioral_score=row.get("behavioral_score"),
                trap_score=row.get("trap_score"),
            ),
            axis=1,
        )
        subset = subset.rename(
            columns={
                "analysis_label": "live_analysis_label",
                "behavioral_score": "live_behavioral_score",
                "leader_persistence_score": "live_leader_persistence_score",
                "pullback_absorption_score": "live_pullback_absorption_score",
                "trap_score": "live_trap_score",
                "pct_change": "live_pct_change",
                "dollar_volume": "live_dollar_volume",
            }
        )
        leaderboard = leaderboard.merge(subset, on="symbol", how="left")

    decision_weight = leaderboard.get("decision", pd.Series(index=leaderboard.index, dtype=object)).map(
        {"ENTER": 3.0, "WATCH": 1.5, "AVOID": -1.0}
    )
    leaderboard["decision_weight"] = decision_weight.fillna(0.0)
    leaderboard["watchlist_score"] = _numeric_series(leaderboard, "watchlist_score")
    leaderboard["quality_score"] = _numeric_series(leaderboard, "quality_score")
    leaderboard["social_velocity_score"] = _numeric_series(leaderboard, "social_velocity_score")
    leaderboard["live_momentum_score"] = _numeric_series(leaderboard, "live_momentum_score")
    leaderboard["radar_score"] = (
        leaderboard["watchlist_score"]
        + leaderboard["quality_score"]
        + leaderboard["social_velocity_score"]
        + leaderboard["decision_weight"]
        + leaderboard["live_momentum_score"]
    )
    leaderboard = leaderboard.sort_values(
        ["radar_score", "live_momentum_score", "quality_score", "watchlist_score", "symbol"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    return leaderboard


def _best_live_rank(*values: object) -> int:
    ranks = []
    for value in values:
        try:
            rank = int(value)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            ranks.append(rank)
    return min(ranks) if ranks else 9999


def _live_momentum_score(
    *,
    best_rank: object,
    label: object,
    behavioral_score: object,
    trap_score: object,
) -> float:
    score = 0.0
    rank = _best_live_rank(best_rank)
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


def _decision_pill(decision: str | None) -> str:
    if decision in {"ENTER", "CONDITIONAL_ENTRY", "OPENING_RANGE_CANDIDATE"}:
        klass = "pill-enter"
    elif decision in {"WATCH", "WAIT_PULLBACK", "NEWS_CHECK_FIRST"}:
        klass = "pill-watch"
    elif decision in {"AVOID", "NO_CHASE"}:
        klass = "pill-avoid"
    else:
        klass = "pill-neutral"
        decision = decision or "NO SIGNAL"
    return f'<span class="pill {klass}">{_translate_label(decision)}</span>'


def _render_command_center(
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
    leaderboard = _prepare_leaderboard(
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

    top_symbol = leaderboard.iloc[0]["symbol"]
    top_score = float(leaderboard.iloc[0]["radar_score"])
    enter_count = int((session["decision"] == "ENTER").sum()) if not session.empty else 0
    if not social.empty:
        social_rank = social.copy()
        social_rank["social_score"] = _numeric_series(social_rank, "social_score")
        top_social = str(social_rank.sort_values(["social_score", "symbol"], ascending=[False, True]).iloc[0]["symbol"])
    else:
        top_social = "-"
    report_label = _translate_label(replay.iloc[0]["label"]) if not replay.empty else "-"

    cols = st.columns(4)
    cols[0].metric("최상위 종목", top_symbol)
    cols[1].metric("레이더 점수", f"{top_score:.2f}")
    cols[2].metric("진입 신호 수", enter_count)
    cols[3].metric("리플레이 라벨", report_label)

    if not paper_run.empty:
        row = paper_run.iloc[0]
        open_count = (
            int((paper_positions["status"] == "OPEN").sum())
            if not paper_positions.empty and "status" in paper_positions.columns
            else 0
        )
        paper_cols = st.columns(5)
        paper_cols[0].metric("모의계좌", _translate_label(row.get("status")))
        paper_cols[1].metric("에쿼티", _metric_number(row.get("equity"), 2))
        paper_cols[2].metric("누적 실현손익", _metric_number(row.get("realized_pnl"), 2))
        paper_cols[3].metric("보유 포지션", open_count)
        paper_cols[4].metric("손익비", _metric_number(row.get("reward_risk_ratio"), 2))

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

        display_columns = [
            "symbol",
            "radar_score",
            "live_momentum_score",
            "live_best_rank",
            "live_analysis_label",
            "watchlist_score",
            "quality_score",
            "social_velocity_score",
            "decision",
            "premarket_rvol",
            "dollar_volume",
        ]
        available = [column for column in display_columns if column in leaderboard.columns]
        display = leaderboard[available].copy()
        display = display.rename(
            columns={
                "live_momentum_score": "live",
                "live_best_rank": "live_rank",
                "live_analysis_label": "live_call",
                "watchlist_score": "setup",
                "quality_score": "pm_quality",
                "social_velocity_score": "social",
                "premarket_rvol": "pm_rvol",
                "dollar_volume": "pm_dv",
            }
        )
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        symbols = leaderboard["symbol"].tolist()
        default_index = 0 if symbols else None
        selected = st.selectbox("집중 분석 종목", options=symbols, index=default_index)
        _render_symbol_focus(selected, universe, watchlist, premarket, session, social, leaderboard)


def _render_symbol_focus(
    symbol: str,
    universe: pd.DataFrame,
    watchlist: pd.DataFrame,
    premarket: pd.DataFrame,
    session: pd.DataFrame,
    social: pd.DataFrame,
    leaderboard: pd.DataFrame,
) -> None:
    leader_row = _first_row(leaderboard, symbol)
    universe_row = _first_row(universe, symbol)
    watchlist_row = _first_row(watchlist, symbol)
    premarket_row = _first_row(premarket, symbol)
    session_row = _first_row(session, symbol)
    social_row = _first_row(social, symbol)

    st.markdown('<div class="focus-card">', unsafe_allow_html=True)
    decision = session_row.get("decision") if session_row else None
    st.markdown(
        f"<h3 class='section-title'>{symbol} {_decision_pill(decision)}</h3>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='small-note'>이 종목이 아직 진입 가능한지 가장 빠르게 판단하는 요약입니다.</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns(3)
    cols[0].metric(
        "현재가",
        _metric_number(universe_row.get("price") if universe_row else None, 2),
    )
    cols[1].metric(
        "셋업 점수",
        _metric_number(watchlist_row.get("total_score") if watchlist_row else None, 2),
    )
    cols[2].metric(
        "프리장 품질",
        _metric_number(premarket_row.get("quality_score") if premarket_row else None, 2),
    )

    cols = st.columns(3)
    cols[0].metric(
        "유통주식수",
        _metric_integer(universe_row.get("float_shares") if universe_row else None),
    )
    cols[1].metric(
        "최대 유리폭(MFE%)",
        _metric_number(session_row.get("mfe_pct") if session_row else None, 2),
    )
    cols[2].metric(
        "소셜 점수",
        _metric_number(social_row.get("social_score") if social_row else None, 2),
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

    _render_interpretation_summary(symbol, session_row, premarket_row, watchlist_row, social_row)
    _render_reason_block("후보 선정 이유", watchlist_row.get("reasons") if watchlist_row else None)
    _render_reason_block(
        "프리장 판단 이유", premarket_row.get("reasons") if premarket_row else None
    )
    _render_reason_block(
        "정규장 판단 이유", session_row.get("reasons") if session_row else None
    )
    _render_reason_block("소셜 판단 이유", social_row.get("reasons") if social_row else None)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_reason_block(title: str, value: Any) -> None:
    reasons = _coerce_reason_list(value)
    st.markdown(f"**{title}**")
    if not reasons:
        st.markdown("<div class='small-note'>기록된 신호가 없습니다.</div>", unsafe_allow_html=True)
        return
    for reason in reasons:
        st.markdown(f"- {_translate_reason(reason)}")


def _render_interpretation_summary(
    symbol: str,
    session_row: dict[str, Any],
    premarket_row: dict[str, Any],
    watchlist_row: dict[str, Any],
    social_row: dict[str, Any],
) -> None:
    decision = _translate_label(session_row.get("decision")) if session_row else "-"
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
            highlight_reasons.extend(_coerce_reason_list(row["reasons"]))
    if highlight_reasons:
        translated = [_translate_reason(reason) for reason in highlight_reasons[:4]]
        lines.append("핵심 근거: " + ", ".join(translated))

    st.info("\n\n".join(lines))


def _first_row(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    subset = frame[frame["symbol"] == symbol]
    if subset.empty:
        return {}
    return subset.iloc[0].to_dict()


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index, dtype=float)
    series = pd.to_numeric(frame[column], errors="coerce")
    return series.fillna(0.0)


def _render_universe(frame: pd.DataFrame) -> None:
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

    display = _prepare_display_frame(filtered)
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


def _render_watchlist(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("관심종목이 없습니다. 먼저 `psradar build-watchlist`를 실행하세요.")
        return

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

    display = _prepare_display_frame(filtered)
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


def _render_live_activity_panel(
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
        top_pct = _prepare_live_activity_display(frame.sort_values(["pct_rank", "symbol"]).copy())
        display = _prepare_display_frame(top_pct)
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
        top_volume = _prepare_live_activity_display(frame.sort_values(["volume_rank", "symbol"]).copy())
        display = _prepare_display_frame(top_volume)
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
            lambda row: _best_live_rank(row.get("pct_rank"), row.get("volume_rank")),
            axis=1,
        )
        enriched_outcomes = enriched_outcomes.sort_values(
            ["best_live_rank", "pct_rank", "volume_rank", "predicted", "symbol"],
            ascending=[True, True, True, False, True],
        )
        display = _prepare_display_frame(enriched_outcomes)
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


def _prepare_live_activity_display(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    result["trade_call"] = result.apply(_live_trade_call, axis=1)
    result["entry_basis"] = result.apply(_live_entry_basis, axis=1)
    return result


def _live_trade_call(row: pd.Series) -> str:
    label = str(row.get("analysis_label") or "")
    return label or "-"


def _live_entry_basis(row: pd.Series) -> str:
    label = str(row.get("analysis_label") or "")
    reasons = _coerce_reason_list(row.get("reasons"))
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


def _render_premarket(
    frame: pd.DataFrame,
    live_frame: pd.DataFrame,
    outcomes: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    if frame.empty:
        st.info("프리장 신호가 없습니다. 먼저 replay pipeline을 실행하세요.")
    else:
        frame = frame.copy()
        frame["quality_score"] = _numeric_series(frame, "quality_score")
        frame["premarket_rvol"] = _numeric_series(frame, "premarket_rvol")
        frame["dollar_volume"] = _numeric_series(frame, "dollar_volume")
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

        display = _prepare_display_frame(filtered)
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
    _render_live_activity_panel(
        "프리장 실시간 Top Movers",
        live_frame,
        outcomes,
        empty_message=_live_empty_message(meta, "premarket"),
    )


def _render_session(
    frame: pd.DataFrame,
    live_frame: pd.DataFrame,
    outcomes: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    if frame.empty:
        st.info("정규장 판단 결과가 없습니다. 먼저 replay pipeline을 실행하세요.")
    else:
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

        display = _prepare_display_frame(filtered)
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
    _render_live_activity_panel(
        "정규장 실시간 Top Movers",
        live_frame,
        outcomes,
        empty_message=_live_empty_message(meta, "regular"),
    )


def _build_replay_detail_frame(
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
                "리플레이 판정": _translate_label(label),
                "정규장 판단": _translate_label(decision_row.get("decision")),
                "MFE%": decision_row.get("mfe_pct"),
                "MAE%": decision_row.get("mae_pct"),
            }
        )
    return pd.DataFrame(rows)


def _render_report(frame: pd.DataFrame, premarket: pd.DataFrame, session: pd.DataFrame) -> None:
    if frame.empty:
        st.info("리플레이 리포트가 없습니다. 먼저 replay pipeline을 실행하세요.")
        return
    row = frame.iloc[0]
    cols = st.columns(4)
    cols[0].metric("라벨", _translate_label(row.get("label", "-")))
    cols[1].metric("기대값", f"{row.get('expectancy', 0.0):.2f}")
    cols[2].metric("수익 요인", f"{row.get('profit_factor', 0.0):.2f}")
    cols[3].metric("Precision@K", f"{row.get('precision_at_k', 0.0):.2f}")

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

    detail = _build_replay_detail_frame(premarket, session)
    if not detail.empty:
        st.markdown("**종목별 리플레이 판정**")
        st.dataframe(_prepare_display_frame(detail), use_container_width=True, hide_index=True)


def _render_live_mode(
    settings,
    universe: pd.DataFrame,
    watchlist: pd.DataFrame,
    enabled: bool,
    refresh_seconds: int,
    symbol_limit: int,
) -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("<h3 class='section-title'>실시간 모니터</h3>", unsafe_allow_html=True)
    st.markdown(
        "<div class='small-note'>설정된 live provider(KIS/Alpaca)에서 최신 trade, quote, snapshot을 주기적으로 다시 조회합니다. `시장 데이터 시각`은 마지막 실제 체결/스냅샷 시각이라 거래가 없으면 고정될 수 있습니다.</div>",
        unsafe_allow_html=True,
    )

    symbols = []
    if not watchlist.empty and "symbol" in watchlist.columns:
        symbols = watchlist["symbol"].dropna().astype(str).tolist()[:symbol_limit]

    scan_symbols: list[str] = []
    if not watchlist.empty and "symbol" in watchlist.columns:
        scan_symbols.extend(watchlist["symbol"].dropna().astype(str).tolist())
    if not universe.empty and "symbol" in universe.columns:
        passed = universe
        if "passed_filters" in passed.columns:
            passed = passed[passed["passed_filters"] == 1]
        for symbol in passed["symbol"].dropna().astype(str).tolist():
            if symbol not in scan_symbols:
                scan_symbols.append(symbol)
    scan_limit = max(symbol_limit * 4, min(len(scan_symbols), 20)) if scan_symbols else 0

    if not symbols and not scan_symbols:
        st.info("실시간 조회 대상이 없습니다. 먼저 후보군/관심종목을 생성하세요.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    monitor = LiveMonitor(settings)
    scanner = MarketActivityScanner(settings)
    available, reason = monitor.provider_status()
    if not available:
        st.warning(
            f"실시간 provider가 아직 준비되지 않았습니다. 현재 상태: {reason}. `.env`에 API 키를 넣어주세요."
        )
        st.markdown(
            "<div class='small-note'>Finder에서 <code>live_api_setup.command</code>를 더블클릭하면 .env 파일을 바로 열 수 있습니다.</div>",
            unsafe_allow_html=True,
        )
        st.code(
            "\n".join(
                [
                    "PENNY_STOCK_LIVE_MARKET_PROVIDER=kis",
                    "PENNY_STOCK_KIS_APP_KEY=",
                    "PENNY_STOCK_KIS_APP_SECRET=",
                    "PENNY_STOCK_KIS_NASDAQ_MASTER_PATH=./data/kis_master/NASMST.COD",
                    "PENNY_STOCK_KIS_NYSE_MASTER_PATH=./data/kis_master/NYSMST.COD",
                    "PENNY_STOCK_KIS_AMEX_MASTER_PATH=./data/kis_master/AMSMST.COD",
                    "",
                    "PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca",
                    "PENNY_STOCK_ALPACA_API_KEY=",
                    "PENNY_STOCK_ALPACA_SECRET_KEY=",
                ]
            )
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    if enabled:
        st.success(f"{refresh_seconds}초마다 자동 새로고침 중입니다.")

        @st.fragment(run_every=f"{refresh_seconds}s")
        def _live_fragment() -> None:
            _render_live_market_workspace(
                monitor,
                scanner,
                symbols,
                scan_limit=scan_limit,
                symbol_limit=symbol_limit,
            )

        _live_fragment()
    else:
        st.info("자동 새로고침이 꺼져 있습니다. 사이드바에서 실시간 자동 새로고침을 켜면 주기적으로 갱신됩니다.")
        _render_live_market_workspace(
            monitor,
            scanner,
            symbols,
            scan_limit=scan_limit,
            symbol_limit=symbol_limit,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_live_market_workspace(
    monitor: LiveMonitor,
    scanner: MarketActivityScanner,
    symbols: list[str],
    *,
    scan_limit: int,
    symbol_limit: int,
) -> None:
    phase = scanner.resolve_market_phase("auto")
    phase_label = {
        "premarket": "프리장",
        "regular": "정규장",
        "afterhours": "애프터장",
        "closed": "장외/휴장",
    }.get(phase, phase)
    st.markdown(f"**현재 인식된 장 상태:** `{phase_label}`")

    if phase in {"premarket", "regular"} and scan_limit > 0:
        csv_path = _project_root() / "sample_outputs" / f"{phase}_prediction_vs_actual.csv"
        try:
            result = scanner.scan(
                phase=phase,
                scan_limit=scan_limit,
                top_limit=max(symbol_limit, 10),
                comparison_csv=csv_path,
            )
            live_frame = pd.DataFrame([row.model_dump(mode="json") for row in result.activity])
            outcome_frame = pd.DataFrame([row.model_dump(mode="json") for row in result.outcomes])
            _render_live_activity_panel(
                f"{phase_label} 실시간 {scanner.settings.market_scope_label} movers",
                live_frame,
                outcome_frame,
                empty_message="실시간 시장 스캔 결과가 없습니다.",
            )
            trade_plan = TradePlanService(scanner.settings).generate(
                phase=phase,
                activity=result.activity,
                export=True,
            )
            st.divider()
            _render_trade_plan_panel(trade_plan)
            st.caption(f"비교 CSV: {result.comparison_csv}")
        except RuntimeError as exc:
            st.warning(f"실시간 시장 스캔을 저장하지 못했습니다: {exc}")
    else:
        st.info("지금은 프리장/정규장이 아니어서 실시간 movers 랭킹 저장을 건너뜁니다.")

    if symbols:
        st.divider()
        st.markdown("**관심종목 실시간 스냅샷**")
        _render_live_snapshot_table(monitor, symbols, symbol_limit)


def _render_live_snapshot_table(
    monitor: LiveMonitor,
    symbols: list[str],
    symbol_limit: int,
) -> None:
    try:
        rows = monitor.fetch(symbols, limit=symbol_limit)
    except RuntimeError as exc:
        st.error(str(exc))
        return

    if not rows:
        st.warning("실시간 스냅샷을 받아오지 못했습니다.")
        return

    frame = pd.DataFrame(
        [
            {
                "symbol": row.symbol,
                "source": row.source,
                "market_phase": row.market_phase,
                "trade_price": row.trade_price,
                "trade_size": row.trade_size,
                "bid_price": row.bid_price,
                "ask_price": row.ask_price,
                "spread_pct": row.spread_pct,
                "market_status": row.market_status,
                "market_data_at": row.market_data_at.isoformat() if row.market_data_at else "-",
                "polled_at": row.polled_at.isoformat(),
                "data_age_seconds": round(row.data_age_seconds, 1) if row.data_age_seconds is not None else "-",
            }
            for row in rows
        ]
    )

    cols = st.columns(4)
    cols[0].metric("실시간 종목 수", len(frame))
    cols[1].metric(
        "최고 체결가",
        f"{frame['trade_price'].dropna().max():.4f}" if frame["trade_price"].notna().any() else "-",
    )
    cols[2].metric(
        "최저 스프레드",
        f"{frame['spread_pct'].dropna().min() * 100:.2f}%"
        if frame["spread_pct"].notna().any()
        else "-",
    )
    cols[3].metric("조회 종목", ", ".join(frame["symbol"].tolist()))

    st.dataframe(_prepare_display_frame(frame), use_container_width=True, hide_index=True)


def _render_paper_trading(
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


def _render_trade_plan_panel(result) -> None:
    st.markdown("**실전 실행 플랜**")
    cols = st.columns(5)
    cols[0].metric("장 상태", result.market_phase)
    cols[1].metric("레짐", result.regime)
    cols[2].metric("Daily Lock", "ON" if result.daily_loss_locked else "OFF")
    cols[3].metric("당일 손익", f"{result.current_day_pnl:.2f}")
    cols[4].metric("현재 오픈 리스크", f"{result.current_open_risk:.2f}")

    def _plan_frame(actionability: str) -> pd.DataFrame:
        rows = [
            row.model_dump(mode="json")
            for row in result.candidates
            if row.actionability == actionability
        ]
        return pd.DataFrame(rows)

    for title, actionability in (
        ("지금 매매 가능", "actionable"),
        ("지켜보기", "watch_only"),
        ("거래 금지", "blocked"),
    ):
        frame = _plan_frame(actionability)
        st.markdown(f"**{title}**")
        if frame.empty:
            st.info("없음")
            continue
        preferred = [
            "symbol",
            "bucket",
            "entry_reference",
            "stop",
            "risk_per_share",
            "suggested_size",
            "max_dollar_risk",
            "spread_pct",
            "data_age_seconds",
            "reasons",
            "blockers",
        ]
        available = [column for column in preferred if column in frame.columns]
        st.dataframe(_prepare_display_frame(frame[available]), use_container_width=True, hide_index=True)


def _render_social(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("소셜 신호가 없습니다. 먼저 `psradar analyze-social`을 실행하세요.")
        return

    frame = frame.copy()
    frame["social_score"] = _numeric_series(frame, "social_score")
    frame["mention_velocity"] = _numeric_series(frame, "mention_velocity")
    frame["cross_platform_count"] = _numeric_series(frame, "cross_platform_count")
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

    display = _prepare_display_frame(filtered)
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


def main() -> None:
    st.set_page_config(
        page_title="Penny Stock Radar",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()

    settings = get_settings()
    database_path, live_enabled, refresh_seconds, live_limit = _sidebar_controls(settings.db_path)
    overview = load_run_overview(database_path)
    scan_meta = load_scan_selection_metadata(database_path)

    st.markdown(
        """
        <div class="hero">
            <h1>Penny Stock Radar</h1>
            <p>프리장 전 후보 선별, 프리장 중 품질 판정, 정규장 판단 흐름을 한눈에 보는 읽기 전용 대시보드입니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    _overview_cards(overview)
    _render_scan_status(scan_meta)

    universe = _table_or_empty(load_universe(database_path), ["symbol"])
    watchlist = _table_or_empty(load_watchlist(database_path), ["symbol"])
    premarket = _table_or_empty(load_premarket(database_path), ["symbol"])
    live_premarket = _table_or_empty(load_market_activity(database_path, "premarket"), ["symbol"])
    premarket_outcomes = _table_or_empty(
        load_prediction_outcomes(database_path, "premarket"), ["symbol"]
    )
    session = _table_or_empty(load_session_decisions(database_path), ["symbol"])
    live_regular = _table_or_empty(load_market_activity(database_path, "regular"), ["symbol"])
    regular_outcomes = _table_or_empty(
        load_prediction_outcomes(database_path, "regular"), ["symbol"]
    )
    replay = _table_or_empty(load_replay_report(database_path), ["label"])
    social = _table_or_empty(load_social(database_path), ["symbol"])
    paper_run = _table_or_empty(load_latest_paper_run(database_path), ["run_id"])
    paper_strategy_runs = _table_or_empty(load_paper_strategy_runs(database_path), ["strategy_name"])
    paper_positions = _table_or_empty(load_paper_positions(database_path), ["symbol"])
    paper_orders = _table_or_empty(load_paper_orders(database_path), ["order_id"])
    paper_snapshots = _table_or_empty(load_paper_snapshots(database_path), ["run_id"])
    paper_backtest_kpis = _table_or_empty(load_paper_backtest_kpis(settings.paper_trade_dir), ["strategy_name"])
    paper_regime_split = _table_or_empty(load_paper_regime_split(settings.paper_trade_dir), ["strategy_name"])
    paper_predictor_kpis = _table_or_empty(load_paper_predictor_kpis(settings.paper_trade_dir), ["strategy_name"])
    paper_execution_quality = _table_or_empty(load_paper_execution_quality(settings.paper_trade_dir), ["strategy_name"])

    tabs = st.tabs(
        [
            "종합 상황판",
            "전체 후보군",
            "프리장 전 분석",
            "프리장 중 분석",
            "정규장 분석",
            "모의투자",
            "실시간 모드",
            "리플레이",
            "소셜 분석",
        ]
    )

    with tabs[0]:
        _render_command_center(
            universe,
            watchlist,
            premarket,
            live_premarket,
            session,
            live_regular,
            social,
            replay,
            paper_run,
            paper_positions,
        )
    with tabs[1]:
        _render_universe(universe)
    with tabs[2]:
        _render_watchlist(watchlist)
    with tabs[3]:
        _render_premarket(premarket, live_premarket, premarket_outcomes, scan_meta)
    with tabs[4]:
        _render_session(session, live_regular, regular_outcomes, scan_meta)
    with tabs[5]:
        _render_paper_trading(
            paper_run,
            paper_strategy_runs,
            paper_positions,
            paper_orders,
            paper_snapshots,
            paper_backtest_kpis,
            paper_regime_split,
            paper_predictor_kpis,
            paper_execution_quality,
        )
    with tabs[6]:
        _render_live_mode(
            settings,
            universe,
            watchlist,
            live_enabled,
            refresh_seconds,
            live_limit,
        )
    with tabs[7]:
        _render_report(replay, premarket, session)
    with tabs[8]:
        _render_social(social)


if __name__ == "__main__":
    main()
