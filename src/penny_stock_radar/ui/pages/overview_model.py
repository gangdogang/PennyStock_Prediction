from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..formatting import metric_number, translate_label
from .activity import best_live_rank, live_momentum_score
from .shared import numeric_series

LEADERBOARD_DISPLAY_COLUMNS = [
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

LEADERBOARD_DISPLAY_LABELS = {
    "live_momentum_score": "live",
    "live_best_rank": "live_rank",
    "live_analysis_label": "live_call",
    "watchlist_score": "setup",
    "quality_score": "pm_quality",
    "social_velocity_score": "social",
    "premarket_rvol": "pm_rvol",
    "dollar_volume": "pm_dv",
}


@dataclass(frozen=True)
class OverviewMetricSet:
    headline: list[tuple[str, str | int]]
    paper: list[tuple[str, str | int]]


def prepare_leaderboard(
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
            lambda row: best_live_rank(row.get("pct_rank"), row.get("volume_rank")),
            axis=1,
        )
        subset["live_momentum_score"] = subset.apply(
            lambda row: live_momentum_score(
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
    leaderboard["watchlist_score"] = numeric_series(leaderboard, "watchlist_score")
    leaderboard["quality_score"] = numeric_series(leaderboard, "quality_score")
    leaderboard["social_velocity_score"] = numeric_series(leaderboard, "social_velocity_score")
    leaderboard["live_momentum_score"] = numeric_series(leaderboard, "live_momentum_score")
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


def build_overview_metrics(
    leaderboard: pd.DataFrame,
    session: pd.DataFrame,
    replay: pd.DataFrame,
    paper_run: pd.DataFrame,
    paper_positions: pd.DataFrame,
) -> OverviewMetricSet:
    if leaderboard.empty:
        return OverviewMetricSet(headline=[], paper=[])

    top_symbol = str(leaderboard.iloc[0]["symbol"])
    top_score = float(leaderboard.iloc[0]["radar_score"])
    enter_count = int((session["decision"] == "ENTER").sum()) if not session.empty else 0
    report_label = translate_label(replay.iloc[0]["label"]) if not replay.empty else "-"
    headline = [
        ("최상위 종목", top_symbol),
        ("레이더 점수", f"{top_score:.2f}"),
        ("진입 신호 수", enter_count),
        ("리플레이 라벨", report_label),
    ]

    paper: list[tuple[str, str | int]] = []
    if not paper_run.empty:
        row = paper_run.iloc[0]
        open_count = (
            int((paper_positions["status"] == "OPEN").sum())
            if not paper_positions.empty and "status" in paper_positions.columns
            else 0
        )
        paper = [
            ("모의계좌", translate_label(row.get("status"))),
            ("에쿼티", metric_number(row.get("equity"), 2)),
            ("누적 실현손익", metric_number(row.get("realized_pnl"), 2)),
            ("보유 포지션", open_count),
            ("손익비", metric_number(row.get("reward_risk_ratio"), 2)),
        ]

    return OverviewMetricSet(headline=headline, paper=paper)


def build_leaderboard_display_frame(leaderboard: pd.DataFrame) -> pd.DataFrame:
    available = [column for column in LEADERBOARD_DISPLAY_COLUMNS if column in leaderboard.columns]
    if not available:
        return pd.DataFrame()
    return leaderboard[available].copy().rename(columns=LEADERBOARD_DISPLAY_LABELS)
