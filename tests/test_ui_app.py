from __future__ import annotations

import pandas as pd

from penny_stock_radar.ui.pages.overview_model import (
    build_leaderboard_display_frame,
    build_overview_metrics,
)


def test_prepare_leaderboard_fills_missing_numeric_columns() -> None:
    module = __import__("penny_stock_radar.ui.app", fromlist=["_prepare_leaderboard"])

    watchlist = pd.DataFrame(
        [
            {"symbol": "AAA", "total_score": 4.0},
            {"symbol": "BBB", "total_score": 1.5},
        ]
    )
    premarket = pd.DataFrame(
        [
            {"symbol": "AAA", "premarket_rvol": 8.2},
        ]
    )
    session = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "ENTER"},
        ]
    )
    social = pd.DataFrame(columns=["symbol"])

    leaderboard = module._prepare_leaderboard(watchlist, premarket, session, social)

    assert list(leaderboard["symbol"]) == ["AAA", "BBB"]
    assert list(leaderboard["quality_score"]) == [0.0, 0.0]
    assert list(leaderboard["social_velocity_score"]) == [0.0, 0.0]
    assert leaderboard.iloc[0]["radar_score"] == 7.0
    assert leaderboard.iloc[1]["radar_score"] == 1.5


def test_prepare_leaderboard_boosts_live_leader_over_stale_social_pick() -> None:
    module = __import__("penny_stock_radar.ui.app", fromlist=["_prepare_leaderboard"])

    watchlist = pd.DataFrame(
        [
            {"symbol": "AARD", "total_score": 6.75},
            {"symbol": "AGL", "total_score": 4.75},
        ]
    )
    premarket = pd.DataFrame(columns=["symbol"])
    session = pd.DataFrame(columns=["symbol"])
    social = pd.DataFrame(
        [
            {"symbol": "AARD", "social_score": 4.0},
        ]
    )
    live_premarket = pd.DataFrame(
        [
            {
                "symbol": "AGL",
                "pct_rank": 1,
                "volume_rank": 23,
                "analysis_label": "OPENING_RANGE_CANDIDATE",
                "behavioral_score": 74.0,
                "trap_score": 10.0,
            }
        ]
    )

    leaderboard = module._prepare_leaderboard(
        watchlist,
        premarket,
        session,
        social,
        live_premarket=live_premarket,
    )

    assert leaderboard.iloc[0]["symbol"] == "AGL"
    assert leaderboard.iloc[0]["live_momentum_score"] > leaderboard.iloc[1]["live_momentum_score"]


def test_overview_metrics_keep_headline_and_paper_shape() -> None:
    leaderboard = pd.DataFrame([{"symbol": "AAA", "radar_score": 7.25}])
    session = pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "ENTER"},
            {"symbol": "BBB", "decision": "WATCH"},
        ]
    )
    replay = pd.DataFrame([{"label": "continuation"}])
    paper_run = pd.DataFrame(
        [
            {
                "status": "ACTIVE",
                "equity": 10123.456,
                "realized_pnl": 123.4,
                "reward_risk_ratio": 1.75,
            }
        ]
    )
    paper_positions = pd.DataFrame(
        [
            {"symbol": "AAA", "status": "OPEN"},
            {"symbol": "BBB", "status": "CLOSED"},
        ]
    )

    metrics = build_overview_metrics(
        leaderboard,
        session,
        replay,
        paper_run,
        paper_positions,
    )

    assert metrics.headline == [
        ("최상위 종목", "AAA"),
        ("레이더 점수", "7.25"),
        ("진입 신호 수", 1),
        ("리플레이 라벨", "지속 상승형"),
    ]
    assert metrics.paper == [
        ("모의계좌", "운용 중"),
        ("에쿼티", "10123.46"),
        ("누적 실현손익", "123.40"),
        ("보유 포지션", 1),
        ("손익비", "1.75"),
    ]


def test_leaderboard_display_frame_keeps_first_screen_columns() -> None:
    leaderboard = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "radar_score": 7.25,
                "live_momentum_score": 4.0,
                "live_best_rank": 1,
                "live_analysis_label": "OPENING_RANGE_CANDIDATE",
                "watchlist_score": 3.0,
                "quality_score": 2.0,
                "social_velocity_score": 1.0,
                "decision": "ENTER",
                "premarket_rvol": 5.0,
                "dollar_volume": 1000000.0,
                "unused": "ignored",
            }
        ]
    )

    display = build_leaderboard_display_frame(leaderboard)

    assert list(display.columns) == [
        "symbol",
        "radar_score",
        "live",
        "live_rank",
        "live_call",
        "setup",
        "pm_quality",
        "social",
        "decision",
        "pm_rvol",
        "pm_dv",
    ]
