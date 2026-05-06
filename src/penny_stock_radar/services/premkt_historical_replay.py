from __future__ import annotations

import csv
import json
import subprocess
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    fetch_historical_minute_bars_for_symbols,
    fetch_latest_watchlist,
    fetch_point_in_time_scan_id,
    init_database,
)
from ..models import MarketActivity, PaperPosition, PremktPrediction, WatchlistEntry
from .activity_sanitizer import sanitize_activity_for_bucket
from .csv_utils import iter_non_comment_lines
from .fill_model import FillModel
from .market_activity import MarketActivityScanner
from .paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
)
from .premkt_model_scoring import PremktModelScorer
from .premkt_predictor import PremktPredictor
from .replay_bar_cache import ReplayBarCursor, ReplayBarSeriesCache
from .replay_grade_stamper import (
    ReplayGrade,
    csv_decision_grade_comment,
    replay_grade_to_dict,
    stamp_replay_run,
)
from .setup_state import SETUP_STATES, AISetupJudgeV1, SetupContext, SetupContextBuilder, SetupJudgement

EASTERN = ZoneInfo("America/New_York")
DEFAULT_REPLAY_DB = Path("data/backtest_lab/penny_stock_radar.sqlite3")
LIVE_DB = Path("data/penny_stock_radar.sqlite3")
PAPER_EXPORT_DIR = Path("sample_outputs/paper_trading")
REPLAY_BUCKETS = (
    PREDICTOR_WEIGHTED_BUCKET,
    MOMENTUM_ONLY_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
)
DEFAULT_ENTRY_LABELS = (
    "OPENING_RANGE_CANDIDATE",
    "CONDITIONAL_ENTRY",
    "NEWS_CHECK_FIRST",
)


@dataclass(frozen=True)
class ReplayOptions:
    start_date: str
    end_date: str
    db_path: Path = DEFAULT_REPLAY_DB
    model_path: Path | None = None
    score_mode: str = "blend"
    ml_weight: float = 0.5
    export_dir: Path | None = None
    cutoff_time: time = time(8, 0)
    max_runtime_seconds: float | None = None
    resume: bool = False
    allow_live_db: bool = False
    entry_labels: tuple[str, ...] = DEFAULT_ENTRY_LABELS
    exclude_entry_labels: tuple[str, ...] = ()
    require_l1_quotes_for_entries: bool = False
    entry_score_upper_bound: float | None = None
    min_entry_time: time | None = None
    exit_labels: tuple[str, ...] = ()
    breakeven_stop_after_r: float | None = None
    max_entries_per_symbol_per_day: int | None = None
    cooldown_after_stop_minutes: float | None = None
    require_entry_setup_states: tuple[str, ...] | None = None


@dataclass
class _Position:
    symbol: str
    bucket: str
    quantity: int
    entry_price: float
    stop_price: float
    initial_stop_price: float
    opened_at: datetime
    entry_score: float
    entry_label: str
    predictor_score: float | None
    predictor_weight: float
    entry_reasons: list[str]
    fees_paid: float
    best_close_price: float
    worst_close_price: float
    best_intrabar_price: float
    worst_intrabar_price: float
    first_0_5r_at: datetime | None = None
    first_1r_at: datetime | None = None
    intrabar_stop_touched: bool = False
    intrabar_stop_first_at: datetime | None = None
    stop_trigger: str = "none"
    breakeven_stop_active: bool = False
    breakeven_stop_after_r: float | None = None
    breakeven_stop_activated_at: datetime | None = None
    entry_setup_judgement: SetupJudgement | None = None


@dataclass
class _BucketState:
    bucket: str
    cash: float
    positions: dict[str, _Position] = field(default_factory=dict)
    trades: list[dict[str, object]] = field(default_factory=list)
    entry_counts_by_symbol: dict[str, int] = field(default_factory=dict)
    last_stop_at_by_symbol: dict[str, datetime] = field(default_factory=dict)


@dataclass
class ReplayResult:
    export_dir: Path
    run_manifest_path: Path
    progress_path: Path
    summary_path: Path
    conclusion_status: str


class ReplaySafetyError(ValueError):
    pass


class PremktHistoricalReplayRunner:
    """Local point-in-time historical replay runner for premarket model scoring."""

    def __init__(self, settings: AppSettings, *, options: ReplayOptions) -> None:
        self.settings = settings.model_copy(update={"db_path": Path(options.db_path)})
        self.options = options
        if options.require_entry_setup_states is not None:
            unknown_states = sorted(set(options.require_entry_setup_states) - set(SETUP_STATES))
            if unknown_states:
                raise ValueError(
                    "Unknown entry setup states: "
                    f"{', '.join(unknown_states)}. Allowed states: {', '.join(SETUP_STATES)}"
                )
        self.db_path = Path(options.db_path)
        self.export_dir = self._resolve_export_dir(options.export_dir)
        self.run_id = self.export_dir.name
        self.scanner = MarketActivityScanner(self.settings)
        self.fill_model = FillModel(self.settings)
        self.predictor = PremktPredictor(self.settings)
        self.setup_context_builder = SetupContextBuilder(
            run_id=self.run_id,
            min_dollar_volume=float(self.settings.premarket_min_dollar_volume),
            max_spread_pct=float(self.settings.premarket_max_spread_pct),
        )
        self.setup_judge = AISetupJudgeV1()
        self._allowed_entry_labels = set(options.entry_labels) - set(options.exclude_entry_labels)
        self._exit_labels = set(options.exit_labels)
        self._required_entry_setup_states = (
            set(options.require_entry_setup_states)
            if options.require_entry_setup_states is not None
            else None
        )
        self._progress_path = self.export_dir / "progress.json"
        self._summary_path = self.export_dir / "replay_summary.json"
        self._manifest_path = self.export_dir / "run_manifest.json"
        self._prediction_rows: list[dict[str, object]] = []
        self._trade_rows: list[dict[str, object]] = []
        self._setup_feature_rows: list[dict[str, object]] = []
        self._model_scorer: PremktModelScorer | None = None
        self._model_scoring_notes: list[str] | None = None
        self._coverage_warnings: list[dict[str, object]] = []
        self._data_quality_notes: list[str] = []
        self._leakage_guard_notes: list[str] = [
            "Universe/watchlist is loaded from point-in-time scan_runs where market_date <= replay date.",
            "Predictor model features use only historical_minute_bars for the replay date before cutoff.",
            "Entry activity is built from bars at or before each simulated clock timestamp.",
            "Future bars are used only for later clock steps and end-of-day outcome calculation.",
        ]

    def run(self) -> ReplayResult:
        if self.options.score_mode.lower() not in {"rule", "ml", "blend"}:
            raise ValueError("score_mode must be one of: blend, ml, rule")
        if not 0.0 <= self.options.ml_weight <= 1.0:
            raise ValueError("ml_weight must be between 0.0 and 1.0")
        if not self.options.entry_labels:
            raise ValueError("At least one entry label must be enabled.")
        if not (set(self.options.entry_labels) - set(self.options.exclude_entry_labels)):
            raise ValueError("Entry label policy excludes all enabled labels.")
        if self.options.breakeven_stop_after_r is not None and self.options.breakeven_stop_after_r < 0:
            raise ValueError("breakeven_stop_after_r must be greater than or equal to 0.0.")
        if (
            self.options.max_entries_per_symbol_per_day is not None
            and self.options.max_entries_per_symbol_per_day < 1
        ):
            raise ValueError("max_entries_per_symbol_per_day must be at least 1.")
        if self.options.cooldown_after_stop_minutes is not None and self.options.cooldown_after_stop_minutes < 0:
            raise ValueError("cooldown_after_stop_minutes must be greater than or equal to 0.0.")
        validate_replay_paths(
            db_path=self.db_path,
            export_dir=self.export_dir,
            allow_live_db=self.options.allow_live_db,
        )
        if not self.db_path.exists():
            raise FileNotFoundError(f"Historical replay DB not found: {self.db_path}")
        init_database(self.db_path)
        self.export_dir.mkdir(parents=True, exist_ok=True)

        dates = [day.isoformat() for day in _date_range(self.options.start_date, self.options.end_date)]
        progress = self._load_or_create_progress(dates)
        if self.options.resume:
            self._load_existing_outputs()
        self._write_json(self._manifest_path, self._build_manifest())
        self._write_json(self._progress_path, progress)

        started = time_module.monotonic()
        for market_date in dates:
            status = progress["dates"].setdefault(market_date, {"status": "pending"})
            if self.options.resume and status.get("status") == "completed":
                continue
            if self.options.max_runtime_seconds is not None:
                if time_module.monotonic() - started >= self.options.max_runtime_seconds:
                    break
            self._run_market_date(market_date, progress)

        summary = self._build_summary(progress)
        replay_grade = stamp_replay_run(self.export_dir)
        summary["replay_grade"] = replay_grade_to_dict(replay_grade)
        self._export_predictions()
        self._export_trades_and_kpis(summary, replay_grade=replay_grade)
        self._write_json(self._summary_path, summary)
        self._write_json(self._progress_path, progress)
        return ReplayResult(
            export_dir=self.export_dir,
            run_manifest_path=self._manifest_path,
            progress_path=self._progress_path,
            summary_path=self._summary_path,
            conclusion_status=str(summary["conclusion_status"]),
        )

    def _run_market_date(self, market_date: str, progress: dict[str, Any]) -> None:
        started_at = _utc_now_iso()
        date_started = time_module.monotonic()
        progress["dates"][market_date] = {
            "status": "running",
            "started_at": started_at,
        }
        self._write_json(self._progress_path, progress)
        try:
            scan_row = fetch_point_in_time_scan_id(self.db_path, market_date)
            if scan_row is None:
                self._skip(
                    progress,
                    market_date,
                    "missing_point_in_time_universe",
                    started_at=started_at,
                    date_started=date_started,
                )
                return
            scan_id = str(scan_row["scan_id"])
            watchlist_rows = fetch_latest_watchlist(
                self.db_path,
                limit=max(int(self.settings.watchlist_limit), 1_000),
                scan_id=scan_id,
                prefer_reportable=False,
            )
            if not watchlist_rows:
                self._skip(
                    progress,
                    market_date,
                    "missing_point_in_time_watchlist",
                    started_at=started_at,
                    date_started=date_started,
                )
                return

            cutoff_at = self._cutoff_at(market_date)
            bars = self._load_bars(
                market_date=market_date,
                symbols=[str(row["symbol"]).upper() for row in watchlist_rows],
            )
            predictions = self._score_predictions(
                market_date=market_date,
                cutoff_at=cutoff_at,
                watchlist_rows=watchlist_rows,
                bar_cache=bars,
            )
            self._prediction_rows.extend(
                self._prediction_to_row(market_date, scan_id, row) for row in predictions
            )
            if not bars:
                self._skip(
                    progress,
                    market_date,
                    "missing_historical_minute_bars",
                    started_at=started_at,
                    date_started=date_started,
                    loaded_bar_rows=bars.loaded_bar_count,
                    loaded_symbol_count=bars.loaded_symbol_count,
                )
                return
            simulated_times = bars.simulated_times(cutoff_at)
            date_trade_rows = self._replay_intraday(
                market_date=market_date,
                cutoff_at=cutoff_at,
                watchlist_rows=watchlist_rows,
                predictions=predictions,
                bars=bars,
                simulated_times=simulated_times,
            )
            self._trade_rows.extend(date_trade_rows)
            finished_at = _utc_now_iso()
            progress["dates"][market_date] = {
                "status": "completed",
                "started_at": started_at,
                "finished_at": finished_at,
                "completed_at": finished_at,
                "elapsed_seconds": round(time_module.monotonic() - date_started, 6),
                "scan_id": scan_id,
                "watchlist_count": len(watchlist_rows),
                "prediction_count": len(predictions),
                "loaded_bar_rows": bars.loaded_bar_count,
                "loaded_symbol_count": bars.loaded_symbol_count,
                "simulated_time_count": len(simulated_times),
                "trade_count": len(date_trade_rows),
            }
            self._write_json(self._progress_path, progress)
        except Exception as exc:
            failed_at = _utc_now_iso()
            progress["dates"][market_date] = {
                "status": "failed",
                "started_at": started_at,
                "finished_at": failed_at,
                "failed_at": failed_at,
                "elapsed_seconds": round(time_module.monotonic() - date_started, 6),
                "reason": f"{type(exc).__name__}: {exc}",
            }
            self._write_json(self._progress_path, progress)

    def _skip(
        self,
        progress: dict[str, Any],
        market_date: str,
        reason: str,
        *,
        started_at: str | None = None,
        date_started: float | None = None,
        loaded_bar_rows: int | None = None,
        loaded_symbol_count: int | None = None,
    ) -> None:
        note = {
            "market_date": market_date,
            "reason": reason,
        }
        self._coverage_warnings.append(note)
        skipped_at = _utc_now_iso()
        payload: dict[str, object] = {
            "status": "skipped",
            "skipped_at": skipped_at,
            "reason": reason,
        }
        if started_at is not None:
            payload["started_at"] = started_at
            payload["finished_at"] = skipped_at
        if date_started is not None:
            payload["elapsed_seconds"] = round(time_module.monotonic() - date_started, 6)
        if loaded_bar_rows is not None:
            payload["loaded_bar_rows"] = loaded_bar_rows
        if loaded_symbol_count is not None:
            payload["loaded_symbol_count"] = loaded_symbol_count
        progress["dates"][market_date] = payload
        self._write_json(self._progress_path, progress)

    def _score_predictions(
        self,
        *,
        market_date: str,
        cutoff_at: datetime,
        watchlist_rows: Iterable[Any],
        bar_cache: ReplayBarSeriesCache | None = None,
    ) -> list[PremktPrediction]:
        scorer, scoring_notes = self._model_scorer_for_run()
        predictions: list[PremktPrediction] = []
        entries = [_watchlist_entry_from_row(row) for row in watchlist_rows]
        model_scores = (
            scorer.score_symbols(
                symbols=[entry.symbol for entry in entries],
                market_date=market_date,
                cutoff_at=cutoff_at,
                bar_cache=bar_cache,
            )
            if scorer is not None and self.options.score_mode.lower() != "rule"
            else {}
        )
        for entry in entries:
            rule_score = self.predictor._score_entry(entry, filing_summary="")
            ml_score: float | None = None
            feature_version: str | None = None
            missing: list[str] = []
            notes = list(scoring_notes)
            score_mode = self.options.score_mode.lower()
            final_score = rule_score
            row_score_mode = score_mode
            if scorer is not None and score_mode != "rule":
                model_score = model_scores.get(entry.symbol)
                if model_score is None:
                    model_score = scorer.fallback_score(
                        symbol=entry.symbol,
                        notes=[
                            "model_scoring_unavailable: no historical minute bars before cutoff."
                        ],
                    )
                ml_score = model_score.ml_score
                feature_version = model_score.model_feature_version
                missing = model_score.missing_feature_names
                notes.extend(model_score.scoring_notes)
                if ml_score is None:
                    row_score_mode = "rule"
                    notes.append(f"requested_score_mode={score_mode}; fell back to rule score.")
                elif score_mode == "ml":
                    final_score = ml_score
                elif score_mode == "blend":
                    final_score = ((1.0 - self.options.ml_weight) * rule_score) + (
                        self.options.ml_weight * ml_score
                    )
            elif score_mode != "rule":
                row_score_mode = "rule"

            predictions.append(
                PremktPrediction(
                    symbol=entry.symbol,
                    score=round(final_score, 2),
                    max_hold_days=self.predictor._max_hold_days(entry),
                    entry_rationale=self.predictor._entry_rationale(entry, filing_summary=""),
                    themes=entry.themes,
                    filing_summary="",
                    generated_at=cutoff_at,
                    cutoff_at=cutoff_at,
                    source="premkt_historical_replay",
                    market_date=market_date,
                    rule_score=rule_score,
                    ml_score=ml_score,
                    final_score=round(final_score, 2),
                    score_mode=row_score_mode,
                    model_path=str(self.options.model_path) if self.options.model_path else None,
                    model_feature_version=feature_version,
                    model_missing_feature_count=len(missing),
                    model_missing_features=missing,
                    scoring_notes=notes,
                )
            )
        return sorted(predictions, key=lambda item: (-item.score, item.symbol))

    def _model_scorer_for_run(self) -> tuple[PremktModelScorer | None, list[str]]:
        if self._model_scoring_notes is None:
            self._model_scorer, self._model_scoring_notes = self._build_model_scorer()
        return self._model_scorer, list(self._model_scoring_notes)

    def _build_model_scorer(self) -> tuple[PremktModelScorer | None, list[str]]:
        score_mode = self.options.score_mode.lower()
        if score_mode == "rule":
            return None, []
        if self.options.model_path is None:
            return None, ["model_scoring_unavailable: model_path is not set."]
        try:
            return (
                PremktModelScorer(
                    model_path=self.options.model_path,
                    database_path=self.db_path,
                    cutoff_time=self.options.cutoff_time,
                ),
                [],
            )
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
            return None, [f"model_scoring_unavailable: {type(exc).__name__}: {exc}"]

    def _load_bars(self, *, market_date: str, symbols: list[str]) -> ReplayBarSeriesCache:
        requested_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        rows = fetch_historical_minute_bars_for_symbols(
            self.db_path,
            market_date=market_date,
            symbols=requested_symbols,
        )
        cache = ReplayBarSeriesCache.from_rows(rows)
        missing = sorted(set(requested_symbols) - set(cache.symbols))
        if missing:
            self._coverage_warnings.append(
                {
                    "market_date": market_date,
                    "reason": "missing_symbol_minute_bars",
                    "symbols": missing[:50],
                    "symbol_count": len(missing),
                }
            )
        if cache.has_missing_bid_ask:
            self._coverage_warnings.append(
                {
                    "market_date": market_date,
                    "reason": "missing_l1_bid_ask_fallback_to_last_price",
                }
            )
            self._data_quality_notes.append(
                f"{market_date}: some bars lack bid/ask; FillModel may fall back to last price."
            )
        return cache

    def _replay_intraday(
        self,
        *,
        market_date: str,
        cutoff_at: datetime,
        watchlist_rows: Iterable[Any],
        predictions: list[PremktPrediction],
        bars: ReplayBarSeriesCache,
        simulated_times: list[datetime],
    ) -> list[dict[str, object]]:
        predictions_by_symbol = {row.symbol: row for row in predictions}
        watchlist_rank = {
            str(row["symbol"]).upper(): index
            for index, row in enumerate(watchlist_rows, start=1)
        }
        watchlist_score = {
            str(row["symbol"]).upper(): float(row["total_score"])
            for row in watchlist_rows
            if row["total_score"] is not None
        }
        states = {
            bucket: _BucketState(bucket=bucket, cash=float(self.settings.paper_initial_capital))
            for bucket in REPLAY_BUCKETS
        }
        bar_cursor = bars.cursor()
        latest_activity_by_symbol: dict[str, MarketActivity] = {}
        date_trade_rows: list[dict[str, object]] = []
        for simulated_time in simulated_times:
            base_activity = bar_cursor.activity_at(
                simulated_time=simulated_time,
                scanner=self.scanner,
                watchlist_rank=watchlist_rank,
                watchlist_score=watchlist_score,
                predictions_by_symbol=predictions_by_symbol,
            )
            self._apply_ranks(base_activity)
            for row in base_activity:
                latest_activity_by_symbol[row.symbol] = row
            bucket_activity_by_name = {
                PREDICTOR_WEIGHTED_BUCKET: base_activity,
                MOMENTUM_ONLY_BUCKET: base_activity,
                WATCHLIST_BLIND_MOMENTUM_BUCKET: sanitize_activity_for_bucket(
                    self.scanner,
                    WATCHLIST_BLIND_MOMENTUM_BUCKET,
                    base_activity,
                ),
            }
            for bucket, state in states.items():
                date_trade_rows.extend(
                    self._process_bucket_time(
                        state=state,
                        market_date=market_date,
                        simulated_time=simulated_time,
                        activity=bucket_activity_by_name[bucket],
                        predictions_by_symbol=predictions_by_symbol,
                        bar_cursor=bar_cursor,
                        cutoff_at=cutoff_at,
                    )
                )

        for state in states.values():
            for position in list(state.positions.values()):
                row = latest_activity_by_symbol.get(position.symbol)
                if row is not None:
                    bucket_row = sanitize_activity_for_bucket(self.scanner, state.bucket, [row])[0]
                    session_end_at = row.market_data_at or cutoff_at
                    setup = self._setup_for_row(
                        state=state,
                        market_date=market_date,
                        simulated_time=session_end_at,
                        row=bucket_row,
                        bar_cursor=bar_cursor,
                        cutoff_at=cutoff_at,
                    )
                    date_trade_rows.append(
                        self._close_position(
                            state,
                            position,
                            row=bucket_row,
                            market_date=market_date,
                            simulated_time=session_end_at,
                            reason="session_end",
                            setup_judgement=setup[1] if setup is not None else None,
                        )
                    )
        return date_trade_rows

    def _process_bucket_time(
        self,
        *,
        state: _BucketState,
        market_date: str,
        simulated_time: datetime,
        activity: list[MarketActivity],
        predictions_by_symbol: dict[str, PremktPrediction],
        bar_cursor: ReplayBarCursor,
        cutoff_at: datetime,
    ) -> list[dict[str, object]]:
        trades: list[dict[str, object]] = []
        setup_by_symbol: dict[str, tuple[SetupContext, SetupJudgement]] = {}
        for row in activity:
            setup = self._setup_for_row(
                state=state,
                market_date=market_date,
                simulated_time=simulated_time,
                row=row,
                bar_cursor=bar_cursor,
                cutoff_at=cutoff_at,
            )
            if setup is None:
                continue
            setup_by_symbol[row.symbol] = setup
            context, judgement = setup
            self._setup_feature_rows.append(context.feature_row(judgement))

        for row in activity:
            position = state.positions.get(row.symbol)
            if position is None:
                continue
            self._update_position_path(position, row=row, simulated_time=simulated_time)
            stop_reference_price = row.bar_low_price if row.bar_low_price is not None else row.last_price
            if stop_reference_price is not None and stop_reference_price <= position.stop_price:
                if (
                    row.bar_low_price is not None
                    and row.last_price is not None
                    and row.bar_low_price <= position.stop_price
                    and row.last_price > position.stop_price
                ):
                    position.stop_trigger = "intrabar"
                else:
                    position.stop_trigger = "close"
                trades.append(
                    self._close_position(
                        state,
                        position,
                        row=row,
                        market_date=market_date,
                        simulated_time=simulated_time,
                        reason="stop_loss",
                        setup_judgement=setup_by_symbol.get(row.symbol, (None, None))[1],
                    )
                )
        for row in activity:
            position = state.positions.get(row.symbol)
            if position is not None and row.analysis_label in self._exit_labels:
                trades.append(
                    self._close_position(
                        state,
                        position,
                        row=row,
                        market_date=market_date,
                        simulated_time=simulated_time,
                        reason="label_exit",
                        setup_judgement=setup_by_symbol.get(row.symbol, (None, None))[1],
                    )
                )
        for row in sorted(activity, key=lambda item: (-item.analysis_score, item.pct_rank or 9999, item.symbol)):
            if row.symbol in state.positions:
                continue
            if not self._entry_reentry_policy_allows(
                state=state,
                symbol=row.symbol,
                simulated_time=simulated_time,
            ):
                continue
            if len(state.positions) >= max(int(self.settings.paper_adaptive_max_open_positions), 1):
                continue
            prediction = predictions_by_symbol.get(row.symbol)
            predictor_score = prediction.score if prediction is not None else None
            predictor_weight = _predictor_weight(predictor_score) if state.bucket == PREDICTOR_WEIGHTED_BUCKET and row.predicted else 0.0
            threshold = max(
                float(self.settings.paper_entry_score_min)
                - (float(self.settings.paper_predictor_weight_k1) * predictor_weight),
                0.0,
            )
            if row.analysis_score < threshold:
                continue
            if (
                self.options.entry_score_upper_bound is not None
                and row.analysis_score >= self.options.entry_score_upper_bound
            ):
                continue
            if self.options.min_entry_time is not None and simulated_time.time() < self.options.min_entry_time:
                continue
            if self.options.require_l1_quotes_for_entries and not row.has_live_quote:
                continue
            if row.analysis_label not in self._allowed_entry_labels:
                continue
            entry_setup = setup_by_symbol.get(row.symbol)
            if entry_setup is not None and entry_setup[0].position_state != "flat":
                entry_setup = self._setup_for_row(
                    state=state,
                    market_date=market_date,
                    simulated_time=simulated_time,
                    row=row,
                    bar_cursor=bar_cursor,
                    cutoff_at=cutoff_at,
                )
                if entry_setup is not None:
                    context, judgement = entry_setup
                    self._setup_feature_rows.append(context.feature_row(judgement))
            if self._required_entry_setup_states is not None and (
                entry_setup is None or entry_setup[1].setup_state not in self._required_entry_setup_states
            ):
                continue
            entry = self._open_position(
                state,
                row=row,
                market_date=market_date,
                simulated_time=simulated_time,
                predictor_score=predictor_score,
                predictor_weight=predictor_weight,
                setup_judgement=entry_setup[1] if entry_setup is not None else None,
            )
            if entry is not None:
                trades.append(entry)
        return trades

    def _setup_for_row(
        self,
        *,
        state: _BucketState,
        market_date: str,
        simulated_time: datetime,
        row: MarketActivity,
        bar_cursor: ReplayBarCursor,
        cutoff_at: datetime,
    ) -> tuple[SetupContext, SetupJudgement] | None:
        snapshot = bar_cursor.latest_snapshot(row.symbol)
        if snapshot is None:
            return None
        position = state.positions.get(row.symbol)
        context = self.setup_context_builder.build(
            market_date=market_date,
            bucket=state.bucket,
            row=row,
            snapshot=snapshot,
            observed_at=simulated_time,
            cutoff_at=cutoff_at,
            position_opened_at=position.opened_at if position is not None else None,
            position_entry_price=position.entry_price if position is not None else None,
        )
        return context, self.setup_judge.judge(context)

    def _entry_reentry_policy_allows(
        self,
        *,
        state: _BucketState,
        symbol: str,
        simulated_time: datetime,
    ) -> bool:
        max_entries = self.options.max_entries_per_symbol_per_day
        if max_entries is not None and state.entry_counts_by_symbol.get(symbol, 0) >= max_entries:
            return False
        cooldown_minutes = self.options.cooldown_after_stop_minutes
        if cooldown_minutes is None:
            return True
        last_stop_at = state.last_stop_at_by_symbol.get(symbol)
        if last_stop_at is None:
            return True
        return simulated_time >= last_stop_at + timedelta(minutes=cooldown_minutes)

    def _open_position(
        self,
        state: _BucketState,
        *,
        row: MarketActivity,
        market_date: str,
        simulated_time: datetime,
        predictor_score: float | None,
        predictor_weight: float,
        setup_judgement: SetupJudgement | None,
    ) -> dict[str, object] | None:
        reference = row.ask_price or row.last_price
        if reference is None or reference <= 0:
            return None
        fraction = float(self.settings.paper_adaptive_predicted_entry_size_fraction)
        if state.bucket == WATCHLIST_BLIND_MOMENTUM_BUCKET:
            fraction = float(self.settings.paper_adaptive_live_exception_entry_size_fraction)
        fraction = min(
            fraction * (1.0 + float(self.settings.paper_predictor_weight_k2) * predictor_weight),
            float(self.settings.paper_entry_size_fraction),
        )
        requested_quantity = int((state.cash * fraction) // reference)
        if requested_quantity <= 0:
            return None
        fill = self.fill_model.buy(
            row,
            market_phase=row.market_phase,
            requested_quantity=requested_quantity,
        )
        if fill.quantity <= 0 or fill.fill_price is None:
            return None
        state.cash -= (fill.fill_price * fill.quantity) + fill.transaction_cost
        stop_price = fill.fill_price * (1.0 - (float(self.settings.paper_stop_loss_pct) / 100.0))
        position = _Position(
            symbol=row.symbol,
            bucket=state.bucket,
            quantity=fill.quantity,
            entry_price=fill.fill_price,
            stop_price=stop_price,
            initial_stop_price=stop_price,
            opened_at=simulated_time,
            entry_score=row.analysis_score,
            entry_label=row.analysis_label,
            predictor_score=predictor_score,
            predictor_weight=predictor_weight,
            entry_reasons=[*row.reasons, f"predictor_score:{predictor_score:.1f}" if predictor_score is not None else ""],
            fees_paid=fill.transaction_cost,
            best_close_price=fill.fill_price,
            worst_close_price=fill.fill_price,
            best_intrabar_price=fill.fill_price,
            worst_intrabar_price=fill.fill_price,
            breakeven_stop_after_r=self.options.breakeven_stop_after_r,
            entry_setup_judgement=setup_judgement,
        )
        state.positions[row.symbol] = position
        state.entry_counts_by_symbol[row.symbol] = state.entry_counts_by_symbol.get(row.symbol, 0) + 1
        return {
            "run_id": self.run_id,
            "market_date": market_date,
            "bucket": state.bucket,
            "symbol": row.symbol,
            "event": "ENTRY",
            "status": "OPEN",
            "entry_at": simulated_time.isoformat(),
            "exit_at": "",
            "quantity": fill.quantity,
            "entry_price": round(fill.fill_price, 6),
            "exit_price": "",
            "gross_pnl": "",
            "net_pnl": "",
            "realized_pnl_pct": "",
            "transaction_cost": round(fill.transaction_cost, 6),
            "predictor_score": predictor_score,
            "predictor_weight": round(predictor_weight, 6),
            "analysis_score": row.analysis_score,
            "analysis_label": row.analysis_label,
            "entry_analysis_label": row.analysis_label,
            "exit_analysis_label": "",
            "entry_reasons": ", ".join(reason for reason in position.entry_reasons if reason),
            "exit_reason": "",
            "holding_minutes": "",
            "has_l1_quote": int(row.has_live_quote),
            "fill_status": fill.fill_status,
            **_setup_prefixed_fields(setup_judgement, "entry"),
            **_fill_metrics(fill),
        }

    def _close_position(
        self,
        state: _BucketState,
        position: _Position,
        *,
        row: MarketActivity,
        market_date: str,
        simulated_time: datetime,
        reason: str,
        setup_judgement: SetupJudgement | None,
    ) -> dict[str, object]:
        paper_position = PaperPosition(
            position_id=f"{self.run_id}-{state.bucket}-{position.symbol}",
            run_id=self.run_id,
            symbol=position.symbol,
            entry_phase=row.market_phase,
            quantity=position.quantity,
            average_entry_price=position.entry_price,
            cost_basis=position.entry_price * position.quantity,
            stop_price=position.stop_price,
            strategy_bucket=state.bucket,
            fees_paid_total=position.fees_paid,
            opened_at=position.opened_at,
        )
        fill = self.fill_model.sell(
            position=paper_position,
            row=row,
            reason=reason,
            market_phase=row.market_phase,
            requested_quantity=position.quantity,
        )
        exit_price = fill.fill_price or row.last_price or position.entry_price
        if reason == "stop_loss" and exit_price > position.stop_price:
            exit_price = position.stop_price
        gross_pnl = (exit_price - position.entry_price) * fill.quantity
        net_pnl = gross_pnl - position.fees_paid - fill.transaction_cost
        state.cash += (exit_price * fill.quantity) - fill.transaction_cost
        state.positions.pop(position.symbol, None)
        if reason == "stop_loss":
            state.last_stop_at_by_symbol[position.symbol] = simulated_time
        holding_minutes = (simulated_time - position.opened_at).total_seconds() / 60.0
        row_payload = {
            "run_id": self.run_id,
            "market_date": market_date,
            "bucket": state.bucket,
            "symbol": position.symbol,
            "event": "EXIT",
            "status": "CLOSED",
            "entry_at": position.opened_at.isoformat(),
            "exit_at": simulated_time.isoformat(),
            "quantity": fill.quantity,
            "entry_price": round(position.entry_price, 6),
            "exit_price": round(exit_price, 6),
            "gross_pnl": round(gross_pnl, 6),
            "net_pnl": round(net_pnl, 6),
            "realized_pnl_pct": round((net_pnl / (position.entry_price * position.quantity)) * 100.0, 6),
            "transaction_cost": round(position.fees_paid + fill.transaction_cost, 6),
            "predictor_score": position.predictor_score,
            "predictor_weight": round(position.predictor_weight, 6),
            "analysis_score": position.entry_score,
            "analysis_label": position.entry_label,
            "entry_analysis_label": position.entry_label,
            "exit_analysis_label": row.analysis_label,
            "entry_reasons": ", ".join(reason for reason in position.entry_reasons if reason),
            "exit_reason": reason,
            "holding_minutes": round(holding_minutes, 6),
            "has_l1_quote": int(row.has_live_quote),
            "fill_status": fill.fill_status,
            **_setup_prefixed_fields(position.entry_setup_judgement, "entry"),
            **_setup_prefixed_fields(setup_judgement, "exit"),
            **_position_path_metrics(
                position,
                exit_price=exit_price,
                exit_reason=reason,
            ),
            **_fill_metrics(fill),
        }
        state.trades.append(row_payload)
        return row_payload

    def _update_position_path(
        self,
        position: _Position,
        *,
        row: MarketActivity,
        simulated_time: datetime,
    ) -> None:
        close_price = row.last_price
        if close_price is not None and close_price > 0:
            position.best_close_price = max(position.best_close_price, close_price)
            position.worst_close_price = min(position.worst_close_price, close_price)
        high_price = row.bar_high_price if row.bar_high_price is not None else close_price
        low_price = row.bar_low_price if row.bar_low_price is not None else close_price
        if high_price is not None and high_price > 0:
            position.best_intrabar_price = max(position.best_intrabar_price, high_price)
        if low_price is not None and low_price > 0:
            position.worst_intrabar_price = min(position.worst_intrabar_price, low_price)
            if low_price <= position.stop_price:
                position.intrabar_stop_touched = True
                if position.intrabar_stop_first_at is None:
                    position.intrabar_stop_first_at = simulated_time

        risk_per_share = position.entry_price - position.initial_stop_price
        if risk_per_share <= 0:
            return
        reached_price_0_5r = position.entry_price + (0.5 * risk_per_share)
        reached_price_1r = position.entry_price + risk_per_share
        reference_high = high_price if high_price is not None else close_price
        if reference_high is None:
            return
        if position.first_0_5r_at is None and reference_high >= reached_price_0_5r:
            position.first_0_5r_at = simulated_time
        if position.first_1r_at is None and reference_high >= reached_price_1r:
            position.first_1r_at = simulated_time
        if (
            position.breakeven_stop_after_r is not None
            and not position.breakeven_stop_active
            and close_price is not None
            and close_price >= position.entry_price + (position.breakeven_stop_after_r * risk_per_share)
        ):
            position.stop_price = max(position.stop_price, position.entry_price)
            position.breakeven_stop_active = True
            position.breakeven_stop_activated_at = simulated_time

    def _build_manifest(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "created_at": _utc_now_iso(),
            "command": "run-premkt-model-replay",
            "options": {
                "start_date": self.options.start_date,
                "end_date": self.options.end_date,
                "db_path": str(self.db_path),
                "model_path": str(self.options.model_path) if self.options.model_path else None,
                "score_mode": self.options.score_mode,
                "ml_weight": self.options.ml_weight,
                "cutoff_time": self.options.cutoff_time.strftime("%H:%M"),
                "max_runtime_seconds": self.options.max_runtime_seconds,
                "resume": self.options.resume,
                "entry_labels": list(self.options.entry_labels),
                "exclude_entry_labels": list(self.options.exclude_entry_labels),
                "require_l1_quotes_for_entries": self.options.require_l1_quotes_for_entries,
                "entry_score_upper_bound": self.options.entry_score_upper_bound,
                "min_entry_time": self.options.min_entry_time.strftime("%H:%M")
                if self.options.min_entry_time
                else None,
                "exit_labels": list(self.options.exit_labels),
                "breakeven_stop_after_r": self.options.breakeven_stop_after_r,
                "max_entries_per_symbol_per_day": self.options.max_entries_per_symbol_per_day,
                "cooldown_after_stop_minutes": self.options.cooldown_after_stop_minutes,
                "require_entry_setup_states": list(self.options.require_entry_setup_states)
                if self.options.require_entry_setup_states is not None
                else None,
            },
            "db_path": str(self.db_path),
            "export_dir": str(self.export_dir),
            "start_date": self.options.start_date,
            "end_date": self.options.end_date,
            "cutoff_time": self.options.cutoff_time.strftime("%H:%M"),
            "model_path": str(self.options.model_path) if self.options.model_path else None,
            "score_mode": self.options.score_mode,
            "ml_weight": self.options.ml_weight,
            "local_only": True,
            "git_sha": _git_sha(),
            "settings_snapshot": {
                "paper_initial_capital": self.settings.paper_initial_capital,
                "paper_entry_score_min": self.settings.paper_entry_score_min,
                "paper_stop_loss_pct": self.settings.paper_stop_loss_pct,
                "paper_predictor_weight_k1": self.settings.paper_predictor_weight_k1,
                "paper_predictor_weight_k2": self.settings.paper_predictor_weight_k2,
                "premarket_min_dollar_volume": self.settings.premarket_min_dollar_volume,
                "premarket_max_spread_pct": self.settings.premarket_max_spread_pct,
            },
            "leakage_guard": self._leakage_guard_notes,
            "fill_model": {
                "class": "FillModel",
                "premarket_spread_multiplier": self.settings.paper_premarket_spread_slippage_multiplier,
                "regular_spread_multiplier": self.settings.paper_regular_spread_slippage_multiplier,
                "min_trade_fee": self.settings.paper_min_trade_fee,
                "notional_fee_rate_pct": self.settings.paper_notional_fee_rate_pct,
                "volume_cap_small_pct": self.settings.paper_volume_cap_small_pct,
            },
            "bucket_policy": {
                PREDICTOR_WEIGHTED_BUCKET: "watchlist + predictor score/weight + live momentum from historical bars",
                MOMENTUM_ONLY_BUCKET: "watchlist metadata + momentum; predictor score/weight ignored",
                WATCHLIST_BLIND_MOMENTUM_BUCKET: "same scanned symbols; watchlist/predictor metadata removed before decision",
            },
            "entry_label_policy": {
                "include": list(self.options.entry_labels),
                "exclude": list(self.options.exclude_entry_labels),
                "require_l1_quotes_for_entries": self.options.require_l1_quotes_for_entries,
                "analysis_label_semantics": "paper_trade_log.analysis_label is the entry-time label; exit_analysis_label stores the label observed at exit.",
            },
            "replay_ablation_policy": {
                "entry_score_upper_bound": self.options.entry_score_upper_bound,
                "min_entry_time": self.options.min_entry_time.strftime("%H:%M")
                if self.options.min_entry_time
                else None,
                "exit_labels": list(self.options.exit_labels),
                "breakeven_stop_after_r": self.options.breakeven_stop_after_r,
                "max_entries_per_symbol_per_day": self.options.max_entries_per_symbol_per_day,
                "cooldown_after_stop_minutes": self.options.cooldown_after_stop_minutes,
                "require_entry_setup_states": list(self.options.require_entry_setup_states)
                if self.options.require_entry_setup_states is not None
                else None,
            },
            "setup_state_policy": {
                "version": "deterministic_rule_backed_v1",
                "order_control": "diagnostic_only_risk_rule_engine_still_controls_orders",
                "states": list(SETUP_STATES),
                "required_entry_states_for_entries": list(self.options.require_entry_setup_states)
                if self.options.require_entry_setup_states is not None
                else None,
                "outputs": [
                    "paper_setup_features.csv",
                    "paper_setup_state_kpis.csv",
                    "paper_setup_transition_matrix.csv",
                    "paper_add_trim_runner_diagnostics.csv",
                ],
            },
        }

    def _build_summary(self, progress: dict[str, Any]) -> dict[str, object]:
        statuses = progress.get("dates", {})
        completed = sorted(day for day, row in statuses.items() if row.get("status") == "completed")
        skipped = [
            {"market_date": day, "reason": row.get("reason", "")}
            for day, row in sorted(statuses.items())
            if row.get("status") == "skipped"
        ]
        failed = [
            {"market_date": day, "reason": row.get("reason", "")}
            for day, row in sorted(statuses.items())
            if row.get("status") == "failed"
        ]
        bucket_results = self._bucket_results()
        entry_label_results = self._entry_label_results()
        conclusion = self._conclusion_status(completed=completed, skipped=skipped, failed=failed)
        return {
            "start_date": self.options.start_date,
            "end_date": self.options.end_date,
            "market_date_count": len(statuses),
            "completed_dates": completed,
            "skipped_dates": skipped,
            "failed_dates": failed,
            "model_path": str(self.options.model_path) if self.options.model_path else None,
            "score_mode": self.options.score_mode,
            "ml_weight": self.options.ml_weight,
            "db_path": str(self.db_path),
            "export_dir": str(self.export_dir),
            "bucket_results": bucket_results,
            "entry_label_results": entry_label_results,
            "diagnostic_artifacts": {
                "entry_exit_label_matrix": "paper_entry_exit_label_matrix.csv",
                "stop_out_diagnostics": "paper_stop_out_diagnostics.csv",
                "symbol_loss_concentration": "paper_symbol_loss_concentration.csv",
                "hold_bucket_kpis": "paper_hold_bucket_kpis.csv",
                "exit_path_diagnostics": "paper_exit_path_diagnostics.csv",
                "setup_features": "paper_setup_features.csv",
                "setup_state_kpis": "paper_setup_state_kpis.csv",
                "setup_transition_matrix": "paper_setup_transition_matrix.csv",
                "add_trim_runner_diagnostics": "paper_add_trim_runner_diagnostics.csv",
            },
            "predictor_weighted_vs_watchlist_momentum": _pair_diff(
                bucket_results,
                PREDICTOR_WEIGHTED_BUCKET,
                MOMENTUM_ONLY_BUCKET,
            ),
            "predictor_weighted_vs_watchlist_blind_momentum": _pair_diff(
                bucket_results,
                PREDICTOR_WEIGHTED_BUCKET,
                WATCHLIST_BLIND_MOMENTUM_BUCKET,
            ),
            "coverage_warnings": self._coverage_warnings,
            "data_quality_notes": sorted(set(self._data_quality_notes)),
            "leakage_guard_notes": self._leakage_guard_notes,
            "conclusion_status": conclusion,
        }

    def _bucket_results(self) -> dict[str, dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        results: dict[str, dict[str, object]] = {}
        for bucket in REPLAY_BUCKETS:
            rows = [row for row in exits if row.get("bucket") == bucket]
            pnl_values = [float(row.get("net_pnl") or 0.0) for row in rows]
            wins = [value for value in pnl_values if value > 0]
            losses = [value for value in pnl_values if value < 0]
            results[bucket] = {
                "trade_count": len(rows),
                "total_net_pnl": round(sum(pnl_values), 6),
                "win_rate": round((len(wins) / len(rows)) if rows else 0.0, 6),
                "gross_profit": round(sum(wins), 6),
                "gross_loss": round(sum(losses), 6),
                "profit_factor": round((sum(wins) / abs(sum(losses))) if losses else 0.0, 6),
            }
        return results

    def _entry_label_results(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in exits:
            bucket = str(row.get("bucket") or "")
            label = _entry_label(row)
            grouped.setdefault((bucket, label), []).append(row)

        results: list[dict[str, object]] = []
        for (bucket, label), rows in sorted(grouped.items()):
            pnl_values = [float(row.get("net_pnl") or 0.0) for row in rows]
            wins = [value for value in pnl_values if value > 0]
            losses = [value for value in pnl_values if value < 0]
            stop_rows = [row for row in rows if row.get("exit_reason") == "stop_loss"]
            quick_stop_rows = [
                row
                for row in stop_rows
                if float(row.get("holding_minutes") or 0.0) <= 3.0
            ]
            results.append(
                {
                    "bucket": bucket,
                    "analysis_label": label,
                    "trade_count": len(rows),
                    "stop_loss_count": len(stop_rows),
                    "quick_stop_3m_count": len(quick_stop_rows),
                    "session_end_count": sum(1 for row in rows if row.get("exit_reason") == "session_end"),
                    "total_net_pnl": round(sum(pnl_values), 6),
                    "win_rate": round((len(wins) / len(rows)) if rows else 0.0, 6),
                    "profit_factor": round((sum(wins) / abs(sum(losses))) if losses else 0.0, 6),
                }
            )
        return results

    def _entry_exit_label_matrix(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
        for row in exits:
            key = (
                str(row.get("bucket") or ""),
                _entry_label(row),
                _exit_label(row),
                str(row.get("exit_reason") or ""),
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, entry_label, exit_label, exit_reason), group in sorted(grouped.items()):
            rows.append(
                {
                    "bucket": bucket,
                    "entry_analysis_label": entry_label,
                    "exit_analysis_label": exit_label,
                    "exit_reason": exit_reason,
                    **_closed_trade_stats(group),
                }
            )
        return rows

    def _stop_out_diagnostics(self) -> list[dict[str, object]]:
        exits = [
            row
            for row in self._trade_rows
            if row.get("event") == "EXIT" and row.get("exit_reason") == "stop_loss"
        ]
        grouped: dict[tuple[str, str, str, str, str], list[dict[str, object]]] = {}
        for row in exits:
            key = (
                str(row.get("bucket") or ""),
                _entry_label(row),
                _exit_label(row),
                _hold_bucket(_number(row.get("holding_minutes"))),
                _score_bucket(_number(row.get("analysis_score"))),
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, entry_label, exit_label, hold_bucket, score_bucket), group in sorted(
            grouped.items()
        ):
            stats = _closed_trade_stats(group)
            rows.append(
                {
                    "bucket": bucket,
                    "entry_analysis_label": entry_label,
                    "exit_analysis_label": exit_label,
                    "hold_bucket": hold_bucket,
                    "entry_score_bucket": score_bucket,
                    "stop_loss_count": len(group),
                    "quick_stop_3m_count": sum(
                        1 for row in group if _number(row.get("holding_minutes")) <= 3.0
                    ),
                    "capacity_limited_count": sum(1 for row in group if _truthy(row.get("capacity_limited"))),
                    "avg_fill_slippage_pct": _avg(
                        _number(row.get("fill_slippage_pct"))
                        for row in group
                        if row.get("fill_slippage_pct") not in (None, "")
                    ),
                    "avg_participation_slippage_pct": _avg(
                        _number(row.get("participation_slippage_pct"))
                        for row in group
                        if row.get("participation_slippage_pct") not in (None, "")
                    ),
                    **stats,
                }
            )
        return rows

    def _symbol_loss_concentration(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        bucket_loss_totals: dict[str, float] = {}
        for row in exits:
            bucket = str(row.get("bucket") or "")
            grouped.setdefault((bucket, str(row.get("symbol") or "")), []).append(row)
            net_pnl = _number(row.get("net_pnl"))
            if net_pnl < 0:
                bucket_loss_totals[bucket] = bucket_loss_totals.get(bucket, 0.0) + abs(net_pnl)

        rows: list[dict[str, object]] = []
        for (bucket, symbol), group in grouped.items():
            pnl_values = [_number(row.get("net_pnl")) for row in group]
            loss_abs_pnl = abs(sum(value for value in pnl_values if value < 0))
            bucket_loss_abs_pnl = bucket_loss_totals.get(bucket, 0.0)
            entry_labels = [_entry_label(row) for row in group]
            exit_labels = [_exit_label(row) for row in group]
            rows.append(
                {
                    "bucket": bucket,
                    "symbol": symbol,
                    **_closed_trade_stats(group),
                    "stop_loss_count": sum(1 for row in group if row.get("exit_reason") == "stop_loss"),
                    "quick_stop_3m_count": sum(
                        1
                        for row in group
                        if row.get("exit_reason") == "stop_loss"
                        and _number(row.get("holding_minutes")) <= 3.0
                    ),
                    "worst_trade_net_pnl": round(min(pnl_values), 6) if pnl_values else 0.0,
                    "best_trade_net_pnl": round(max(pnl_values), 6) if pnl_values else 0.0,
                    "dominant_entry_analysis_label": _mode(entry_labels),
                    "dominant_exit_analysis_label": _mode(exit_labels),
                    "loss_abs_pnl": round(loss_abs_pnl, 6),
                    "loss_share_of_bucket_losses": round(
                        loss_abs_pnl / bucket_loss_abs_pnl if bucket_loss_abs_pnl else 0.0,
                        6,
                    ),
                }
            )
        rows.sort(
            key=lambda row: (
                str(row["bucket"]),
                _number(row["total_net_pnl"]),
                str(row["symbol"]),
            )
        )
        return rows

    def _hold_bucket_kpis(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in exits:
            hold_bucket = _hold_bucket(_number(row.get("holding_minutes")))
            grouped.setdefault((str(row.get("bucket") or ""), hold_bucket), []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, hold_bucket), group in sorted(grouped.items()):
            rows.append(
                {
                    "bucket": bucket,
                    "hold_bucket": hold_bucket,
                    **_closed_trade_stats(group),
                    "stop_loss_count": sum(1 for row in group if row.get("exit_reason") == "stop_loss"),
                    "session_end_count": sum(1 for row in group if row.get("exit_reason") == "session_end"),
                    "capacity_limited_count": sum(1 for row in group if _truthy(row.get("capacity_limited"))),
                }
            )
        return rows

    def _exit_path_diagnostics(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
        for row in exits:
            key = (
                str(row.get("bucket") or ""),
                _entry_label(row),
                str(row.get("exit_reason") or ""),
                _hold_bucket(_number(row.get("holding_minutes"))),
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, entry_label, exit_reason, hold_bucket), group in sorted(grouped.items()):
            session_end_rows = [row for row in group if row.get("exit_reason") == "session_end"]
            reached_0_5r_count = sum(1 for row in group if _truthy(row.get("reached_0_5r")))
            reached_1r_count = sum(1 for row in group if _truthy(row.get("reached_1r")))
            intrabar_stop_touched_count = sum(
                1 for row in group if _truthy(row.get("intrabar_stop_touched"))
            )
            breakeven_stop_active_count = sum(
                1 for row in group if _truthy(row.get("breakeven_stop_active"))
            )
            rows.append(
                {
                    "bucket": bucket,
                    "entry_analysis_label": entry_label,
                    "exit_reason": exit_reason,
                    "hold_bucket": hold_bucket,
                    **_closed_trade_stats(group),
                    "avg_mfe_pct_close": _avg(
                        _number(row.get("mfe_pct_close"))
                        for row in group
                        if row.get("mfe_pct_close") not in (None, "")
                    ),
                    "avg_mae_pct_close": _avg(
                        _number(row.get("mae_pct_close"))
                        for row in group
                        if row.get("mae_pct_close") not in (None, "")
                    ),
                    "avg_mfe_pct_intrabar": _avg(
                        _number(row.get("mfe_pct_intrabar"))
                        for row in group
                        if row.get("mfe_pct_intrabar") not in (None, "")
                    ),
                    "avg_mae_pct_intrabar": _avg(
                        _number(row.get("mae_pct_intrabar"))
                        for row in group
                        if row.get("mae_pct_intrabar") not in (None, "")
                    ),
                    "avg_max_r_multiple": _avg(
                        _number(row.get("max_r_multiple"))
                        for row in group
                        if row.get("max_r_multiple") not in (None, "")
                    ),
                    "avg_min_r_multiple": _avg(
                        _number(row.get("min_r_multiple"))
                        for row in group
                        if row.get("min_r_multiple") not in (None, "")
                    ),
                    "avg_max_close_r": _avg(
                        _number(row.get("max_close_r"))
                        for row in group
                        if row.get("max_close_r") not in (None, "")
                    ),
                    "avg_min_close_r": _avg(
                        _number(row.get("min_close_r"))
                        for row in group
                        if row.get("min_close_r") not in (None, "")
                    ),
                    "avg_first_1r_minutes": _avg(
                        _number(row.get("first_1r_minutes"))
                        for row in group
                        if row.get("first_1r_minutes") not in (None, "")
                    ),
                    "avg_breakeven_stop_activated_minutes": _avg(
                        _number(row.get("breakeven_stop_activated_minutes"))
                        for row in group
                        if row.get("breakeven_stop_activated_minutes") not in (None, "")
                    ),
                    "avg_giveback_from_mfe_pct": _avg(
                        _number(row.get("giveback_from_mfe_pct"))
                        for row in group
                        if row.get("giveback_from_mfe_pct") not in (None, "")
                    ),
                    "reached_0_5r_count": reached_0_5r_count,
                    "reached_0_5r_rate": round(reached_0_5r_count / len(group), 6) if group else 0.0,
                    "reached_1r_count": reached_1r_count,
                    "reached_1r_rate": round(reached_1r_count / len(group), 6) if group else 0.0,
                    "stop_after_1r_count": sum(
                        1
                        for row in group
                        if row.get("exit_reason") == "stop_loss" and _truthy(row.get("reached_1r"))
                    ),
                    "intrabar_stop_touched_count": intrabar_stop_touched_count,
                    "intrabar_stop_touched_rate": round(intrabar_stop_touched_count / len(group), 6)
                    if group
                    else 0.0,
                    "breakeven_stop_active_count": breakeven_stop_active_count,
                    "breakeven_stop_active_rate": round(breakeven_stop_active_count / len(group), 6)
                    if group
                    else 0.0,
                    "session_end_winner_count": sum(
                        1 for row in session_end_rows if _number(row.get("net_pnl")) > 0
                    ),
                    "session_end_loser_count": sum(
                        1 for row in session_end_rows if _number(row.get("net_pnl")) < 0
                    ),
                }
            )
        return rows

    def _setup_state_kpis(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for row in exits:
            key = (
                str(row.get("bucket") or ""),
                str(row.get("entry_setup_state") or ""),
                str(row.get("entry_action_bias") or ""),
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, setup_state, action_bias), group in sorted(grouped.items()):
            rows.append(
                {
                    "bucket": bucket,
                    "entry_setup_state": setup_state,
                    "entry_action_bias": action_bias,
                    **_closed_trade_stats(group),
                    "stop_loss_count": sum(1 for row in group if row.get("exit_reason") == "stop_loss"),
                    "label_exit_count": sum(1 for row in group if row.get("exit_reason") == "label_exit"),
                    "session_end_count": sum(1 for row in group if row.get("exit_reason") == "session_end"),
                    "quick_stop_3m_count": sum(
                        1
                        for row in group
                        if row.get("exit_reason") == "stop_loss"
                        and _number(row.get("holding_minutes")) <= 3.0
                    ),
                    "avg_entry_setup_quality": _avg(
                        _number(row.get("entry_setup_quality"))
                        for row in group
                        if row.get("entry_setup_quality") not in (None, "")
                    ),
                    "avg_entry_setup_risk": _avg(
                        _number(row.get("entry_setup_risk"))
                        for row in group
                        if row.get("entry_setup_risk") not in (None, "")
                    ),
                    "avg_exit_setup_risk": _avg(
                        _number(row.get("exit_setup_risk"))
                        for row in group
                        if row.get("exit_setup_risk") not in (None, "")
                    ),
                    "avg_exit_setup_quality": _avg(
                        _number(row.get("exit_setup_quality"))
                        for row in group
                        if row.get("exit_setup_quality") not in (None, "")
                    ),
                    "avg_max_r_multiple": _avg(
                        _number(row.get("max_r_multiple"))
                        for row in group
                        if row.get("max_r_multiple") not in (None, "")
                    ),
                    "avg_min_r_multiple": _avg(
                        _number(row.get("min_r_multiple"))
                        for row in group
                        if row.get("min_r_multiple") not in (None, "")
                    ),
                    "reached_1r_count": sum(1 for row in group if _truthy(row.get("reached_1r"))),
                    "reached_1r_rate": round(
                        sum(1 for row in group if _truthy(row.get("reached_1r"))) / len(group), 6
                    ) if group else 0.0,
                    "stop_after_1r_count": sum(
                        1 for row in group
                        if row.get("exit_reason") == "stop_loss" and _truthy(row.get("reached_1r"))
                    ),
                    "stop_after_1r_rate": round(
                        sum(
                            1 for row in group
                            if row.get("exit_reason") == "stop_loss" and _truthy(row.get("reached_1r"))
                        ) / len(group), 6
                    ) if group else 0.0,
                }
            )
        return rows

    def _setup_transition_matrix(self) -> list[dict[str, object]]:
        exits = [row for row in self._trade_rows if row.get("event") == "EXIT"]
        grouped: dict[tuple[str, str, str, str, str], list[dict[str, object]]] = {}
        for row in exits:
            key = (
                str(row.get("bucket") or ""),
                str(row.get("entry_setup_state") or ""),
                str(row.get("exit_setup_state") or ""),
                str(row.get("exit_reason") or ""),
                _hold_bucket(_number(row.get("holding_minutes"))),
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, entry_state, exit_state, exit_reason, hold_bucket), group in sorted(grouped.items()):
            rows.append(
                {
                    "bucket": bucket,
                    "entry_setup_state": entry_state,
                    "exit_setup_state": exit_state,
                    "exit_reason": exit_reason,
                    "hold_bucket": hold_bucket,
                    **_closed_trade_stats(group),
                    "entry_action_bias": _mode(str(row.get("entry_action_bias") or "") for row in group),
                    "exit_action_bias": _mode(str(row.get("exit_action_bias") or "") for row in group),
                    "stop_loss_count": sum(1 for row in group if row.get("exit_reason") == "stop_loss"),
                }
            )
        return rows

    def _add_trim_runner_diagnostics(self) -> list[dict[str, object]]:
        grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
        for row in self._setup_feature_rows:
            action_bias = str(row.get("action_bias") or "")
            if action_bias not in {"starter_ok", "add_ok", "trim", "exit", "watch"}:
                continue
            key = (
                str(row.get("bucket") or ""),
                str(row.get("position_state") or ""),
                str(row.get("setup_state") or ""),
                action_bias,
            )
            grouped.setdefault(key, []).append(row)

        rows: list[dict[str, object]] = []
        for (bucket, position_state, setup_state, action_bias), group in sorted(grouped.items()):
            rows.append(
                {
                    "bucket": bucket,
                    "position_state": position_state,
                    "setup_state": setup_state,
                    "action_bias": action_bias,
                    "observation_count": len(group),
                    "unique_symbol_count": len({str(row.get("symbol") or "") for row in group}),
                    "avg_quality": _avg(_number(row.get("quality")) for row in group),
                    "avg_risk": _avg(_number(row.get("risk")) for row in group),
                    "avg_distance_to_vwap_pct": _avg(
                        _number(row.get("distance_to_vwap_pct"))
                        for row in group
                        if row.get("distance_to_vwap_pct") not in (None, "")
                    ),
                    "avg_volume_bar_ratio": _avg(
                        _number(row.get("volume_bar_ratio"))
                        for row in group
                        if row.get("volume_bar_ratio") not in (None, "")
                    ),
                    "true_leader_observation_count": sum(
                        1 for row in group if _truthy(row.get("true_leader"))
                    ),
                    "failed_breakout_observation_count": sum(
                        1 for row in group if _truthy(row.get("failed_breakout"))
                    ),
                }
            )
        return rows

    def _conclusion_status(
        self,
        *,
        completed: list[str],
        skipped: list[dict[str, object]],
        failed: list[dict[str, object]],
    ) -> str:
        if failed and not completed:
            return "failed"
        if len(completed) <= 10:
            return "smoke_only" if completed else "insufficient_data"
        if skipped or self._coverage_warnings:
            return "insufficient_data"
        return "comparable"

    def _export_predictions(self) -> None:
        _write_csv(self.export_dir / "premkt_predictions_replay.csv", self._prediction_rows)

    def _export_trades_and_kpis(
        self,
        summary: dict[str, object],
        *,
        replay_grade: ReplayGrade,
    ) -> None:
        _write_csv(self.export_dir / "paper_trade_log.csv", self._trade_rows)
        bucket_rows = [
            {"bucket": bucket, **values}
            for bucket, values in dict(summary["bucket_results"]).items()
        ]
        comment = csv_decision_grade_comment(replay_grade)
        _write_csv(self.export_dir / "paper_backtest_kpis.csv", bucket_rows, header_comment=comment)
        _write_csv(
            self.export_dir / "paper_entry_label_kpis.csv",
            list(summary.get("entry_label_results", [])),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_entry_exit_label_matrix.csv",
            self._entry_exit_label_matrix(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_stop_out_diagnostics.csv",
            self._stop_out_diagnostics(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_symbol_loss_concentration.csv",
            self._symbol_loss_concentration(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_hold_bucket_kpis.csv",
            self._hold_bucket_kpis(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_exit_path_diagnostics.csv",
            self._exit_path_diagnostics(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_setup_features.csv",
            self._setup_feature_rows,
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_setup_state_kpis.csv",
            self._setup_state_kpis(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_setup_transition_matrix.csv",
            self._setup_transition_matrix(),
            header_comment=comment,
        )
        _write_csv(
            self.export_dir / "paper_add_trim_runner_diagnostics.csv",
            self._add_trim_runner_diagnostics(),
            header_comment=comment,
        )
        pair_rows = [
            {"left_bucket": left, "right_bucket": right, **_pair_diff(dict(summary["bucket_results"]), left, right)}
            for left, right in (
                (PREDICTOR_WEIGHTED_BUCKET, MOMENTUM_ONLY_BUCKET),
                (MOMENTUM_ONLY_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
                (PREDICTOR_WEIGHTED_BUCKET, WATCHLIST_BLIND_MOMENTUM_BUCKET),
            )
        ]
        _write_csv(self.export_dir / "paper_bucket_pair_diff.csv", pair_rows, header_comment=comment)
        _write_csv(
            self.export_dir / "paper_bucket_trade_diff.csv",
            [
                {
                    "left_bucket": PREDICTOR_WEIGHTED_BUCKET,
                    "right_bucket": MOMENTUM_ONLY_BUCKET,
                    **_pair_diff(dict(summary["bucket_results"]), PREDICTOR_WEIGHTED_BUCKET, MOMENTUM_ONLY_BUCKET),
                }
            ],
            header_comment=comment,
        )
        self._write_json(
            self.export_dir / "data_quality_warnings.json",
            {
                "coverage_warnings": self._coverage_warnings,
                "data_quality_notes": sorted(set(self._data_quality_notes)),
            },
        )

    def _prediction_to_row(
        self,
        market_date: str,
        scan_id: str,
        prediction: PremktPrediction,
    ) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "market_date": market_date,
            "scan_id": scan_id,
            "symbol": prediction.symbol,
            "score": prediction.score,
            "rule_score": prediction.rule_score,
            "ml_score": prediction.ml_score,
            "final_score": prediction.final_score,
            "score_mode": prediction.score_mode,
            "ml_weight": self.options.ml_weight,
            "model_path": prediction.model_path,
            "model_feature_version": prediction.model_feature_version,
            "model_missing_feature_count": prediction.model_missing_feature_count,
            "model_missing_features": ", ".join(prediction.model_missing_features),
            "scoring_notes": ", ".join(prediction.scoring_notes),
            "generated_at": prediction.generated_at.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat() if prediction.cutoff_at else "",
            "entry_rationale": prediction.entry_rationale,
        }

    def _cutoff_at(self, market_date: str) -> datetime:
        return datetime.combine(date.fromisoformat(market_date), self.options.cutoff_time, tzinfo=EASTERN)

    def _resolve_export_dir(self, export_dir: Path | None) -> Path:
        if export_dir is not None:
            return Path(export_dir)
        timestamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        return Path("data/backtest_lab/replays") / timestamp

    def _load_or_create_progress(self, dates: list[str]) -> dict[str, Any]:
        if self.options.resume and self._progress_path.exists():
            with self._progress_path.open("r", encoding="utf-8") as handle:
                progress = json.load(handle)
            progress.setdefault("dates", {})
            for day in dates:
                progress["dates"].setdefault(day, {"status": "pending"})
            return progress
        return {
            "run_id": self.run_id,
            "created_at": _utc_now_iso(),
            "dates": {day: {"status": "pending"} for day in dates},
        }

    def _load_existing_outputs(self) -> None:
        self._prediction_rows = _read_csv(self.export_dir / "premkt_predictions_replay.csv")
        self._trade_rows = _read_csv(self.export_dir / "paper_trade_log.csv")
        self._setup_feature_rows = _read_csv(self.export_dir / "paper_setup_features.csv")
        warnings_path = self.export_dir / "data_quality_warnings.json"
        if warnings_path.exists():
            try:
                existing = json.loads(warnings_path.read_text(encoding="utf-8"))
                self._coverage_warnings = list(existing.get("coverage_warnings", []))
                self._data_quality_notes = list(existing.get("data_quality_notes", []))
            except (json.JSONDecodeError, OSError):
                pass

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    def _apply_ranks(self, rows: list[MarketActivity]) -> None:
        pct_sorted = sorted(
            rows,
            key=lambda row: (-(row.pct_change or float("-inf")), row.symbol),
        )
        for index, row in enumerate(pct_sorted, start=1):
            row.pct_rank = index
        volume_sorted = sorted(
            rows,
            key=lambda row: (-(row.volume or float("-inf")), -(row.dollar_volume or 0.0), row.symbol),
        )
        for index, row in enumerate(volume_sorted, start=1):
            row.volume_rank = index


def validate_replay_paths(
    *,
    db_path: Path,
    export_dir: Path,
    allow_live_db: bool = False,
) -> None:
    resolved_db = Path(db_path).resolve()
    live_db = LIVE_DB.resolve()
    if resolved_db == live_db and not allow_live_db:
        raise ReplaySafetyError(
            "Refusing to use live DB data/penny_stock_radar.sqlite3 for historical replay. "
            "Use data/backtest_lab/penny_stock_radar.sqlite3 or pass --allow-live-db explicitly."
        )
    resolved_export = Path(export_dir).resolve()
    paper_dir = PAPER_EXPORT_DIR.resolve()
    if resolved_export == paper_dir or paper_dir in resolved_export.parents:
        raise ReplaySafetyError(
            "Refusing to write replay outputs under sample_outputs/paper_trading/."
        )


def _watchlist_entry_from_row(row: Any) -> WatchlistEntry:
    return WatchlistEntry(
        symbol=str(row["symbol"]).upper(),
        total_score=float(row["total_score"] or 0.0),
        catalyst_score=float(row["catalyst_score"] or 0.0),
        technical_score=float(row["technical_score"] or 0.0),
        sympathy_score=float(row["sympathy_score"] or 0.0),
        market_context_score=float(row["market_context_score"] or 0.0),
        low_float_bonus=float(row["low_float_bonus"] or 0.0),
        social_score=float(row["social_score"] or 0.0),
        themes=_json_list(row["themes"]),
        reasons=_json_list(row["reasons"]),
    )


def _json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _date_range(start_date: str, end_date: str) -> Iterable[date]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _predictor_weight(score: float | None) -> float:
    if score is None or score <= 40.0:
        return 0.0
    if score >= 80.0:
        return 1.0
    return (score - 40.0) / 40.0


def _pair_diff(
    bucket_results: dict[str, Any],
    left: str,
    right: str,
) -> dict[str, object]:
    left_row = bucket_results.get(left, {})
    right_row = bucket_results.get(right, {})
    return {
        "left_trade_count": int(left_row.get("trade_count", 0) or 0),
        "right_trade_count": int(right_row.get("trade_count", 0) or 0),
        "trade_count_diff": int(left_row.get("trade_count", 0) or 0) - int(right_row.get("trade_count", 0) or 0),
        "net_pnl_diff": round(float(left_row.get("total_net_pnl", 0.0) or 0.0) - float(right_row.get("total_net_pnl", 0.0) or 0.0), 6),
    }


def _closed_trade_stats(rows: list[dict[str, object]]) -> dict[str, object]:
    pnl_values = [_number(row.get("net_pnl")) for row in rows]
    pct_values = [
        _number(row.get("realized_pnl_pct"))
        for row in rows
        if row.get("realized_pnl_pct") not in (None, "")
    ]
    hold_values = [
        _number(row.get("holding_minutes"))
        for row in rows
        if row.get("holding_minutes") not in (None, "")
    ]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    return {
        "trade_count": len(rows),
        "total_net_pnl": round(sum(pnl_values), 6),
        "gross_profit": round(sum(wins), 6),
        "gross_loss": round(sum(losses), 6),
        "win_rate": round((len(wins) / len(rows)) if rows else 0.0, 6),
        "profit_factor": round((sum(wins) / abs(sum(losses))) if losses else 0.0, 6),
        "avg_net_pnl": round((sum(pnl_values) / len(rows)) if rows else 0.0, 6),
        "avg_realized_pnl_pct": _avg(pct_values),
        "avg_holding_minutes": _avg(hold_values),
    }


def _position_path_metrics(
    position: _Position,
    *,
    exit_price: float,
    exit_reason: str,
) -> dict[str, object]:
    risk_per_share = position.entry_price - position.initial_stop_price
    mfe_pct_close = _price_move_pct(position.best_close_price, position.entry_price)
    mae_pct_close = _price_move_pct(position.worst_close_price, position.entry_price)
    mfe_pct_intrabar = _price_move_pct(position.best_intrabar_price, position.entry_price)
    mae_pct_intrabar = _price_move_pct(position.worst_intrabar_price, position.entry_price)
    max_r_multiple = (
        (position.best_intrabar_price - position.entry_price) / risk_per_share
        if risk_per_share > 0
        else 0.0
    )
    min_r_multiple = (
        (position.worst_intrabar_price - position.entry_price) / risk_per_share
        if risk_per_share > 0
        else 0.0
    )
    max_close_r = (
        (position.best_close_price - position.entry_price) / risk_per_share
        if risk_per_share > 0
        else 0.0
    )
    min_close_r = (
        (position.worst_close_price - position.entry_price) / risk_per_share
        if risk_per_share > 0
        else 0.0
    )
    giveback_from_mfe_pct = (
        ((position.best_intrabar_price - exit_price) / position.best_intrabar_price) * 100.0
        if position.best_intrabar_price > 0
        else 0.0
    )
    first_1r_minutes = (
        (position.first_1r_at - position.opened_at).total_seconds() / 60.0
        if position.first_1r_at is not None
        else None
    )
    breakeven_stop_activated_minutes = (
        (position.breakeven_stop_activated_at - position.opened_at).total_seconds() / 60.0
        if position.breakeven_stop_activated_at is not None
        else None
    )
    return {
        "entry_stop_price": round(position.initial_stop_price, 6),
        "initial_stop_price": round(position.initial_stop_price, 6),
        "final_stop_price": round(position.stop_price, 6),
        "risk_per_share": round(risk_per_share, 6),
        "mfe_close_per_share": round(position.best_close_price - position.entry_price, 6),
        "mae_close_per_share": round(position.worst_close_price - position.entry_price, 6),
        "mfe_intrabar_per_share": round(position.best_intrabar_price - position.entry_price, 6),
        "mae_intrabar_per_share": round(position.worst_intrabar_price - position.entry_price, 6),
        "mfe_pct_close": round(mfe_pct_close, 6),
        "mae_pct_close": round(mae_pct_close, 6),
        "mfe_pct_intrabar": round(mfe_pct_intrabar, 6),
        "mae_pct_intrabar": round(mae_pct_intrabar, 6),
        "max_close_r": round(max_close_r, 6),
        "min_close_r": round(min_close_r, 6),
        "max_r_multiple": round(max_r_multiple, 6),
        "min_r_multiple": round(min_r_multiple, 6),
        "reached_0_5r": int(position.first_0_5r_at is not None),
        "reached_1r": int(position.first_1r_at is not None),
        "first_1r_minutes": _round_optional(first_1r_minutes),
        "intrabar_stop_touched": int(position.intrabar_stop_touched),
        "intrabar_stop_first_at": (
            position.intrabar_stop_first_at.isoformat()
            if position.intrabar_stop_first_at is not None
            else ""
        ),
        "stop_trigger_basis": _stop_trigger_basis(position, exit_reason=exit_reason),
        "giveback_from_mfe_pct": round(max(giveback_from_mfe_pct, 0.0), 6),
        "breakeven_stop_active": int(position.breakeven_stop_active),
        "breakeven_stop_after_r": _round_optional(position.breakeven_stop_after_r),
        "breakeven_stop_activated_at": (
            position.breakeven_stop_activated_at.isoformat()
            if position.breakeven_stop_activated_at is not None
            else ""
        ),
        "breakeven_stop_activated_minutes": _round_optional(breakeven_stop_activated_minutes),
    }


def _stop_trigger_basis(position: _Position, *, exit_reason: str) -> str:
    if exit_reason == "stop_loss":
        return position.stop_trigger
    if position.intrabar_stop_touched:
        return "intrabar_low_only"
    return "none"


def _price_move_pct(price: float, entry_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((price - entry_price) / entry_price) * 100.0


def _entry_label(row: dict[str, object]) -> str:
    return str(row.get("entry_analysis_label") or row.get("analysis_label") or "")


def _exit_label(row: dict[str, object]) -> str:
    return str(row.get("exit_analysis_label") or "")


def _hold_bucket(holding_minutes: float) -> str:
    if holding_minutes <= 3.0:
        return "00_0_3m"
    if holding_minutes <= 10.0:
        return "01_3_10m"
    if holding_minutes <= 30.0:
        return "02_10_30m"
    if holding_minutes <= 120.0:
        return "03_30_120m"
    return "04_2h_plus"


def _score_bucket(score: float) -> str:
    if score <= 0.0:
        return "00_missing_or_zero"
    if score < 2.75:
        return "01_lt_2_75"
    if score < 3.5:
        return "02_2_75_3_5"
    if score < 4.5:
        return "03_3_5_4_5"
    return "04_4_5_plus"


def _avg(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 0.0


def _mode(values: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _fill_metrics(fill: Any) -> dict[str, object]:
    return {
        "fill_reference_price": _round_optional(fill.fill_reference_price),
        "fill_slippage_pct": _round_optional(fill.fill_slippage_pct),
        "participation_slippage_pct": _round_optional(fill.participation_slippage_pct),
        "bar_volume": _round_optional(fill.bar_volume),
        "bar_dollar_volume": _round_optional(fill.bar_dollar_volume),
        "shares_pct_of_bar_volume": _round_optional(fill.shares_pct_of_bar_volume),
        "notional_pct_of_bar_dollar_volume": _round_optional(fill.notional_pct_of_bar_dollar_volume),
        "estimated_capacity_at_1pct_volume": _round_optional(fill.estimated_capacity_at_1pct_volume),
        "estimated_capacity_at_2pct_volume": _round_optional(fill.estimated_capacity_at_2pct_volume),
        "capacity_limited": int(fill.capacity_limited),
    }


def _setup_prefixed_fields(
    judgement: SetupJudgement | None,
    prefix: str,
) -> dict[str, object]:
    if judgement is None:
        return {
            f"{prefix}_setup_state": "",
            f"{prefix}_setup_quality": "",
            f"{prefix}_setup_risk": "",
            f"{prefix}_action_bias": "",
            f"{prefix}_setup_confidence": "",
            f"{prefix}_setup_reasons": "",
        }
    return judgement.prefixed_fields(prefix)


def _round_optional(value: object, digits: int = 6) -> object:
    if value is None:
        return ""
    return round(float(value), digits)


def _write_csv(
    path: Path,
    rows: list[dict[str, object]],
    *,
    header_comment: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen_fieldnames: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen_fieldnames:
                fieldnames.append(key)
                seen_fieldnames.add(key)
    if not fieldnames:
        path.write_text((header_comment + "\n") if header_comment else "", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        if header_comment:
            handle.write(header_comment + "\n")
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, object]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(iter_non_comment_lines(handle))]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            check=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None
