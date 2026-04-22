from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ...services.live_monitor import LiveMonitor
from ...services.market_activity import MarketActivityScanner
from ...services.trade_plan import TradePlanService
from ..formatting import prepare_display_frame
from .activity import render_live_activity_panel


def render_live_mode(
    settings: Any,
    universe: pd.DataFrame,
    watchlist: pd.DataFrame,
    enabled: bool,
    refresh_seconds: int,
    symbol_limit: int,
    *,
    project_root: Path,
) -> None:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown("<h3 class='section-title'>실시간 모니터</h3>", unsafe_allow_html=True)
    st.markdown(
        "<div class='small-note'>설정된 live provider(KIS)에서 최신 trade, quote, snapshot을 주기적으로 다시 조회합니다. `시장 데이터 시각`은 마지막 실제 체결/스냅샷 시각이라 거래가 없으면 고정될 수 있습니다.</div>",
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
                ]
            )
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    if enabled:
        st.success(f"{refresh_seconds}초마다 자동 새로고침 중입니다.")

        @st.fragment(run_every=f"{refresh_seconds}s")
        def _live_fragment() -> None:
            render_live_market_workspace(
                monitor,
                scanner,
                symbols,
                scan_limit=scan_limit,
                symbol_limit=symbol_limit,
                project_root=project_root,
            )

        _live_fragment()
    else:
        st.info("자동 새로고침이 꺼져 있습니다. 사이드바에서 실시간 자동 새로고침을 켜면 주기적으로 갱신됩니다.")
        render_live_market_workspace(
            monitor,
            scanner,
            symbols,
            scan_limit=scan_limit,
            symbol_limit=symbol_limit,
            project_root=project_root,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def render_live_market_workspace(
    monitor: LiveMonitor,
    scanner: MarketActivityScanner,
    symbols: list[str],
    *,
    scan_limit: int,
    symbol_limit: int,
    project_root: Path,
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
        csv_path = project_root / "sample_outputs" / f"{phase}_prediction_vs_actual.csv"
        try:
            result = scanner.scan(
                phase=phase,
                scan_limit=scan_limit,
                top_limit=max(symbol_limit, 10),
                comparison_csv=csv_path,
            )
            live_frame = pd.DataFrame([row.model_dump(mode="json") for row in result.activity])
            outcome_frame = pd.DataFrame([row.model_dump(mode="json") for row in result.outcomes])
            render_live_activity_panel(
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
            render_trade_plan_panel(trade_plan)
            st.caption(f"비교 CSV: {result.comparison_csv}")
        except RuntimeError as exc:
            st.warning(f"실시간 시장 스캔을 저장하지 못했습니다: {exc}")
    else:
        st.info("지금은 프리장/정규장이 아니어서 실시간 movers 랭킹 저장을 건너뜁니다.")

    if symbols:
        st.divider()
        st.markdown("**관심종목 실시간 스냅샷**")
        render_live_snapshot_table(monitor, symbols, symbol_limit)


def render_live_snapshot_table(
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

    st.dataframe(prepare_display_frame(frame), use_container_width=True, hide_index=True)


def render_trade_plan_panel(result: Any) -> None:
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
        st.dataframe(prepare_display_frame(frame[available]), use_container_width=True, hide_index=True)
