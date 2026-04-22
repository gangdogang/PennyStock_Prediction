from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import streamlit as st

if __package__ in {None, ""}:
    SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    from penny_stock_radar.config import get_settings
    from penny_stock_radar.runtime_scripts import full_refresh_command
    from penny_stock_radar.ui import data as ui_data
    from penny_stock_radar.ui.layout import (
        inject_styles,
        render_overview_cards,
        render_scan_status,
        table_or_empty,
    )
    from penny_stock_radar.ui.pages.activity import (
        best_live_rank as _best_live_rank,
        live_momentum_score as _live_momentum_score,
        render_premarket,
        render_session,
    )
    from penny_stock_radar.ui.pages.live import render_live_mode
    from penny_stock_radar.ui.pages.overview import render_command_center
    from penny_stock_radar.ui.pages.overview_model import (
        prepare_leaderboard as _prepare_leaderboard,
    )
    from penny_stock_radar.ui.pages.paper import render_paper_trading
    from penny_stock_radar.ui.pages.replay import render_report
    from penny_stock_radar.ui.pages.screeners import (
        render_social,
        render_universe,
        render_watchlist,
    )
else:
    from ..config import get_settings
    from ..runtime_scripts import full_refresh_command
    from . import data as ui_data
    from .layout import inject_styles, render_overview_cards, render_scan_status, table_or_empty
    from .pages.activity import (
        best_live_rank as _best_live_rank,
        live_momentum_score as _live_momentum_score,
        render_premarket,
        render_session,
    )
    from .pages.live import render_live_mode
    from .pages.overview import render_command_center
    from .pages.overview_model import prepare_leaderboard as _prepare_leaderboard
    from .pages.paper import render_paper_trading
    from .pages.replay import render_report
    from .pages.screeners import render_social, render_universe, render_watchlist

_table_or_empty = table_or_empty


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


def main() -> None:
    st.set_page_config(
        page_title="Penny Stock Radar",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles()

    settings = get_settings()
    database_path, live_enabled, refresh_seconds, live_limit = _sidebar_controls(settings.db_path)
    overview = ui_data.load_run_overview(database_path)
    scan_meta = ui_data.load_scan_selection_metadata(database_path)

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
    render_overview_cards(overview)
    render_scan_status(scan_meta)

    universe = table_or_empty(ui_data.load_universe(database_path), ["symbol"])
    watchlist = table_or_empty(ui_data.load_watchlist(database_path), ["symbol"])
    premarket = table_or_empty(ui_data.load_premarket(database_path), ["symbol"])
    live_premarket = table_or_empty(ui_data.load_market_activity(database_path, "premarket"), ["symbol"])
    premarket_outcomes = table_or_empty(
        ui_data.load_prediction_outcomes(database_path, "premarket"), ["symbol"]
    )
    session = table_or_empty(ui_data.load_session_decisions(database_path), ["symbol"])
    live_regular = table_or_empty(ui_data.load_market_activity(database_path, "regular"), ["symbol"])
    regular_outcomes = table_or_empty(
        ui_data.load_prediction_outcomes(database_path, "regular"), ["symbol"]
    )
    replay = table_or_empty(ui_data.load_replay_report(database_path), ["label"])
    social = table_or_empty(ui_data.load_social(database_path), ["symbol"])
    paper_run = table_or_empty(ui_data.load_latest_paper_run(database_path), ["run_id"])
    paper_strategy_runs = table_or_empty(ui_data.load_paper_strategy_runs(database_path), ["strategy_name"])
    paper_positions = table_or_empty(ui_data.load_paper_positions(database_path), ["symbol"])
    paper_orders = table_or_empty(ui_data.load_paper_orders(database_path), ["order_id"])
    paper_snapshots = table_or_empty(ui_data.load_paper_snapshots(database_path), ["run_id"])
    paper_backtest_kpis = table_or_empty(
        ui_data.load_paper_backtest_kpis(settings.paper_trade_dir), ["strategy_name"]
    )
    paper_regime_split = table_or_empty(
        ui_data.load_paper_regime_split(settings.paper_trade_dir), ["strategy_name"]
    )
    paper_predictor_kpis = table_or_empty(
        ui_data.load_paper_predictor_kpis(settings.paper_trade_dir), ["strategy_name"]
    )
    paper_execution_quality = table_or_empty(
        ui_data.load_paper_execution_quality(settings.paper_trade_dir), ["strategy_name"]
    )

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
        render_command_center(
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
        render_universe(universe)
    with tabs[2]:
        render_watchlist(watchlist)
    with tabs[3]:
        render_premarket(premarket, live_premarket, premarket_outcomes, scan_meta)
    with tabs[4]:
        render_session(session, live_regular, regular_outcomes, scan_meta)
    with tabs[5]:
        render_paper_trading(
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
        render_live_mode(
            settings,
            universe,
            watchlist,
            live_enabled,
            refresh_seconds,
            live_limit,
            project_root=_project_root(),
        )
    with tabs[7]:
        render_report(replay, premarket, session)
    with tabs[8]:
        render_social(social)


if __name__ == "__main__":
    main()
