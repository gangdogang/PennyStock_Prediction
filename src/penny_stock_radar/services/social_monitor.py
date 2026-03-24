from __future__ import annotations

import pandas as pd

from ..models import SocialSignal


class SocialMonitor:
    def analyze(self, mentions: pd.DataFrame) -> list[SocialSignal]:
        if mentions.empty:
            return []

        signals: list[SocialSignal] = []
        for symbol, frame in mentions.groupby("symbol"):
            frame = frame.sort_values("timestamp")
            duration_hours = max(
                (frame["timestamp"].iloc[-1] - frame["timestamp"].iloc[0]).total_seconds() / 3600,
                1 / 60,
            )
            mention_count = int(len(frame))
            unique_authors = int(frame["author"].nunique()) if "author" in frame.columns else mention_count
            cross_platform_count = int(frame["platform"].nunique()) if "platform" in frame.columns else 1
            engagement_total = float(frame["engagement"].sum()) if "engagement" in frame.columns else 0.0
            mention_velocity = mention_count / duration_hours

            reasons: list[str] = []
            score = 0.0
            if mention_velocity >= 5:
                score += 1.5
                reasons.append("mention_velocity")
            if unique_authors >= 3:
                score += 1.0
                reasons.append("unique_authors")
            if cross_platform_count >= 2:
                score += 1.0
                reasons.append("cross_platform_sync")
            if engagement_total >= 20:
                score += 0.5
                reasons.append("engagement")

            signals.append(
                SocialSignal(
                    symbol=symbol,
                    mention_count=mention_count,
                    mention_velocity=mention_velocity,
                    unique_authors=unique_authors,
                    cross_platform_count=cross_platform_count,
                    engagement_total=engagement_total,
                    social_score=score,
                    reasons=reasons,
                )
            )

        signals.sort(key=lambda item: (-item.social_score, item.symbol))
        return signals
