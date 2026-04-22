from __future__ import annotations

from collections.abc import Iterable

from ..models import MarketActivity
from .market_activity import MarketActivityScanner
from .paper_bucket_policy import paper_bucket_policy


def sanitize_activity_for_bucket(
    scanner: MarketActivityScanner,
    bucket: str,
    activity: Iterable[MarketActivity],
) -> list[MarketActivity]:
    policy = paper_bucket_policy(bucket)
    return [
        _sanitize_watchlist_blind(scanner, row)
        if policy.recompute_analysis_without_watchlist
        else row.model_copy(deep=True)
        for row in activity
    ]


def _sanitize_watchlist_blind(
    scanner: MarketActivityScanner,
    row: MarketActivity,
) -> MarketActivity:
    analysis_label, analysis_score, analysis_reasons = scanner._analysis(
        market_phase=row.market_phase,
        pct_change=row.pct_change,
        dollar_volume=row.dollar_volume,
        spread_pct=row.spread_pct,
        predicted=False,
        watchlist_score=None,
    )
    reasons = _dedupe(
        [
            *analysis_reasons,
            *[
                reason
                for reason in row.reasons
                if not _is_predictor_or_watchlist_reason(reason)
                and reason not in analysis_reasons
            ],
        ]
    )
    return row.model_copy(
        deep=True,
        update={
            "predicted": False,
            "watchlist_rank": None,
            "watchlist_score": None,
            "analysis_label": analysis_label,
            "analysis_score": analysis_score,
            "reasons": reasons,
        },
    )


def _is_predictor_or_watchlist_reason(reason: str) -> bool:
    normalized = reason.strip().lower()
    return "predict" in normalized or "watchlist" in normalized


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
