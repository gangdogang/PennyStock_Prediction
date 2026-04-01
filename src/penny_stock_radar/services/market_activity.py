from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    fetch_latest_passed_universe,
    fetch_recent_market_activity,
    fetch_latest_scan_id,
    fetch_latest_watchlist,
    insert_market_activity,
    insert_prediction_outcomes,
)
from ..models import MarketActivity, PredictionOutcome
from ..providers.live_market import LiveMover, LiveSnapshot, build_live_market_provider

EASTERN = ZoneInfo("America/New_York")


@dataclass(slots=True)
class MarketActivityScanResult:
    scan_id: str
    market_phase: str
    scanned_symbol_count: int
    activity: list[MarketActivity]
    outcomes: list[PredictionOutcome]
    comparison_csv: Path | None = None


class MarketActivityScanner:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def scan(
        self,
        *,
        phase: str = "auto",
        scan_limit: int | None = None,
        top_limit: int = 10,
        persist: bool = True,
        comparison_csv: Path | None = None,
    ) -> MarketActivityScanResult:
        scan_row = fetch_latest_scan_id(self.settings.database_path)
        if scan_row is None:
            raise RuntimeError("No scan run found. Build the universe/watchlist first.")

        scan_id = str(scan_row["scan_id"])
        market_phase = self.resolve_market_phase(phase)
        if market_phase not in {"premarket", "regular"}:
            raise RuntimeError("Live market ranking is only available during premarket or regular hours.")

        watchlist_rows = fetch_latest_watchlist(
            self.settings.database_path,
            limit=200,
            prefer_reportable=False,
        )
        passed_universe_rows = fetch_latest_passed_universe(
            self.settings.database_path,
            prefer_reportable=False,
        )
        provider = build_live_market_provider(self.settings)
        if not provider.is_available():
            reason = getattr(provider, "reason", "No live market data provider is configured.")
            self._close_provider(provider)
            raise RuntimeError(reason)

        watchlist_rank = {
            str(row["symbol"]): index + 1 for index, row in enumerate(watchlist_rows)
        }
        watchlist_score = {
            str(row["symbol"]): float(row["total_score"])
            for row in watchlist_rows
            if row["total_score"] is not None
        }

        activity: list[MarketActivity] = []
        now = datetime.now(timezone.utc)
        screener_symbols, screener_gainers, screener_actives = self._build_screener_symbol_list(
            provider,
            scan_limit=scan_limit,
        )
        fallback_symbols = self._build_symbol_list(
            watchlist_rows,
            passed_universe_rows,
            scan_limit=scan_limit,
        )
        symbols = screener_symbols or fallback_symbols
        if not symbols:
            self._close_provider(provider)
            raise RuntimeError("No live scan symbols found. Build the universe/watchlist first.")

        try:
            for symbol in symbols:
                snapshot = provider.latest_snapshot(symbol)
                gainers_row = screener_gainers.get(symbol)
                active_row = screener_actives.get(symbol)
                if snapshot is None and gainers_row is None and active_row is None:
                    continue
                row = self._snapshot_to_activity(
                    snapshot,
                    market_phase=market_phase,
                    now=now,
                    watchlist_rank=watchlist_rank.get(symbol),
                    watchlist_score=watchlist_score.get(symbol),
                    gainers_row=gainers_row,
                    active_row=active_row,
                )
                activity.append(row)
        finally:
            self._close_provider(provider)

        if not activity:
            raise RuntimeError("The configured live provider returned no market snapshots for the scan universe.")

        self._apply_ranks(activity)
        self._apply_behavioral_scores(activity, market_phase=market_phase)
        outcomes = self._build_prediction_outcomes(
            activity,
            market_phase=market_phase,
            watchlist_rank=watchlist_rank,
            watchlist_score=watchlist_score,
            top_limit=top_limit,
        )

        if persist:
            insert_market_activity(self.settings.database_path, scan_id, market_phase, activity)
            insert_prediction_outcomes(self.settings.database_path, scan_id, market_phase, outcomes)

        if comparison_csv is not None:
            self.export_outcomes_csv(outcomes, comparison_csv)

        return MarketActivityScanResult(
            scan_id=scan_id,
            market_phase=market_phase,
            scanned_symbol_count=len(symbols),
            activity=activity,
            outcomes=outcomes,
            comparison_csv=comparison_csv.resolve() if comparison_csv is not None else None,
        )

    def resolve_market_phase(self, phase: str = "auto") -> str:
        normalized = (phase or "auto").strip().lower()
        if normalized in {"premarket", "regular", "afterhours", "closed"}:
            return normalized
        current = datetime.now(EASTERN)
        if current.weekday() >= 5:
            return "closed"
        session_time = current.timetz().replace(tzinfo=None)
        if time(4, 0) <= session_time < time(9, 30):
            return "premarket"
        if time(9, 30) <= session_time < time(16, 0):
            return "regular"
        if time(16, 0) <= session_time < time(20, 0):
            return "afterhours"
        return "closed"

    def export_outcomes_csv(
        self,
        outcomes: list[PredictionOutcome],
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol",
            "market_phase",
            "predicted",
            "watchlist_rank",
            "watchlist_score",
            "pct_rank",
            "volume_rank",
            "pct_change",
            "volume",
            "dollar_volume",
            "analysis_label",
            "outcome",
            "reasons",
            "created_at",
        ]
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in outcomes:
                writer.writerow(
                    {
                        "symbol": row.symbol,
                        "market_phase": row.market_phase,
                        "predicted": int(row.predicted),
                        "watchlist_rank": row.watchlist_rank,
                        "watchlist_score": row.watchlist_score,
                        "pct_rank": row.pct_rank,
                        "volume_rank": row.volume_rank,
                        "pct_change": row.pct_change,
                        "volume": row.volume,
                        "dollar_volume": row.dollar_volume,
                        "analysis_label": row.analysis_label,
                        "outcome": row.outcome,
                        "reasons": ", ".join(row.reasons),
                        "created_at": row.created_at.isoformat(),
                    }
                )
        return output_path.resolve()

    def _build_symbol_list(
        self,
        watchlist_rows: list[object],
        passed_universe_rows: list[object],
        *,
        scan_limit: int | None,
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for row_group in (watchlist_rows, passed_universe_rows):
            for row in row_group:
                symbol = str(row["symbol"]).upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                ordered.append(symbol)
        if scan_limit is None:
            scan_limit = min(max(len(ordered), 1), 60)
        return ordered[: max(int(scan_limit), 1)]

    def _snapshot_to_activity(
        self,
        snapshot: LiveSnapshot | None,
        *,
        market_phase: str,
        now: datetime,
        watchlist_rank: int | None,
        watchlist_score: float | None,
        gainers_row: LiveMover | None,
        active_row: LiveMover | None,
    ) -> MarketActivity:
        symbol = ""
        source = "market"
        if snapshot is not None:
            symbol = snapshot.symbol
            source = snapshot.source
        elif gainers_row is not None:
            symbol = gainers_row.symbol
            source = gainers_row.source
        elif active_row is not None:
            symbol = active_row.symbol
            source = active_row.source

        last_price = (
            gainers_row.price
            if gainers_row and gainers_row.price is not None
            else self._last_price(snapshot)
        )
        previous_close = self._previous_close(snapshot)
        pct_change = (
            gainers_row.pct_change
            if gainers_row and gainers_row.pct_change is not None
            else self._pct_change(last_price, previous_close)
        )
        volume = (
            active_row.volume
            if active_row and active_row.volume is not None
            else self._volume(snapshot)
        )
        dollar_volume = (last_price or 0.0) * volume if last_price is not None and volume else None
        spread_pct = self._spread_pct(snapshot)

        if not self._is_scannable_symbol(symbol, price=last_price):
            return MarketActivity(
                symbol=symbol,
                market_phase=market_phase,
                source=source,
                last_price=last_price,
                previous_close=previous_close,
                pct_change=pct_change,
                volume=volume,
                dollar_volume=dollar_volume,
                trade_size=snapshot.latest_trade.size if snapshot and snapshot.latest_trade else None,
                spread_pct=spread_pct,
                market_status=snapshot.market_status if snapshot else None,
                market_data_at=(
                    snapshot.updated_at or (snapshot.latest_trade.timestamp if snapshot and snapshot.latest_trade else None)
                )
                if snapshot
                else gainers_row.updated_at if gainers_row else active_row.updated_at if active_row else None,
                pct_rank=gainers_row.rank if gainers_row else 0,
                volume_rank=active_row.rank if active_row else 0,
                watchlist_rank=watchlist_rank,
                watchlist_score=watchlist_score,
                predicted=watchlist_rank is not None,
                analysis_label="SKIP",
                analysis_score=-999.0,
                reasons=["outside_market_scope"],
                created_at=now,
            )

        analysis_label, analysis_score, reasons = self._analysis(
            market_phase=market_phase,
            pct_change=pct_change,
            dollar_volume=dollar_volume,
            spread_pct=spread_pct,
            predicted=watchlist_rank is not None,
            watchlist_score=watchlist_score,
        )
        return MarketActivity(
            symbol=symbol,
            market_phase=market_phase,
            source=source,
            last_price=last_price,
            previous_close=previous_close,
            pct_change=pct_change,
            volume=volume,
            dollar_volume=dollar_volume,
            trade_size=snapshot.latest_trade.size if snapshot and snapshot.latest_trade else None,
            spread_pct=spread_pct,
            market_status=snapshot.market_status if snapshot else None,
            market_data_at=(
                snapshot.updated_at or (snapshot.latest_trade.timestamp if snapshot and snapshot.latest_trade else None)
            )
            if snapshot
            else gainers_row.updated_at if gainers_row else active_row.updated_at if active_row else None,
            pct_rank=gainers_row.rank if gainers_row else 0,
            volume_rank=active_row.rank if active_row else 0,
            watchlist_rank=watchlist_rank,
            watchlist_score=watchlist_score,
            predicted=watchlist_rank is not None,
            analysis_label=analysis_label,
            analysis_score=analysis_score,
            reasons=reasons,
            created_at=now,
        )

    def _apply_ranks(self, rows: list[MarketActivity]) -> None:
        rows[:] = [row for row in rows if row.analysis_label != "SKIP"]
        if not rows:
            return

        pct_sorted = sorted(
            rows,
            key=lambda row: (
                row.pct_rank if row.pct_rank > 0 else 9999,
                -(row.pct_change if row.pct_change is not None else float("-inf")),
                row.symbol,
            ),
        )
        for index, row in enumerate(pct_sorted, start=1):
            if row.pct_rank <= 0:
                row.pct_rank = index

        volume_sorted = sorted(
            rows,
            key=lambda row: (
                row.volume_rank if row.volume_rank > 0 else 9999,
                -(row.volume if row.volume is not None else float("-inf")),
                -(row.dollar_volume if row.dollar_volume is not None else float("-inf")),
                row.symbol,
            ),
        )
        for index, row in enumerate(volume_sorted, start=1):
            if row.volume_rank <= 0:
                row.volume_rank = index

    def _apply_behavioral_scores(
        self,
        rows: list[MarketActivity],
        *,
        market_phase: str,
    ) -> None:
        if not rows:
            return
        history_rows = fetch_recent_market_activity(
            self.settings.database_path,
            limit=max(200, len(rows) * 20),
            market_phases=(market_phase,),
            symbols=tuple(sorted({row.symbol for row in rows})),
        )
        history_by_symbol: dict[str, list[object]] = defaultdict(list)
        for history_row in history_rows:
            history_by_symbol[str(history_row["symbol"]).upper()].append(history_row)

        for row in rows:
            leader, absorption, trap, behavioral, behavior_reasons = self._behavioral_profile(
                row=row,
                history_rows=history_by_symbol.get(row.symbol, []),
            )
            row.leader_persistence_score = leader
            row.pullback_absorption_score = absorption
            row.trap_score = trap
            row.behavioral_score = behavioral
            row.reasons = self._merge_reasons(row.reasons, behavior_reasons)

            adjusted_score = row.analysis_score
            adjusted_score += (leader / 100.0) * 0.95
            adjusted_score += (absorption / 100.0) * 0.75
            adjusted_score += (behavioral / 100.0) * 0.65
            adjusted_score -= (trap / 100.0) * 1.15
            row.analysis_score = round(adjusted_score, 3)

            reclaim_ready = "leader_reclaim" in behavior_reasons and trap < 55.0
            if (
                row.analysis_label == "WAIT_PULLBACK"
                and reclaim_ready
                and behavioral >= 62.0
                and self._best_rank(row.pct_rank, row.volume_rank) <= 5
            ):
                row.analysis_label = (
                    "OPENING_RANGE_CANDIDATE"
                    if market_phase == "premarket"
                    else "CONDITIONAL_ENTRY"
                )
                row.reasons = self._merge_reasons(row.reasons, ["reclaim_entry_ready"])
            elif row.analysis_label in {"OPENING_RANGE_CANDIDATE", "CONDITIONAL_ENTRY", "NEWS_CHECK_FIRST"}:
                if trap >= 68.0:
                    row.analysis_label = "NO_CHASE"
                    row.reasons = self._merge_reasons(row.reasons, ["trap_warning"])

    def _behavioral_profile(
        self,
        *,
        row: MarketActivity,
        history_rows: list[object],
    ) -> tuple[float, float, float, float, list[str]]:
        current_best_rank = self._best_rank(row.pct_rank, row.volume_rank)
        samples = [
            {
                "pct_rank": row.pct_rank,
                "volume_rank": row.volume_rank,
                "last_price": row.last_price,
                "spread_pct": row.spread_pct,
                "dollar_volume": row.dollar_volume,
                "pct_change": row.pct_change,
            }
        ]
        for history_row in history_rows[:5]:
            samples.append(
                {
                    "pct_rank": int(history_row["pct_rank"] or 0),
                    "volume_rank": int(history_row["volume_rank"] or 0),
                    "last_price": float(history_row["last_price"]) if history_row["last_price"] is not None else None,
                    "spread_pct": float(history_row["spread_pct"]) if history_row["spread_pct"] is not None else None,
                    "dollar_volume": float(history_row["dollar_volume"]) if history_row["dollar_volume"] is not None else None,
                    "pct_change": float(history_row["pct_change"]) if history_row["pct_change"] is not None else None,
                }
            )

        rank_points = 0.0
        rank_weight = 0.0
        top5_streak = 0
        streak_open = True
        recent_best_ranks: list[int] = []
        recent_prices: list[float] = []
        recent_spreads: list[float] = []
        recent_dollar_volume: list[float] = []
        recent_pct_change: list[float] = []

        for index, sample in enumerate(samples):
            best_rank = self._best_rank(sample["pct_rank"], sample["volume_rank"])
            if index > 0:
                recent_best_ranks.append(best_rank)
                if sample["last_price"] is not None:
                    recent_prices.append(float(sample["last_price"]))
                if sample["spread_pct"] is not None:
                    recent_spreads.append(float(sample["spread_pct"]))
                if sample["dollar_volume"] is not None:
                    recent_dollar_volume.append(float(sample["dollar_volume"]))
                if sample["pct_change"] is not None:
                    recent_pct_change.append(float(sample["pct_change"]))

            weight = max(1.0 - (index * 0.16), 0.24)
            strength = max(0.0, (6.0 - min(best_rank, 6)) / 5.0)
            rank_points += strength * weight
            rank_weight += weight

            if streak_open and best_rank <= 5:
                top5_streak += 1
            else:
                streak_open = False

        leader_score = 0.0
        if rank_weight > 0:
            leader_score = min(100.0, (rank_points / rank_weight) * 100.0 + (top5_streak * 6.0))

        recent_rank_average = (
            sum(recent_best_ranks) / len(recent_best_ranks)
            if recent_best_ranks
            else float(current_best_rank)
        )
        rank_reclaim = max(recent_rank_average - float(current_best_rank), 0.0)

        price_candidates = [value for value in [row.last_price, *recent_prices] if value is not None and value > 0]
        peak_price = max(price_candidates) if price_candidates else None
        drawdown_from_peak = 0.0
        if peak_price is not None and row.last_price is not None and peak_price > 0:
            drawdown_from_peak = max((peak_price - row.last_price) / peak_price, 0.0)

        recent_spread_average = (
            sum(recent_spreads) / len(recent_spreads)
            if recent_spreads
            else row.spread_pct or 0.0
        )
        spread_ratio = (
            (row.spread_pct or 0.0) / recent_spread_average
            if recent_spread_average and row.spread_pct is not None
            else 1.0
        )
        recent_dollar_average = (
            sum(recent_dollar_volume) / len(recent_dollar_volume)
            if recent_dollar_volume
            else row.dollar_volume or 0.0
        )
        volume_ratio = (
            (row.dollar_volume or 0.0) / recent_dollar_average
            if recent_dollar_average and row.dollar_volume is not None
            else 1.0
        )
        recent_pct_peak = max(recent_pct_change) if recent_pct_change else row.pct_change or 0.0
        pct_fade = max((recent_pct_peak - (row.pct_change or 0.0)), 0.0)
        reclaim_ready = rank_reclaim >= 1.5 and current_best_rank <= 5 and drawdown_from_peak <= 0.03

        trap_score = 0.0
        if (row.pct_change or 0.0) >= 20.0:
            trap_score += 10.0
        if current_best_rank > 5:
            trap_score += min((current_best_rank - 5) * 8.0, 24.0)
        if spread_ratio >= 1.25:
            trap_score += min((spread_ratio - 1.25) * 80.0, 24.0)
        if drawdown_from_peak >= 0.025:
            trap_score += min(drawdown_from_peak * 350.0, 32.0)
        if pct_fade >= 8.0:
            trap_score += min((pct_fade - 8.0) * 1.5, 18.0)
        if leader_score < 40.0:
            trap_score += 10.0
        if reclaim_ready:
            trap_score -= 12.0
        trap_score = max(0.0, min(100.0, trap_score))

        pullback_absorption_score = 28.0 + (leader_score * 0.34)
        if 0.01 <= drawdown_from_peak <= 0.07:
            pullback_absorption_score += 18.0
        if current_best_rank <= 5:
            pullback_absorption_score += 10.0
        if spread_ratio <= 1.08:
            pullback_absorption_score += 10.0
        if volume_ratio >= 0.7:
            pullback_absorption_score += 10.0
        if reclaim_ready:
            pullback_absorption_score += 16.0
        if trap_score > 55.0:
            pullback_absorption_score -= (trap_score - 55.0) * 0.45
        pullback_absorption_score = max(0.0, min(100.0, pullback_absorption_score))

        behavioral_score = (
            leader_score * 0.42
            + pullback_absorption_score * 0.43
            + min(rank_reclaim, 4.0) * 6.0
            - trap_score * 0.42
            + (8.0 if reclaim_ready else 0.0)
        )
        behavioral_score = max(0.0, min(100.0, behavioral_score))

        behavior_reasons: list[str] = []
        if current_best_rank <= 5:
            behavior_reasons.append("top5_live_leader")
        if leader_score >= 65.0:
            behavior_reasons.append("leader_persistence_strong")
        elif top5_streak >= 2:
            behavior_reasons.append("top5_leader_persistence")
        if reclaim_ready:
            behavior_reasons.append("leader_reclaim")
        if pullback_absorption_score >= 60.0 and drawdown_from_peak >= 0.01:
            behavior_reasons.append("pullback_absorption")
        if spread_ratio >= 1.25:
            behavior_reasons.append("spread_expanding")
        if trap_score >= 60.0:
            behavior_reasons.append("trap_warning")
        return (
            round(leader_score, 2),
            round(pullback_absorption_score, 2),
            round(trap_score, 2),
            round(behavioral_score, 2),
            behavior_reasons,
        )

    def _best_rank(self, pct_rank: int | None, volume_rank: int | None) -> int:
        candidates = [
            int(value)
            for value in (pct_rank, volume_rank)
            if value is not None and int(value) > 0
        ]
        return min(candidates) if candidates else 9999

    def _merge_reasons(self, base: list[str], extra: list[str]) -> list[str]:
        merged: list[str] = []
        for reason in [*base, *extra]:
            if reason and reason not in merged:
                merged.append(reason)
        return merged

    def _build_screener_symbol_list(
        self,
        provider: object,
        *,
        scan_limit: int | None,
    ) -> tuple[list[str], dict[str, LiveMover], dict[str, LiveMover]]:
        gainers_fn = getattr(provider, "top_gainers", None)
        actives_fn = getattr(provider, "most_active", None)
        if not callable(gainers_fn) or not callable(actives_fn):
            return [], {}, {}

        limit = max(int(scan_limit or 20), 10)
        gainers_rows = [
            row for row in gainers_fn(limit=limit) if self._is_scannable_symbol(row.symbol, row.price)
        ]
        actives_rows = list(actives_fn(limit=limit))
        gainers = {row.symbol: row for row in gainers_rows}
        actives: dict[str, LiveMover] = {}
        ordered: list[str] = []
        seen: set[str] = set()

        for row in gainers_rows:
            if row.symbol not in seen:
                seen.add(row.symbol)
                ordered.append(row.symbol)

        for row in actives_rows:
            if row.symbol in seen:
                actives[row.symbol] = row
                continue
            seen.add(row.symbol)
            ordered.append(row.symbol)
            actives[row.symbol] = row

        if scan_limit is not None:
            ordered = ordered[: max(int(scan_limit), 1)]
        actives.update({row.symbol: row for row in actives_rows if row.symbol in ordered})
        gainers = {symbol: row for symbol, row in gainers.items() if symbol in ordered}
        actives = {symbol: row for symbol, row in actives.items() if symbol in ordered}
        return ordered, gainers, actives

    def _build_prediction_outcomes(
        self,
        activity: list[MarketActivity],
        *,
        market_phase: str,
        watchlist_rank: dict[str, int],
        watchlist_score: dict[str, float],
        top_limit: int,
    ) -> list[PredictionOutcome]:
        by_symbol = {row.symbol: row for row in activity}
        selected_symbols: set[str] = set(watchlist_rank)
        for row in activity:
            if row.pct_rank <= top_limit or row.volume_rank <= top_limit:
                selected_symbols.add(row.symbol)

        outcomes: list[PredictionOutcome] = []
        created_at = datetime.now(timezone.utc)
        for symbol in sorted(selected_symbols, key=lambda value: (watchlist_rank.get(value, 9999), value)):
            row = by_symbol.get(symbol)
            predicted = symbol in watchlist_rank
            pct_rank = row.pct_rank if row is not None else None
            volume_rank = row.volume_rank if row is not None else None
            in_top_pct = pct_rank is not None and pct_rank <= top_limit
            in_top_volume = volume_rank is not None and volume_rank <= top_limit
            if predicted and in_top_pct and in_top_volume:
                outcome = "matched_both"
            elif predicted and in_top_pct:
                outcome = "matched_pct"
            elif predicted and in_top_volume:
                outcome = "matched_volume"
            elif predicted:
                outcome = "predicted_only"
            else:
                outcome = "unpredicted_leader"

            reasons: list[str] = []
            if predicted:
                reasons.append("predicted_watchlist")
            if in_top_pct:
                reasons.append("top_pct_change")
            if in_top_volume:
                reasons.append("top_volume")
            if row is not None:
                reasons.extend(row.reasons[:3])

            outcomes.append(
                PredictionOutcome(
                    symbol=symbol,
                    market_phase=market_phase,
                    predicted=predicted,
                    watchlist_rank=watchlist_rank.get(symbol),
                    watchlist_score=watchlist_score.get(symbol),
                    pct_rank=pct_rank,
                    volume_rank=volume_rank,
                    pct_change=row.pct_change if row is not None else None,
                    volume=row.volume if row is not None else None,
                    dollar_volume=row.dollar_volume if row is not None else None,
                    analysis_label=row.analysis_label if row is not None else "",
                    outcome=outcome,
                    reasons=reasons,
                    created_at=created_at,
                )
            )

        outcomes.sort(
            key=lambda row: (
                not row.predicted,
                row.watchlist_rank if row.watchlist_rank is not None else 9999,
                row.pct_rank if row.pct_rank is not None else 9999,
                row.volume_rank if row.volume_rank is not None else 9999,
                row.symbol,
            )
        )
        return outcomes

    def _analysis(
        self,
        *,
        market_phase: str,
        pct_change: float | None,
        dollar_volume: float | None,
        spread_pct: float | None,
        predicted: bool,
        watchlist_score: float | None,
    ) -> tuple[str, float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        if pct_change is not None:
            if pct_change >= 25:
                score += 2.0
                reasons.append("pct_leader")
            elif pct_change >= 10:
                score += 1.0
                reasons.append("pct_momentum")
            elif pct_change <= -5:
                score -= 1.0
                reasons.append("pct_fading")

        if dollar_volume is not None:
            if dollar_volume >= self.settings.premarket_min_dollar_volume * 3:
                score += 1.75
                reasons.append("live_dollar_volume_strong")
            elif dollar_volume >= self.settings.premarket_min_dollar_volume:
                score += 1.0
                reasons.append("live_dollar_volume_ok")

        if spread_pct is not None:
            if spread_pct <= self.settings.premarket_max_spread_pct:
                score += 0.75
                reasons.append("spread_ok")
            else:
                score -= 1.0
                reasons.append("spread_wide")

        if predicted:
            score += 1.0
            reasons.append("watchlist_predicted")
        if watchlist_score is not None and watchlist_score >= 3.0:
            score += 0.5
            reasons.append("watchlist_score_strong")

        has_context = predicted or (watchlist_score is not None and watchlist_score >= 3.0)

        if market_phase == "premarket":
            if score >= 3.5 and has_context:
                reasons.append("first_pullback_only")
                return "OPENING_RANGE_CANDIDATE", score, reasons
            if score >= 3.0 and not has_context:
                reasons.extend(["news_check_required", "context_missing"])
                return "NEWS_CHECK_FIRST", score, reasons
            if score >= 1.5:
                reasons.append("wait_for_reclaim")
                return "WAIT_PULLBACK", score, reasons
            reasons.append("no_chase")
            return "NO_CHASE", score, reasons

        if pct_change is not None and pct_change >= 35 and not predicted:
            score -= 0.75
            reasons.append("late_extension")
        if score >= 3.5 and has_context:
            reasons.append("first_pullback_only")
            return "CONDITIONAL_ENTRY", score, reasons
        if score >= 3.0 and not has_context:
            reasons.extend(["news_check_required", "context_missing"])
            return "NEWS_CHECK_FIRST", score, reasons
        if score >= 1.5:
            reasons.append("wait_for_reclaim")
            return "WAIT_PULLBACK", score, reasons
        reasons.append("no_chase")
        return "NO_CHASE", score, reasons

    def _last_price(self, snapshot: LiveSnapshot | None) -> float | None:
        if snapshot is None:
            return None
        if snapshot.latest_trade and snapshot.latest_trade.price is not None:
            return snapshot.latest_trade.price
        return self._nested_float(
            snapshot.raw,
            ("ticker", "day", "c"),
            ("day", "c"),
            ("dailyBar", "c"),
            ("minuteBar", "c"),
            ("ticker", "min", "c"),
        )

    def _previous_close(self, snapshot: LiveSnapshot | None) -> float | None:
        if snapshot is None:
            return None
        return self._nested_float(
            snapshot.raw,
            ("ticker", "prevDay", "c"),
            ("prevDay", "c"),
            ("prevDailyBar", "c"),
            ("ticker", "prevDay", "close"),
            ("prevDay", "close"),
            ("prevDailyBar", "close"),
        )

    def _volume(self, snapshot: LiveSnapshot | None) -> float | None:
        if snapshot is None:
            return None
        return self._nested_float(
            snapshot.raw,
            ("ticker", "day", "v"),
            ("day", "v"),
            ("dailyBar", "v"),
            ("ticker", "min", "v"),
            ("minuteBar", "v"),
        )

    def _spread_pct(self, snapshot: LiveSnapshot | None) -> float | None:
        if snapshot is None:
            return None
        if not snapshot.latest_quote:
            return None
        bid = snapshot.latest_quote.bid_price
        ask = snapshot.latest_quote.ask_price
        if bid is None or ask is None:
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        midpoint = (bid + ask) / 2
        if midpoint <= 0:
            return None
        return (ask - bid) / midpoint

    def _pct_change(self, last_price: float | None, previous_close: float | None) -> float | None:
        if last_price is None or previous_close is None or previous_close <= 0:
            return None
        return ((last_price - previous_close) / previous_close) * 100.0

    def _is_scannable_symbol(self, symbol: str, price: float | None) -> bool:
        if not symbol:
            return False
        if not self.settings.in_price_scope(price):
            return False
        return True

    def _nested_float(self, payload: dict[str, Any], *paths: tuple[str, ...]) -> float | None:
        for path in paths:
            current: Any = payload
            valid = True
            for part in path:
                if not isinstance(current, dict) or part not in current:
                    valid = False
                    break
                current = current[part]
            if not valid or current in {None, ""}:
                continue
            try:
                return float(current)
            except Exception:
                continue
        return None

    def _close_provider(self, provider: object) -> None:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
