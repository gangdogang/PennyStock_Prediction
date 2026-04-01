from __future__ import annotations

import pandas as pd


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
