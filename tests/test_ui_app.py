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
