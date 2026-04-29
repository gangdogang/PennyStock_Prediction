from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    fetch_latest_passed_universe,
    fetch_passed_universe_for_market_date,
    init_database,
    insert_premkt_predictions,
)
from ..models import FilingMatch, PremktPrediction, WatchlistEntry
from .premkt_model_scoring import PremktModelScorer
from .watchlist_builder import WatchlistBuilder

EASTERN = ZoneInfo("America/New_York")
SCORE_MODES = {"rule", "ml", "blend"}


class PremktPredictor:
    def __init__(
        self,
        settings: AppSettings,
        *,
        watchlist_builder: WatchlistBuilder | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.watchlist_builder = watchlist_builder or WatchlistBuilder(settings)
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        *,
        limit: int | None = None,
        lookback_hours: int | None = None,
        market_date: date | str | None = None,
        filing_cutoff: datetime | None = None,
        output_path: Path | None = None,
        persist: bool = True,
        model_path: Path | None = None,
        score_mode: str = "rule",
        ml_weight: float = 0.5,
    ) -> list[PremktPrediction]:
        score_mode = score_mode.lower()
        if score_mode not in SCORE_MODES:
            raise ValueError(f"score_mode must be one of: {', '.join(sorted(SCORE_MODES))}")
        if not 0.0 <= ml_weight <= 1.0:
            raise ValueError("ml_weight must be between 0.0 and 1.0")
        init_database(self.settings.database_path)
        entries, filings, _ = self.watchlist_builder.build(
            limit=limit,
            lookback_hours=lookback_hours,
            market_date=market_date,
            filing_cutoff=filing_cutoff,
        )
        if not entries:
            return []

        scorer, scoring_notes = self._build_model_scorer(
            model_path=model_path if score_mode != "rule" else None
        )
        effective_score_mode = score_mode if scorer is not None and model_path is not None else "rule"
        lineage_model_path = model_path if score_mode != "rule" else None
        generated_at = self.now_fn()
        cutoff_at = self._prediction_cutoff_at(
            generated_at=generated_at,
            market_date=market_date,
            filing_cutoff=filing_cutoff,
        )
        predictions = self._build_predictions(
            entries=entries,
            filings=filings,
            generated_at=generated_at,
            cutoff_at=cutoff_at,
            market_date=str(market_date) if market_date is not None else None,
            scorer=scorer,
            score_mode=effective_score_mode,
            requested_score_mode=score_mode,
            ml_weight=ml_weight,
            model_path=lineage_model_path,
            scoring_notes=scoring_notes,
        )
        if output_path is not None:
            self.export_json(predictions, output_path)
        if persist:
            scan_id = self._resolve_scan_id(market_date=market_date)
            if scan_id is not None:
                insert_premkt_predictions(
                    self.settings.database_path,
                    scan_id,
                    predictions,
                )
        return predictions

    def export_json(
        self,
        predictions: list[PremktPrediction],
        output_path: Path,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [row.model_dump(mode="json") for row in predictions]
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return output_path

    def _build_predictions(
        self,
        *,
        entries: list[WatchlistEntry],
        filings: list[FilingMatch],
        generated_at: datetime,
        cutoff_at: datetime,
        market_date: str | None,
        scorer: PremktModelScorer | None,
        score_mode: str,
        requested_score_mode: str,
        ml_weight: float,
        model_path: Path | None,
        scoring_notes: list[str],
    ) -> list[PremktPrediction]:
        filings_by_symbol: dict[str, list[FilingMatch]] = defaultdict(list)
        for filing in filings:
            filings_by_symbol[filing.symbol.upper()].append(filing)

        predictions: list[PremktPrediction] = []
        for entry in entries:
            symbol = entry.symbol.upper()
            symbol_filings = filings_by_symbol.get(symbol, [])
            filing_summary = "; ".join(filing.summary for filing in symbol_filings[:2])
            rule_score = self._score_entry(entry, filing_summary=filing_summary)
            final_score = rule_score
            ml_score: float | None = None
            model_feature_version: str | None = None
            model_missing_features: list[str] = []
            notes = list(scoring_notes)
            row_score_mode = score_mode
            if scorer is not None:
                model_result = scorer.score_symbol(
                    symbol=symbol,
                    market_date=market_date,
                    cutoff_at=cutoff_at,
                )
                ml_score = model_result.ml_score
                model_feature_version = model_result.model_feature_version
                model_missing_features = model_result.missing_feature_names
                notes.extend(model_result.scoring_notes)
                if ml_score is None:
                    row_score_mode = "rule"
                    notes.append(
                        f"requested_score_mode={requested_score_mode}; fell back to rule score."
                    )
                elif score_mode == "ml":
                    final_score = ml_score
                elif score_mode == "blend":
                    final_score = ((1.0 - ml_weight) * rule_score) + (ml_weight * ml_score)

            prediction = PremktPrediction(
                symbol=symbol,
                score=round(final_score, 2),
                max_hold_days=self._max_hold_days(entry),
                entry_rationale=self._entry_rationale(entry, filing_summary=filing_summary),
                themes=sorted(dict.fromkeys(entry.themes)),
                filing_summary=filing_summary,
                generated_at=generated_at,
                cutoff_at=cutoff_at,
                source="premkt_prediction",
                market_date=market_date,
                rule_score=rule_score,
                ml_score=ml_score,
                final_score=round(final_score, 2),
                score_mode=row_score_mode,
                model_path=str(model_path) if model_path is not None else None,
                model_feature_version=model_feature_version,
                model_missing_feature_count=len(model_missing_features),
                model_missing_features=model_missing_features,
                scoring_notes=notes,
            )
            predictions.append(prediction)

        return sorted(predictions, key=lambda row: (-row.score, row.symbol))

    def _build_model_scorer(
        self,
        *,
        model_path: Path | None,
    ) -> tuple[PremktModelScorer | None, list[str]]:
        if model_path is None:
            return None, []
        try:
            return (
                PremktModelScorer(
                    model_path=model_path,
                    database_path=self.settings.database_path,
                ),
                [],
            )
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
            return None, [f"model_scoring_unavailable: {type(exc).__name__}: {exc}"]

    def _prediction_cutoff_at(
        self,
        *,
        generated_at: datetime,
        market_date: date | str | None,
        filing_cutoff: datetime | None,
    ) -> datetime:
        if filing_cutoff is not None:
            return filing_cutoff
        if market_date is not None:
            session_date = market_date if isinstance(market_date, date) else date.fromisoformat(str(market_date))
            return datetime.combine(session_date, time(8, 0), tzinfo=EASTERN)
        return generated_at

    def _score_entry(
        self,
        entry: WatchlistEntry,
        *,
        filing_summary: str,
    ) -> float:
        score = 0.0
        score += entry.total_score * self.settings.predictor_weight_total_score
        score += max(entry.catalyst_score, 0.0) * self.settings.predictor_weight_catalyst
        score += max(entry.technical_score, 0.0) * self.settings.predictor_weight_technical
        score += max(entry.sympathy_score, 0.0) * self.settings.predictor_weight_sympathy
        score += (
            max(entry.market_context_score, 0.0)
            * self.settings.predictor_weight_market_context
        )
        score += max(entry.social_score, 0.0) * self.settings.predictor_weight_social
        score += (
            max(entry.low_float_bonus, 0.0)
            * self.settings.predictor_weight_low_float_bonus
        )
        if filing_summary:
            score += self.settings.predictor_weight_filing_bonus
        if len(entry.themes) >= 2:
            score += self.settings.predictor_weight_multi_theme_bonus
        return max(0.0, min(round(score, 2), 100.0))

    def _max_hold_days(self, entry: WatchlistEntry) -> int:
        if (
            entry.catalyst_score >= self.settings.predictor_max_hold_days_tier3_catalyst
            and entry.total_score >= self.settings.predictor_max_hold_days_tier3_total
        ):
            return 3
        if entry.total_score >= self.settings.predictor_max_hold_days_tier2_total:
            return 2
        return 1

    def _entry_rationale(
        self,
        entry: WatchlistEntry,
        *,
        filing_summary: str,
    ) -> str:
        rationale_parts: list[str] = []
        if entry.catalyst_score > 0:
            rationale_parts.append("catalyst-backed")
        if entry.low_float_bonus > 0:
            rationale_parts.append("low-float")
        if entry.technical_score >= 1.0:
            rationale_parts.append("technical-setup")
        if entry.market_context_score >= 1.0:
            rationale_parts.append("live-context")
        if not rationale_parts:
            rationale_parts.append("watchlist-ranked")

        reason_tail = ", ".join(entry.reasons[:3])
        if filing_summary:
            return f"{' '.join(rationale_parts)}; filings={filing_summary}; reasons={reason_tail}"
        return f"{' '.join(rationale_parts)}; reasons={reason_tail}"

    def _resolve_scan_id(self, *, market_date: date | str | None) -> str | None:
        if market_date is not None:
            rows = fetch_passed_universe_for_market_date(
                self.settings.database_path,
                self._coerce_market_date(market_date).isoformat(),
            )
        else:
            rows = fetch_latest_passed_universe(
                self.settings.database_path,
                prefer_reportable=False,
            )
        if not rows:
            return None
        return str(rows[0]["scan_id"])

    def _coerce_market_date(self, value: date | str) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
