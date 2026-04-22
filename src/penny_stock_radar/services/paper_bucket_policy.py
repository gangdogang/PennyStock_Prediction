from __future__ import annotations

from dataclasses import dataclass

from .paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PAPER_BUCKET_LABELS,
    PREDICTOR_WEIGHTED_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
    WATCHLIST_MOMENTUM_BUCKET,
)


@dataclass(frozen=True, slots=True)
class PaperBucketPolicy:
    bucket: str
    canonical_bucket: str
    label: str
    uses_predictor_weight: bool
    uses_watchlist_metadata: bool
    recompute_analysis_without_watchlist: bool = False


def paper_bucket_policy(bucket: str) -> PaperBucketPolicy:
    if bucket == PREDICTOR_WEIGHTED_BUCKET:
        return PaperBucketPolicy(
            bucket=bucket,
            canonical_bucket=PREDICTOR_WEIGHTED_BUCKET,
            label=PAPER_BUCKET_LABELS[PREDICTOR_WEIGHTED_BUCKET],
            uses_predictor_weight=True,
            uses_watchlist_metadata=True,
        )
    if bucket in {MOMENTUM_ONLY_BUCKET, WATCHLIST_MOMENTUM_BUCKET}:
        return PaperBucketPolicy(
            bucket=bucket,
            canonical_bucket=WATCHLIST_MOMENTUM_BUCKET,
            label=PAPER_BUCKET_LABELS.get(bucket, PAPER_BUCKET_LABELS[WATCHLIST_MOMENTUM_BUCKET]),
            uses_predictor_weight=False,
            uses_watchlist_metadata=True,
        )
    if bucket == WATCHLIST_BLIND_MOMENTUM_BUCKET:
        return PaperBucketPolicy(
            bucket=bucket,
            canonical_bucket=WATCHLIST_BLIND_MOMENTUM_BUCKET,
            label=PAPER_BUCKET_LABELS[WATCHLIST_BLIND_MOMENTUM_BUCKET],
            uses_predictor_weight=False,
            uses_watchlist_metadata=False,
            recompute_analysis_without_watchlist=True,
        )
    return PaperBucketPolicy(
        bucket=bucket,
        canonical_bucket=bucket,
        label=PAPER_BUCKET_LABELS.get(bucket, bucket),
        uses_predictor_weight=False,
        uses_watchlist_metadata=False,
    )


def bucket_uses_predictor_weight(bucket: str) -> bool:
    return paper_bucket_policy(bucket).uses_predictor_weight


def bucket_uses_watchlist_metadata(bucket: str) -> bool:
    return paper_bucket_policy(bucket).uses_watchlist_metadata


def bucket_recomputes_analysis_without_watchlist(bucket: str) -> bool:
    return paper_bucket_policy(bucket).recompute_analysis_without_watchlist
