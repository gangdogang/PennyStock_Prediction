from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
import time
from zoneinfo import ZoneInfo

import httpx
import typer
from rich.table import Table

from ..config import resolve_mito_ohlcv_1m_path
from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_premkt_predictions,
    fetch_latest_watchlist,
    get_connection,
    insert_setup_alerts,
)
from ..services.backtest_data import BacktestDataManager
from ..services.benchmark_suite import BenchmarkSuiteOptions, BenchmarkSuiteRunner
from ..services.multiday_catalyst_replay import (
    CatalystEntryRule,
    CatalystExitRule,
    MultidayCatalystReplayRunner,
)
from ..services.kis_historical import KISHistoricalDataService
from ..services.kis_websocket_rotation import (
    KisWebSocketRotationManager,
    RotationPolicy,
)
from ..services.kis_quote_consolidation_check import (
    evaluate_kis_quote_consolidation,
    write_consolidation_verdict,
)
from ..services.alpaca_historical import (
    ALPACA_DIAGNOSTIC_WARNING,
    AlpacaHistoricalDataService,
    build_quote_windows,
    build_quote_windows_from_strategy_run_dir,
    build_quote_windows_from_trade_log,
)
from ..services.ibkr_historical import (
    IBKR_NBBO_SOURCE,
    IbkrHistoricalConfig,
    backfill_ibkr_historical_quotes,
    ibkr_backfill_summary_to_dict,
)
from ..services.coverage_shortfall import estimate_shortfall, shortfall_report_to_dict
from ..services.nasdaq_symbol_directory import NasdaqSymbolDirectoryArchiver
from ..services.sec_edgar_pit import (
    SecEdgarPitBackfillOptions,
    SecEdgarPitBackfillService,
)
from ..services.premkt_entry_signal_audit import (
    DEFAULT_SIGNAL_ENTRY_LABELS,
    DEFAULT_SIGNAL_SETUP_STATES,
    PremktEntrySignalAuditor,
)
from ..services.setup_alerts import (
    DEFAULT_SETUP_ALERT_ROOT,
    SetupAlertBus,
    write_setup_alert_exports,
)
from ..services.market_activity import MarketActivityScanner
from ..services.falsification_research import (
    FalsificationAuditOptions,
    FalsificationResearchAuditor,
)
from ..services.finra_otc_daily_list import (
    FinraOTCDailyListImporter,
    records_to_corporate_actions_hook,
)
from ..services.filing_catalyst_tier_audit import (
    FilingCatalystTierAuditError,
    FilingCatalystTierAuditOptions,
    FilingCatalystTierAuditor,
)
from ..services.hf_1m_bars import (
    DEFAULT_AUDIT_ROOT as DEFAULT_HF_1M_BARS_AUDIT_ROOT,
    HfCandidateDayOptions,
    HfCandidateDaySegmenter,
    HfCandidateEventOptions,
    HfCandidateEventSegmenter,
    Hf1mBarsAuditError,
    Hf1mBarsAuditOptions,
    Hf1mBarsAuditor,
)
from ..services.hf_candidate_event_robustness import (
    DEFAULT_HF_CANDIDATE_EVENT_ROBUSTNESS_ROOT,
    DEFAULT_HF_CANDIDATE_EVENT_ROOT,
    HfCandidateEventRobustnessAuditor,
    HfCandidateEventRobustnessError,
    HfCandidateEventRobustnessOptions,
)
from ..services.hf_event_random_benchmark import (
    DEFAULT_HF_EVENT_RANDOM_BREAKDOWN_ROOT,
    DEFAULT_HF_EVENT_RANDOM_BENCHMARK_ROOT,
    HfEventRandomBenchmarkBreakdownAuditor,
    HfEventRandomBenchmarkBreakdownOptions,
    HfEventRandomBenchmarkError,
    HfEventRandomBenchmarkOptions,
    HfEventRandomBenchmarkRunner,
)
from ..services.point_in_time_universe_audit import (
    PointInTimeUniverseAuditOptions,
    PointInTimeUniverseAuditor,
)
from ..services.premkt_historical_replay import (
    DEFAULT_ENTRY_LABELS,
    DEFAULT_REPLAY_DB,
    PremktHistoricalReplayRunner,
    ReplayOptions,
    ReplaySafetyError,
)
from ..services.premkt_replay_validation import PremktReplayValidationEvaluator
from ..services.premkt_model_training import PremktModelTrainer
from ..services.premkt_training_dataset import PremktTrainingDatasetBuilder
from ..services.research_data_coverage import (
    ResearchDataCoverageAuditor,
    ResearchDataCoverageOptions,
)
from ..services.universe_tradability_audit import audit_universe, write_report
from .common import console, format_optional_number, format_optional_percent, trade_call_label

app = typer.Typer()
EASTERN = ZoneInfo("America/New_York")
MITO_DATASET_NAME = "mito0o852_OHLCV_1m"
MITO_AUDIT_ROOT = Path("data/backtest_lab/audits/mito_ohlcv_1m")
MITO_CANDIDATE_EVENT_ROOT = Path("data/backtest_lab/candidate_events/mito_ohlcv_1m")
MITO_CANDIDATE_EVENT_ROBUSTNESS_ROOT = Path(
    "data/backtest_lab/candidate_event_robustness/mito_ohlcv_1m"
)
MITO_EVENT_RANDOM_BENCHMARK_ROOT = Path(
    "data/backtest_lab/event_random_benchmarks/mito_ohlcv_1m"
)
MITO_EVENT_RANDOM_BREAKDOWN_ROOT = Path(
    "data/backtest_lab/event_random_breakdowns/mito_ohlcv_1m"
)


def _normalize_label_options(
    values: list[str] | None,
    *,
    default: tuple[str, ...] = DEFAULT_ENTRY_LABELS,
) -> tuple[str, ...]:
    if values is None:
        return default
    labels: list[str] = []
    for value in values:
        for part in str(value).split(","):
            normalized = part.strip().upper()
            if normalized and normalized not in labels:
                labels.append(normalized)
    return tuple(labels)


def _parse_optional_time(value: str | None, option_name: str):
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{option_name} must be in HH:MM format.") from exc


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed


def _normalize_entry_setup_states(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    states: list[str] = []
    for part in value.split(","):
        normalized = part.strip().upper()
        if normalized and normalized not in states:
            states.append(normalized)
    return tuple(states) if states else None


def _normalize_optional_csv_filter(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    values: list[str] = []
    for part in value.split(","):
        normalized = part.strip().upper()
        if normalized == "ALL":
            return None
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _resolve_mito_input_path(input_root: Path | None) -> Path:
    return resolve_mito_ohlcv_1m_path(input_root).path


def _normalize_symbol_list(
    values: list[str] | None,
    *,
    symbols_file: Path | None = None,
    symbol_limit: int | None = None,
) -> list[str]:
    symbols: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            normalized = part.strip().upper()
            if normalized and normalized not in symbols:
                symbols.append(normalized)
    if symbols_file is not None:
        for line in symbols_file.read_text(encoding="utf-8").splitlines():
            normalized = line.strip().split(",", 1)[0].strip().upper()
            if normalized and not normalized.startswith("#") and normalized not in symbols:
                symbols.append(normalized)
    if symbol_limit is not None:
        return symbols[:symbol_limit]
    return symbols


def _date_strings(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("--end-date must be greater than or equal to --start-date.")
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _write_run_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _alpaca_window_payload(window) -> dict[str, object]:
    return {
        "symbols": list(window.symbols),
        "market_date": window.market_date,
        "start_at": window.start_at.isoformat(),
        "end_at": window.end_at.isoformat(),
    }


def _insert_corporate_action_rows(db_path: Path, rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    created_at = datetime.now().isoformat()
    before_count: int
    after_count: int
    with get_connection(db_path) as connection:
        before_count = int(
            connection.execute("SELECT COUNT(*) AS count FROM corporate_actions").fetchone()[
                "count"
            ]
            or 0
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO corporate_actions (
                source,
                source_record_id,
                action_category,
                action_subtype,
                symbol,
                old_symbol,
                new_symbol,
                old_name,
                new_name,
                effective_date,
                event_code,
                event_reason,
                raw_payload,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.get("source"),
                    row.get("source_record_id"),
                    row.get("action_category"),
                    row.get("action_subtype"),
                    row.get("symbol"),
                    row.get("old_symbol"),
                    row.get("new_symbol"),
                    row.get("old_name"),
                    row.get("new_name"),
                    row.get("effective_date"),
                    row.get("event_code"),
                    row.get("event_reason"),
                    json.dumps(row.get("raw_row") or {}, ensure_ascii=True, sort_keys=True),
                    created_at,
                )
                for row in rows
            ],
        )
        after_count = int(
            connection.execute("SELECT COUNT(*) AS count FROM corporate_actions").fetchone()[
                "count"
            ]
            or 0
        )
    return max(0, after_count - before_count)


def _resolve_l1_capture_symbols(
    settings,
    *,
    symbol: list[str] | None,
    limit: int,
) -> list[str]:
    symbols = [value.upper() for value in (symbol or [])]
    if symbols:
        return symbols[:limit]

    watchlist_rows = fetch_latest_watchlist(
        settings.database_path,
        limit=limit,
        prefer_reportable=False,
    )
    return [str(row["symbol"]).upper() for row in watchlist_rows]


def _rotation_priority_data(
    symbols: list[str],
    *,
    priority_metric: str,
) -> dict[str, float]:
    normalized = priority_metric.strip().lower()
    if normalized not in {"dollar_volume", "watchlist_rank", "volatility"}:
        raise ValueError(
            "--rotation-priority-metric must be dollar_volume, watchlist_rank, or volatility."
        )
    return {symbol: float(len(symbols) - index) for index, symbol in enumerate(symbols)}


@app.command("run-premkt-predictor")
def run_premkt_predictor(
    limit: int | None = typer.Option(
        None,
        help="Maximum prediction rows to persist and display.",
    ),
    lookback_hours: int | None = typer.Option(
        None,
        help="SEC filing lookback window in hours.",
    ),
    export_json: Path | None = typer.Option(
        None,
        help="Optional JSON path for premarket prediction output.",
    ),
    model_path: Path | None = typer.Option(
        None,
        "--model-path",
        help="Optional trained model artifact for ML scoring.",
    ),
    score_mode: str = typer.Option(
        "rule",
        "--score-mode",
        help="Scoring mode: rule, ml, or blend. Defaults to existing rule score.",
    ),
    ml_weight: float = typer.Option(
        0.5,
        "--ml-weight",
        min=0.0,
        max=1.0,
        help="ML score weight for score-mode=blend.",
    ),
) -> None:
    """Build and persist premarket predictions without placing any orders."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    predictor = root_cli.PremktPredictor(settings)
    try:
        predictions = predictor.run(
            limit=limit,
            lookback_hours=lookback_hours,
            output_path=export_json,
            model_path=model_path,
            score_mode=score_mode,
            ml_weight=ml_weight,
        )
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    if not predictions:
        console.print("No premarket predictions were produced. Build a universe first.")
        raise typer.Exit(code=1)

    console.print(f"Stored {len(predictions)} premarket predictions.")
    show_premkt_predictions(limit=len(predictions))


@app.command("show-premkt-predictions")
def show_premkt_predictions(limit: int = typer.Option(20, help="Rows to display.")) -> None:
    """Show the latest premarket prediction snapshot."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    rows = fetch_latest_premkt_predictions(
        settings.database_path,
        limit=limit,
        prefer_reportable=False,
    )
    if not rows:
        console.print("No premarket predictions found. Run `psradar run-premkt-predictor` first.")
        raise typer.Exit(code=1)

    table = Table(title="Latest Premarket Predictions")
    table.add_column("Symbol")
    table.add_column("Score")
    table.add_column("Max Hold")
    table.add_column("Themes")
    table.add_column("Rationale")
    for row in rows:
        table.add_row(
            row["symbol"],
            f"{float(row['score']):.2f}",
            str(int(row["max_hold_days"])),
            str(row["themes"]),
            str(row["entry_rationale"]),
        )
    console.print(table)


@app.command("build-premkt-training-dataset")
def build_premkt_training_dataset(
    start_date: str = typer.Option(..., help="Inclusive start date in YYYY-MM-DD."),
    end_date: str = typer.Option(..., help="Inclusive end date in YYYY-MM-DD."),
    output: Path = typer.Option(
        ...,
        "--output",
        help="CSV output path for point-in-time premarket feature/label rows.",
    ),
    db_path: Path = typer.Option(
        Path("data/backtest_lab/penny_stock_radar.sqlite3"),
        help="Historical minute-bar DB copy to read. Defaults to data/backtest_lab/.",
    ),
    cutoff_time: str = typer.Option(
        "08:00",
        help="Feature cutoff time in ET, HH:MM. Bars at or after this time are labels only.",
    ),
    label_risk_pct: float = typer.Option(
        5.0,
        min=1e-9,
        help="Fixed percent risk denominator for label_net_r.",
    ),
) -> None:
    """Build leakage-free premarket training rows from historical minute bars."""
    try:
        cutoff = datetime.strptime(cutoff_time, "%H:%M").time()
        builder = PremktTrainingDatasetBuilder(
            db_path,
            cutoff_time=cutoff,
            label_risk_pct=label_risk_pct,
        )
        summary = builder.build_csv(
            start_date=start_date,
            end_date=end_date,
            output_path=output,
        )
    except (FileNotFoundError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write training dataset: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Premarket training dataset wrote [bold]{summary.row_count}[/bold] rows "
        f"to [bold]{summary.output_path}[/bold]."
    )
    if summary.unlabeled_row_count:
        console.print(
            f"Rows without post-cutoff bars keep blank labels: {summary.unlabeled_row_count}."
        )


@app.command("train-premkt-model")
def train_premkt_model(
    dataset: Path = typer.Option(
        Path("data/ml/premkt_training.csv"),
        "--dataset",
        help="Premarket training CSV produced by build-premkt-training-dataset.",
    ),
    model_out: Path = typer.Option(
        Path("data/ml/premkt_model.joblib"),
        "--model-out",
        help="Path where the trained baseline model artifact is written.",
    ),
    metrics_out: Path = typer.Option(
        Path("data/ml/premkt_model_metrics.json"),
        "--metrics-out",
        help="Path where validation metrics JSON is written.",
    ),
    validation_start_date: str | None = typer.Option(
        None,
        "--validation-start-date",
        help="Optional YYYY-MM-DD first validation market_date. Defaults to last 20% of dates.",
    ),
) -> None:
    """Train a baseline premarket winner classifier from the training CSV."""
    try:
        result = PremktModelTrainer().train(
            dataset_path=dataset,
            model_out=model_out,
            metrics_out=metrics_out,
            validation_start_date=validation_start_date,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write model training outputs: {exc}")
        raise typer.Exit(code=1) from exc

    metrics = result.metrics
    console.print(
        f"Trained [bold]{metrics['model_type']}[/bold] on "
        f"[bold]{metrics['train_row_count']}[/bold] rows; validated on "
        f"[bold]{metrics['validation_row_count']}[/bold] rows."
    )
    console.print(f"Model: [bold]{result.model_path}[/bold]")
    console.print(f"Metrics: [bold]{result.metrics_path}[/bold]")


@app.command("run-premkt-model-replay")
def run_premkt_model_replay(
    start_date: str = typer.Option(..., help="Inclusive replay start date in YYYY-MM-DD."),
    end_date: str = typer.Option(..., help="Inclusive replay end date in YYYY-MM-DD."),
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical replay DB copy. Defaults to data/backtest_lab/.",
    ),
    model_path: Path | None = typer.Option(
        Path("data/ml/premkt_model.joblib"),
        "--model-path",
        help="Optional trained model artifact for replay scoring.",
    ),
    score_mode: str = typer.Option(
        "blend",
        "--score-mode",
        help="Scoring mode: rule, ml, or blend.",
    ),
    ml_weight: float = typer.Option(
        0.5,
        "--ml-weight",
        min=0.0,
        max=1.0,
        help="ML score weight for score-mode=blend.",
    ),
    export_dir: Path | None = typer.Option(
        None,
        "--export-dir",
        help="Replay output directory. Defaults to data/backtest_lab/replays/<timestamp>.",
    ),
    cutoff_time: str = typer.Option(
        "08:00",
        "--cutoff-time",
        help="Prediction cutoff time in ET, HH:MM.",
    ),
    max_runtime_seconds: float | None = typer.Option(
        None,
        "--max-runtime-seconds",
        min=0.0,
        help="Stop before starting another date after this many seconds.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume an existing export directory and skip completed dates.",
    ),
    allow_live_db: bool = typer.Option(
        False,
        "--allow-live-db",
        help="Explicitly allow data/penny_stock_radar.sqlite3. Off by default.",
    ),
    entry_label: list[str] | None = typer.Option(
        None,
        "--entry-label",
        help="Entry label to allow. Repeat for label ablation. Defaults to the replay baseline labels.",
    ),
    exclude_entry_label: list[str] | None = typer.Option(
        None,
        "--exclude-entry-label",
        help="Entry label to disable. Repeat to run label ablations.",
    ),
    require_l1_quotes_for_entries: bool = typer.Option(
        False,
        "--require-l1-quotes-for-entries",
        help="Skip replay entries when the bar lacks bid/ask quote data.",
    ),
    entry_score_upper_bound: float | None = typer.Option(
        None,
        "--entry-score-upper-bound",
        min=0.0,
        help="Replay-only ablation: skip entries with analysis_score at or above this value.",
    ),
    min_entry_time: str | None = typer.Option(
        None,
        "--min-entry-time",
        help="Replay-only ablation: skip entries before this ET time, HH:MM.",
    ),
    exit_label: list[str] | None = typer.Option(
        None,
        "--exit-label",
        help="Replay-only ablation: close open positions when this analysis label appears. Repeatable.",
    ),
    breakeven_stop_after_r: float | None = typer.Option(
        None,
        "--breakeven-stop-after-r",
        min=0.0,
        help="Replay-only ablation: move stop to entry after a close-based R multiple is reached.",
    ),
    max_entries_per_symbol_per_day: int | None = typer.Option(
        None,
        "--max-entries-per-symbol-per-day",
        min=1,
        help="Replay-only ablation: cap same-symbol entries per bucket per replay date.",
    ),
    cooldown_after_stop_minutes: float | None = typer.Option(
        None,
        "--cooldown-after-stop-minutes",
        min=0.0,
        help="Replay-only ablation: block same-symbol re-entry for this many minutes after a stop loss.",
    ),
    entry_setup_states: str | None = typer.Option(
        None,
        "--entry-setup-states",
        help="Comma-separated setup_state allowlist for entries, e.g. STARTER_VALID,ORB_BREAKOUT.",
    ),
    predictor_k1: float | None = typer.Option(
        None,
        "--predictor-k1",
        min=0.0,
        help="Override paper_predictor_weight_k1 for this replay.",
    ),
    predictor_k2: float | None = typer.Option(
        None,
        "--predictor-k2",
        min=0.0,
        help="Override paper_predictor_weight_k2 for this replay.",
    ),
) -> None:
    """Run a local-only point-in-time historical premarket model replay."""
    import penny_stock_radar.cli as root_cli

    try:
        cutoff = datetime.strptime(cutoff_time, "%H:%M").time()
        min_entry = _parse_optional_time(min_entry_time, "--min-entry-time")
        settings = root_cli.get_settings()
        settings_overrides = {}
        options_overrides = {}
        if predictor_k1 is not None:
            settings_overrides["paper_predictor_weight_k1"] = predictor_k1
        if predictor_k2 is not None:
            settings_overrides["paper_predictor_weight_k2"] = predictor_k2
        parsed_entry_setup_states = _normalize_entry_setup_states(entry_setup_states)
        if parsed_entry_setup_states is not None:
            options_overrides["require_entry_setup_states"] = parsed_entry_setup_states
        if settings_overrides:
            settings = settings.model_copy(update=settings_overrides)
        runner = PremktHistoricalReplayRunner(
            settings,
            options=ReplayOptions(
                start_date=start_date,
                end_date=end_date,
                db_path=db_path,
                model_path=model_path,
                score_mode=score_mode,
                ml_weight=ml_weight,
                export_dir=export_dir,
                cutoff_time=cutoff,
                max_runtime_seconds=max_runtime_seconds,
                resume=resume,
                allow_live_db=allow_live_db,
                entry_labels=_normalize_label_options(entry_label),
                exclude_entry_labels=_normalize_label_options(exclude_entry_label, default=()),
                require_l1_quotes_for_entries=require_l1_quotes_for_entries,
                entry_score_upper_bound=entry_score_upper_bound,
                min_entry_time=min_entry,
                exit_labels=_normalize_label_options(exit_label, default=()),
                breakeven_stop_after_r=breakeven_stop_after_r,
                max_entries_per_symbol_per_day=max_entries_per_symbol_per_day,
                cooldown_after_stop_minutes=cooldown_after_stop_minutes,
                **options_overrides,
            ),
        )
        result = runner.run()
    except (FileNotFoundError, ReplaySafetyError, ValueError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print(
        f"Premarket model replay wrote outputs to [bold]{result.export_dir}[/bold] "
        f"with conclusion_status=[bold]{result.conclusion_status}[/bold]."
    )
    console.print(f"Manifest: [bold]{result.run_manifest_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")


@app.command("evaluate-premkt-replay")
def evaluate_premkt_replay(
    calibration_dir: Path = typer.Option(
        ...,
        "--calibration-dir",
        help="One-month calibration replay output directory.",
    ),
    out_of_sample_dir: Path = typer.Option(
        ...,
        "--out-of-sample-dir",
        help="Three-month-or-longer fixed-parameter OOS replay output directory.",
    ),
    output: Path = typer.Option(
        Path("data/backtest_lab/replays/evaluation_report.json"),
        "--output",
        help="evaluation_report.json output path.",
    ),
) -> None:
    """Evaluate calibration/OOS premkt historical replay outputs for edge judgment."""
    try:
        result = PremktReplayValidationEvaluator().evaluate(
            calibration_dir=calibration_dir,
            out_of_sample_dir=out_of_sample_dir,
            output_path=output,
        )
    except OSError as exc:
        console.print(f"Failed to write evaluation report: {exc}")
        raise typer.Exit(code=1) from exc

    gate = result.report["decision_gate"]
    console.print(
        f"Premarket replay evaluation wrote [bold]{output}[/bold] "
        f"with decision_gate=[bold]{gate['status']}[/bold]."
    )
    for reason in gate.get("reasons", []):
        console.print(f"- {reason}")


@app.command("audit-premkt-entry-signal")
def audit_premkt_entry_signal(
    run_dir: list[Path] = typer.Option(
        ...,
        "--run-dir",
        help="Replay output directory. Repeat for April/May/June runs.",
    ),
    output: Path = typer.Option(
        Path("data/backtest_lab/replays/entry_signal_audit.json"),
        "--output",
        help="JSON audit report output path.",
    ),
    csv_dir: Path | None = typer.Option(
        None,
        "--csv-dir",
        help="Optional directory for audit CSV outputs.",
    ),
    entry_label: str = typer.Option(
        ",".join(DEFAULT_SIGNAL_ENTRY_LABELS),
        "--entry-label",
        help="Comma-separated entry label allowlist. Use ALL to disable this filter.",
    ),
    entry_setup_state: str = typer.Option(
        ",".join(DEFAULT_SIGNAL_SETUP_STATES),
        "--entry-setup-state",
        help="Comma-separated entry setup_state allowlist. Use ALL to disable this filter.",
    ),
    bucket: str | None = typer.Option(
        None,
        "--bucket",
        help="Comma-separated bucket allowlist, e.g. predictor_weighted,momentum_only.",
    ),
    top_n: int = typer.Option(
        5,
        "--top-n",
        min=1,
        help="Number of top symbols/dates to include in concentration stress tests.",
    ),
) -> None:
    """Audit replay entry-signal robustness, concentration, and 1R feature separation."""
    try:
        result = PremktEntrySignalAuditor().audit(
            run_dirs=run_dir,
            output_path=output,
            csv_dir=csv_dir,
            entry_labels=_normalize_optional_csv_filter(entry_label),
            entry_setup_states=_normalize_optional_csv_filter(entry_setup_state),
            buckets=_normalize_optional_csv_filter(bucket),
            top_n=top_n,
        )
    except OSError as exc:
        console.print(f"Failed to write entry signal audit: {exc}")
        raise typer.Exit(code=1) from exc

    report = result.report
    console.print(
        f"Entry signal audit wrote [bold]{output}[/bold] "
        f"for [bold]{report['selected_trade_count']}[/bold] matching closed trades."
    )
    if result.csv_paths:
        console.print(f"CSV outputs: [bold]{next(iter(result.csv_paths.values())).parent}[/bold]")
    for warning in report.get("warnings", [])[:8]:
        console.print(f"- {warning}")


@app.command("build-setup-alerts-from-features")
def build_setup_alerts_from_features(
    features_csv: Path = typer.Option(
        ...,
        "--features-csv",
        help="Replay paper_setup_features.csv path to convert into setup diagnostic alerts.",
    ),
    output_root: Path = typer.Option(
        DEFAULT_SETUP_ALERT_ROOT,
        "--output-root",
        help="Directory where setup alert exports are written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional alert run id. Defaults to the feature CSV run_id or timestamp.",
    ),
    database_path: Path | None = typer.Option(
        None,
        "--database-path",
        help="Optional SQLite DB path. When provided, alerts are persisted into setup_alerts.",
    ),
    replace_run: bool = typer.Option(
        False,
        "--replace-run",
        help="Delete existing setup_alerts rows for the same run id before inserting.",
    ),
) -> None:
    """Build setup-first diagnostic alerts from replay setup feature rows."""
    try:
        alerts = SetupAlertBus().build_from_feature_csv(features_csv, run_id=run_id)
        result = write_setup_alert_exports(alerts, output_root=output_root, run_id=run_id)
        if database_path is not None:
            insert_setup_alerts(database_path, result.alerts, replace_run=replace_run)
    except OSError as exc:
        console.print(f"Failed to build setup alerts: {exc}")
        raise typer.Exit(code=1) from exc

    summary = result.summary
    console.print(f"Setup alerts wrote [bold]{result.export_dir}[/bold].")
    console.print(f"CSV: [bold]{result.csv_path}[/bold]")
    console.print(f"JSON: [bold]{result.json_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(
        "Alert counts: "
        f"total={summary.get('alert_count', 0)} "
        f"states={summary.get('alert_state_counts', {})}"
    )
    console.print(
        "These alerts are diagnostic only: decision_grade=false, cost_grade=none."
    )


@app.command("backfill-alpaca-iex-quotes")
def backfill_alpaca_iex_quotes(
    strategy_run_dir: Path | None = typer.Option(
        None,
        "--strategy-run-dir",
        help="Replay output directory containing paper_trade_log.csv entry schedule.",
    ),
    strategy_trade_log: Path | None = typer.Option(
        None,
        "--strategy-trade-log",
        help="Explicit paper_trade_log.csv path for entry-schedule quote windows.",
    ),
    strategy_bucket: str | None = typer.Option(
        None,
        "--strategy-bucket",
        help="Optional bucket filter for strategy entry schedule.",
    ),
    market_date: str | None = typer.Option(
        None,
        "--market-date",
        help="Single market date in YYYY-MM-DD for general symbol backfill.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Inclusive start date in YYYY-MM-DD for general symbol backfill.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Inclusive end date in YYYY-MM-DD for general symbol backfill.",
    ),
    symbol: list[str] | None = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbol to request. Repeat or pass comma-separated values.",
    ),
    symbols_file: Path | None = typer.Option(
        None,
        "--symbols-file",
        help="Text/CSV file with one symbol per line or first CSV column.",
    ),
    symbol_limit: int | None = typer.Option(
        None,
        "--symbol-limit",
        min=1,
        help="Maximum symbols to request after normalization.",
    ),
    start_time: str = typer.Option(
        "09:25",
        "--start-time",
        help="ET start time for general backfill windows, HH:MM.",
    ),
    end_time: str = typer.Option(
        "10:00",
        "--end-time",
        help="ET end time for general backfill windows, HH:MM.",
    ),
    entry_window_before_minutes: float = typer.Option(
        5.0,
        "--entry-window-before-minutes",
        min=0.0,
        help="Minutes before each strategy entry to request.",
    ),
    entry_window_after_minutes: float = typer.Option(
        30.0,
        "--entry-window-after-minutes",
        min=0.0,
        help="Minutes after each strategy entry to request.",
    ),
    limit: int = typer.Option(
        10000,
        "--limit",
        min=1,
        max=10000,
        help="Alpaca historical quotes page limit.",
    ),
    max_pages: int = typer.Option(
        100,
        "--max-pages",
        min=1,
        help="Maximum pages per request window.",
    ),
    rate_limit_sleep: float = typer.Option(
        0.0,
        "--rate-limit-sleep",
        min=0.0,
        help="Seconds to sleep between request windows.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build windows and write summary without calling Alpaca or inserting rows.",
    ),
    export_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--export-root",
        help="Directory where the backfill summary run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to alpaca_iex_<timestamp>.",
    ),
) -> None:
    """Backfill Alpaca IEX historical quotes as diagnostic-only L1 rows."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    run_name = run_id or datetime.now().strftime("alpaca_iex_%Y%m%d_%H%M%S")
    export_dir = export_root / run_name
    export_dir.mkdir(parents=True, exist_ok=True)
    try:
        if strategy_run_dir is not None:
            windows = build_quote_windows_from_strategy_run_dir(
                strategy_run_dir,
                bucket=strategy_bucket,
                minutes_before=entry_window_before_minutes,
                minutes_after=entry_window_after_minutes,
                symbols_per_window=symbol_limit,
            )
            input_mode = "strategy_run_dir"
        elif strategy_trade_log is not None:
            windows = build_quote_windows_from_trade_log(
                strategy_trade_log,
                bucket=strategy_bucket,
                minutes_before=entry_window_before_minutes,
                minutes_after=entry_window_after_minutes,
                symbols_per_window=symbol_limit,
            )
            input_mode = "strategy_trade_log"
        else:
            symbols = _normalize_symbol_list(
                symbol,
                symbols_file=symbols_file,
                symbol_limit=symbol_limit,
            )
            if not symbols:
                raise ValueError("Provide --symbol/--symbols-file or a strategy trade log.")
            if market_date is not None:
                dates = [market_date]
            elif start_date is not None and end_date is not None:
                dates = _date_strings(start_date, end_date)
            else:
                raise ValueError("Provide --market-date or --start-date/--end-date.")
            windows = [
                window
                for current_date in dates
                for window in build_quote_windows(
                    symbols,
                    current_date,
                    start_time,
                    end_time,
                    symbols_per_window=symbol_limit,
                )
            ]
            input_mode = "general_symbol_date"
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    manifest = {
        "command": "backfill-alpaca-iex-quotes",
        "created_at": datetime.now().isoformat(),
        "input_mode": input_mode,
        "diagnostic_only": True,
        "source": "alpaca_iex_historical_quotes",
        "warning": ALPACA_DIAGNOSTIC_WARNING,
        "window_count": len(windows),
        "windows_preview": [_alpaca_window_payload(window) for window in windows[:20]],
        "dry_run": dry_run,
    }
    _write_run_json(export_dir / "run_manifest.json", manifest)
    if dry_run:
        summary_payload = {
            **manifest,
            "inserted_rows": 0,
            "requested_symbols": len({symbol for window in windows for symbol in window.symbols}),
        }
        _write_run_json(export_dir / "alpaca_iex_quote_summary.json", summary_payload)
        (export_dir / "alpaca_iex_quote_summary.md").write_text(
            "# Alpaca IEX Diagnostic Quote Backfill\n\n"
            f"- Dry run: true\n- Windows: {len(windows)}\n- Warning: {ALPACA_DIAGNOSTIC_WARNING}\n",
            encoding="utf-8",
        )
        console.print(f"Dry run wrote Alpaca IEX diagnostic summary to [bold]{export_dir}[/bold].")
        return

    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        console.print("Missing PENNY_STOCK_ALPACA_API_KEY or PENNY_STOCK_ALPACA_SECRET_KEY.")
        raise typer.Exit(code=1)

    root_cli.init_database(settings.database_path)
    service = AlpacaHistoricalDataService(settings)
    try:
        summaries = []
        for index, window in enumerate(windows):
            summaries.append(
                service.import_historical_quotes(
                    [window],
                    feed=settings.alpaca_market_data_feed,
                    limit=limit,
                    max_pages_per_window=max_pages,
                )
            )
            if rate_limit_sleep > 0 and index + 1 < len(windows):
                time.sleep(rate_limit_sleep)
    finally:
        service.close()
    summary_payload = {
        **manifest,
        "feed": settings.alpaca_market_data_feed,
        "requested_symbols": len({symbol for window in windows for symbol in window.symbols}),
        "inserted_rows": sum(summary.inserted_rows for summary in summaries),
        "rows": sum(summary.rows for summary in summaries),
        "pages": sum(summary.pages for summary in summaries),
        "skipped_rows": sum(summary.skipped_rows for summary in summaries),
        "duplicate_rows": sum(summary.duplicate_rows for summary in summaries),
        "summary_by_window": [asdict(summary) for summary in summaries],
    }
    _write_run_json(export_dir / "alpaca_iex_quote_summary.json", summary_payload)
    (export_dir / "alpaca_iex_quote_summary.md").write_text(
        "# Alpaca IEX Diagnostic Quote Backfill\n\n"
        f"- Inserted rows: {summary_payload['inserted_rows']}\n"
        f"- Requested symbols: {summary_payload['requested_symbols']}\n"
        f"- Pages: {summary_payload['pages']}\n"
        f"- Source: `alpaca_iex_historical_quotes`\n"
        f"- Warning: {ALPACA_DIAGNOSTIC_WARNING}\n",
        encoding="utf-8",
    )
    console.print(
        "Alpaca IEX diagnostic quotes inserted "
        f"[bold]{summary_payload['inserted_rows']}[/bold] rows. Summary: [bold]{export_dir}[/bold]"
    )
    console.print(ALPACA_DIAGNOSTIC_WARNING)


@app.command("backfill-ibkr-historical-quotes")
def backfill_ibkr_historical_quotes_command(
    symbols_file: Path = typer.Option(
        ...,
        "--symbols-file",
        help="Text/CSV file with one symbol per line or first CSV column.",
    ),
    market_date_start: str = typer.Option(
        ...,
        "--market-date-start",
        help="Inclusive market date start in YYYY-MM-DD.",
    ),
    market_date_end: str = typer.Option(
        ...,
        "--market-date-end",
        help="Inclusive market date end in YYYY-MM-DD.",
    ),
    paper_account: bool = typer.Option(
        True,
        "--paper-account/--live-account",
        help="Use IBKR paper gateway/TWS defaults. Historical calls are supported from paper sessions.",
    ),
    rate_limit_per_minute: int = typer.Option(
        60,
        "--rate-limit-per-minute",
        min=1,
        help="Maximum IBKR historical tick requests per rolling minute.",
    ),
    chunk_size_seconds: int = typer.Option(
        1800,
        "--chunk-size-seconds",
        min=1,
        help="Historical tick request chunk size in seconds.",
    ),
    reqhistoricalticks_count: int = typer.Option(
        1000,
        "--reqHistoricalTicks-count",
        min=1,
        max=1000,
        help="IBKR reqHistoricalTicks count per request.",
    ),
    out_summary: Path = typer.Option(
        ...,
        "--out-summary",
        help="JSON summary output path.",
    ),
) -> None:
    """Backfill IBKR historical NBBO bid/ask ticks into historical_l1_quotes."""
    import penny_stock_radar.cli as root_cli

    try:
        symbols = _normalize_symbol_list(None, symbols_file=symbols_file)
        if not symbols:
            raise ValueError("--symbols-file did not contain any symbols.")
        start = date.fromisoformat(market_date_start)
        end = date.fromisoformat(market_date_end)
        config = IbkrHistoricalConfig(
            paper_account=paper_account,
            rate_limit_per_minute=rate_limit_per_minute,
            chunk_size_seconds=chunk_size_seconds,
            reqHistoricalTicks_count=reqhistoricalticks_count,
        )
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    settings = root_cli.get_settings()
    try:
        summary = backfill_ibkr_historical_quotes(
            symbols=symbols,
            market_date_start=start,
            market_date_end=end,
            db_path=settings.database_path,
            config=config,
        )
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    payload = {
        "command": "backfill-ibkr-historical-quotes",
        "created_at": datetime.now().isoformat(),
        "source": IBKR_NBBO_SOURCE,
        "config": asdict(config),
        **ibkr_backfill_summary_to_dict(summary),
    }
    _write_run_json(out_summary, payload)
    console.print(
        "IBKR historical NBBO backfill inserted "
        f"[bold]{summary.rows_inserted}[/bold] rows for "
        f"[bold]{summary.requested_symbols}[/bold] symbols. Summary: [bold]{out_summary}[/bold]"
    )
    if summary.failed_chunks:
        console.print(f"Failed chunks: [bold]{len(summary.failed_chunks)}[/bold]")


@app.command("archive-nasdaq-symbol-directory")
def archive_nasdaq_symbol_directory(
    market_date: str | None = typer.Option(
        None,
        "--market-date",
        help="Forward PIT market date to tag. Defaults to current ET date.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Output directory. Defaults to data/backtest_lab/reference_snapshots/<run_id>.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print target paths without fetching or writing.",
    ),
    allow_current_date: bool = typer.Option(
        False,
        "--allow-current-date",
        help="Also write scan_runs/universe as forward PIT for the current/future date.",
    ),
) -> None:
    """Archive current Nasdaq Symbol Directory files for forward-only PIT use."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    run_name = f"nasdaq_symbol_directory_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    target_dir = output_dir or Path("data/backtest_lab/reference_snapshots") / run_name
    if dry_run:
        console.print(f"Would archive Nasdaq Symbol Directory to [bold]{target_dir}[/bold].")
        console.print("Forward PIT only; this does not backfill past replay dates.")
        return
    archiver = NasdaqSymbolDirectoryArchiver(target_dir)
    try:
        result = archiver.archive_current(
            db_path=settings.database_path,
            allow_current_date=allow_current_date,
            market_date=market_date,
        )
    except httpx.HTTPError as exc:
        console.print(f"Failed to fetch Nasdaq Symbol Directory: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"Archived [bold]{len(result.records)}[/bold] Nasdaq directory rows to "
        f"[bold]{result.output_dir}[/bold]."
    )
    console.print(f"Report: [bold]{result.report_path}[/bold]")
    if result.scan_id:
        console.print(f"Forward PIT scan_id: [bold]{result.scan_id}[/bold] date={result.market_date}")
    else:
        console.print("DB PIT write skipped. Pass --allow-current-date to write forward PIT rows.")


@app.command("backfill-sec-filings-pit")
def backfill_sec_filings_pit(
    start_date: str = typer.Option(..., "--start-date", help="Inclusive YYYY-MM-DD."),
    end_date: str | None = typer.Option(None, "--end-date", help="Inclusive YYYY-MM-DD."),
    symbol: list[str] | None = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbol to backfill. Repeat or pass comma-separated values.",
    ),
    symbols_file: Path | None = typer.Option(
        None,
        "--symbols-file",
        help="Optional symbol file with one symbol per line or first CSV column.",
    ),
    cik: list[str] | None = typer.Option(
        None,
        "--cik",
        help="CIK to backfill. Repeat or pass comma-separated values.",
    ),
    form: list[str] | None = typer.Option(
        None,
        "--form",
        help="SEC form allowlist. Repeat or pass comma-separated values.",
    ),
    cutoff_time: str = typer.Option(
        "08:00",
        "--cutoff-time",
        help="ET cutoff for D eligibility, HH:MM.",
    ),
    scan_id: str | None = typer.Option(
        None,
        "--scan-id",
        help="Optional existing/supplied PIT scan id. Single market date only.",
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--output-root",
        help="Directory where the SEC PIT backfill run is written.",
    ),
    run_id: str | None = typer.Option(None, "--run-id", help="Optional stable run id."),
    rate_limit_sleep: float = typer.Option(
        0.0,
        "--rate-limit-sleep",
        min=0.0,
        help="Accepted for runbook symmetry; SEC service still relies on provider-level pacing.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and report but do not write filings/scan rows.",
    ),
) -> None:
    """Backfill SEC EDGAR filings with acceptance-time PIT cutoff semantics."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    try:
        cutoff = _parse_optional_time(cutoff_time, "--cutoff-time")
        assert cutoff is not None
        symbols = tuple(_normalize_symbol_list(symbol, symbols_file=symbols_file))
        ciks = tuple(_normalize_symbol_list(cik))
        forms = tuple(_normalize_symbol_list(form)) or ("8-K", "S-1", "S-3", "424B5", "RW")
        if rate_limit_sleep > 0:
            console.print(
                "--rate-limit-sleep is recorded for operator intent; current SEC backfill "
                "uses the SEC provider request path without per-CIK sleep injection."
            )
        result = SecEdgarPitBackfillService(settings).run(
            SecEdgarPitBackfillOptions(
                db_path=settings.database_path,
                output_root=output_root,
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                ciks=ciks,
                forms=forms,
                scan_id=scan_id,
                write_database=not dry_run,
                cutoff_time=cutoff,
            )
        )
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(
        f"SEC EDGAR PIT backfill wrote report to [bold]{result.export_dir}[/bold]: "
        f"eligible={result.eligible_count} diagnostic={result.diagnostic_count} "
        f"written={result.written_count}."
    )
    console.print(f"Report: [bold]{result.report_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    if result.scan_ids_by_market_date:
        console.print(f"PIT scan ids: {json.dumps(result.scan_ids_by_market_date, sort_keys=True)}")


@app.command("backfill-finra-otc-daily-list")
def backfill_finra_otc_daily_list(
    input_csv: Path | None = typer.Option(
        None,
        "--input-csv",
        help="Optional FINRA OTC Daily List CSV to stage instead of calling the FINRA API.",
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--output-root",
        help="Directory where the FINRA staging run is written.",
    ),
    run_id: str | None = typer.Option(None, "--run-id", help="Optional stable run id."),
    limit: int = typer.Option(
        1000,
        "--limit",
        min=1,
        help="Maximum rows requested from FINRA API when --input-csv is not provided.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the target path without fetching or staging rows.",
    ),
    write_database: bool = typer.Option(
        False,
        "--write-database",
        help="Insert READY staging rows into the minimal corporate_actions inventory.",
    ),
) -> None:
    """Stage FINRA OTC Daily List corporate-action rows for survivorship audit."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    run_name = run_id or datetime.now().strftime("finra_otc_daily_list_%Y%m%d_%H%M%S")
    output_dir = output_root / run_name
    if dry_run:
        console.print(f"Would stage FINRA OTC Daily List output under [bold]{output_dir}[/bold].")
        console.print("No DB write; corporate-action coverage remains unresolved.")
        return

    importer = FinraOTCDailyListImporter(output_dir)
    try:
        if input_csv is not None:
            with input_csv.open(encoding="utf-8-sig", newline="") as handle:
                result = importer.stage_rows(csv.DictReader(handle))
        else:
            result = importer.fetch_and_stage(limit=limit)
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    inserted_rows = 0
    if write_database and not result.blocked:
        root_cli.init_database(settings.database_path)
        inserted_rows = _insert_corporate_action_rows(
            settings.database_path,
            records_to_corporate_actions_hook(result.records),
        )

    console.print(
        f"FINRA OTC Daily List staging status=[bold]{result.status}[/bold] "
        f"records=[bold]{len(result.records)}[/bold] output=[bold]{result.output_dir}[/bold]."
    )
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(f"Markdown: [bold]{result.markdown_path}[/bold]")
    if write_database:
        console.print(
            f"Inserted [bold]{inserted_rows}[/bold] corporate_actions rows. "
            "Current FINRA rows are not sufficient historical survivorship proof by themselves."
        )


@app.command("audit-research-data-coverage")
def audit_research_data_coverage(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to audit. Defaults to data/backtest_lab/.",
    ),
    export_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--export-root",
        help="Directory where the coverage audit run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to coverage_<timestamp>.",
    ),
    start_date: str | None = typer.Option(None, "--start-date", help="Optional YYYY-MM-DD."),
    end_date: str | None = typer.Option(None, "--end-date", help="Optional YYYY-MM-DD."),
    strategy_run_dir: Path | None = typer.Option(
        None,
        "--strategy-run-dir",
        help="Replay output directory containing paper_trade_log.csv.",
    ),
    strategy_trade_log: Path | None = typer.Option(
        None,
        "--strategy-trade-log",
        help="Explicit paper_trade_log.csv path for strategy date overlap.",
    ),
    strategy_bucket: str | None = typer.Option(
        None,
        "--strategy-bucket",
        help="Optional bucket filter for strategy entry schedule.",
    ),
    sec_cutoff_time: str = typer.Option(
        "08:00",
        "--sec-cutoff-time",
        help="SEC filing cutoff in ET, HH:MM.",
    ),
) -> None:
    """Write a coverage report before running the falsification audit."""
    try:
        cutoff = _parse_optional_time(sec_cutoff_time, "--sec-cutoff-time")
        assert cutoff is not None
        result = ResearchDataCoverageAuditor().run(
            ResearchDataCoverageOptions(
                db_path=db_path,
                output_root=export_root,
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                strategy_run_dir=strategy_run_dir,
                strategy_trade_log=strategy_trade_log,
                strategy_bucket=strategy_bucket,
                sec_cutoff_time=cutoff,
            )
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    summary = result.report.get("summary", {})
    blockers = summary.get("blockers", [])
    console.print(f"Coverage audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(f"CSV: [bold]{result.csv_path}[/bold]")
    if blockers:
        console.print("Blockers:")
        for blocker in blockers:
            console.print(f"- {blocker}")
    else:
        console.print("No coverage blockers in this report. Run falsification audit next.")


@app.command("audit-filing-catalyst-tiers")
def audit_filing_catalyst_tiers(
    db_path: Path | None = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Database containing filings table. Ignored when --filings-csv is supplied.",
    ),
    filings_csv: Path | None = typer.Option(
        None,
        "--filings-csv",
        help="Optional filings CSV, such as sec_edgar_pit_filings.csv.",
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/filing_catalyst_tiers"),
        "--output-root",
        help="Directory where filing catalyst tier audit outputs are written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to filing_catalyst_tiers_<timestamp>.",
    ),
    scan_id: str | None = typer.Option(
        None,
        "--scan-id",
        help="Optional filings scan_id when reading from database.",
    ),
    symbol: list[str] | None = typer.Option(
        None,
        "--symbol",
        help="Optional symbol filter. Repeat for multiple symbols.",
    ),
    min_total_filings: int = typer.Option(
        1,
        "--min-total-filings",
        min=1,
        help="Minimum filing rows required for inventory PASS.",
    ),
) -> None:
    """Classify SEC filing rows into catalyst tiers and dilution/trap buckets."""
    try:
        result = FilingCatalystTierAuditor().audit(
            FilingCatalystTierAuditOptions(
                db_path=db_path,
                filings_csv=filings_csv,
                output_root=output_root,
                run_id=run_id,
                scan_id=scan_id,
                symbols=tuple(symbol or ()),
                min_total_filings=min_total_filings,
            )
        )
    except (FilingCatalystTierAuditError, OSError, sqlite3.Error) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.summary.get("filing_catalyst_tier_gate", {})
    console.print(f"Filing catalyst tier audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"CSV: [bold]{result.classified_csv_path}[/bold]")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"filings={result.summary.get('filing_count', 0)} "
        f"symbols={result.summary.get('symbol_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-hf-1m-bars")
def audit_hf_1m_bars(
    parquet_path: Path | None = typer.Option(
        None,
        "--parquet-path",
        help=(
            "Optional parquet path. Otherwise resolves PSR_HF_STOCKS_1M_PATH, "
            "then PSR_DATA_ROOT, then the repo-local fallback."
        ),
    ),
    output_root: Path = typer.Option(
        DEFAULT_HF_1M_BARS_AUDIT_ROOT,
        "--output-root",
        help="Directory where the Hugging Face 1m bars audit run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
) -> None:
    """Audit the CryptoSpartan Hugging Face 1m OHLCV bars parquet."""
    try:
        result = Hf1mBarsAuditor().run(
            Hf1mBarsAuditOptions(
                parquet_path=parquet_path,
                output_root=output_root,
                run_id=run_id,
            )
        )
    except Hf1mBarsAuditError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    summary = result.summary
    console.print(f"HF 1m bars audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"rows={summary.get('row_count', 0)} "
        f"tickers={summary.get('ticker_count', 0)} "
        f"ohlc_failures={summary.get('ohlc_sanity_failure_count', 0)} "
        f"duplicates={summary.get('duplicate_ticker_timestamp_count', 0)} "
        "decision_grade=false cost_grade=none"
    )


@app.command("segment-hf-candidate-days")
def segment_hf_candidate_days(
    parquet_path: Path | None = typer.Option(
        None,
        "--parquet-path",
        help=(
            "Optional parquet path. Otherwise resolves PSR_HF_STOCKS_1M_PATH, "
            "then PSR_DATA_ROOT, then the repo-local fallback."
        ),
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/candidate_days/hf_cryptospartan_alpaca_bars_1m"),
        "--output-root",
        help="Directory where HF candidate-day segmentation output is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    min_price: float = typer.Option(0.25, "--min-price", help="Minimum regular open price."),
    max_price: float = typer.Option(10.0, "--max-price", help="Maximum regular open price."),
    min_regular_rows: int = typer.Option(
        120,
        "--min-regular-rows",
        min=1,
        help="Minimum regular-session 1m bars required for a usable ticker-day.",
    ),
    min_candidate_days: int = typer.Option(
        1000,
        "--min-candidate-days",
        min=1,
        help="Minimum gross candidate days required for segmentation PASS.",
    ),
    min_active_months: int = typer.Option(
        36,
        "--min-active-months",
        min=1,
        help="Minimum active candidate months required for segmentation PASS.",
    ),
    chunk_months: int = typer.Option(
        3,
        "--chunk-months",
        min=1,
        help="Process the parquet in date chunks to reduce memory pressure.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Optional inclusive ET start date, YYYY-MM-DD.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Optional inclusive ET end date, YYYY-MM-DD.",
    ),
) -> None:
    """Segment HF 1m bars into gross ticker-day candidates before setup backtests."""
    try:
        parsed_start = date.fromisoformat(start_date) if start_date is not None else None
        parsed_end = date.fromisoformat(end_date) if end_date is not None else None
        result = HfCandidateDaySegmenter().run(
            HfCandidateDayOptions(
                parquet_path=parquet_path,
                output_root=output_root,
                run_id=run_id,
                min_price=min_price,
                max_price=max_price,
                min_regular_rows=min_regular_rows,
                min_candidate_days=min_candidate_days,
                min_active_months=min_active_months,
                chunk_months=chunk_months,
                start_date=parsed_start,
                end_date=parsed_end,
            )
        )
    except Hf1mBarsAuditError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.summary.get("candidate_day_gate", {})
    console.print(f"HF candidate-day segmentation wrote [bold]{result.export_dir}[/bold].")
    console.print(f"CSV: [bold]{result.candidate_csv_path}[/bold]")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"candidate_days={result.summary.get('candidate_day_count', 0)} "
        f"active_months={result.summary.get('active_candidate_month_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("segment-hf-candidate-events")
def segment_hf_candidate_events(
    parquet_path: Path | None = typer.Option(
        None,
        "--parquet-path",
        help=(
            "Optional parquet path. Otherwise resolves PSR_HF_STOCKS_1M_PATH, "
            "then PSR_DATA_ROOT, then the repo-local fallback."
        ),
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/candidate_events/hf_cryptospartan_alpaca_bars_1m"),
        "--output-root",
        help="Directory where HF candidate-event segmentation output is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    event_time: list[str] | None = typer.Option(
        None,
        "--event-time",
        help="Event time in HH:MM ET. Repeat to override defaults: 09:45, 10:30, 14:00, 15:30.",
    ),
    forward_window: list[int] | None = typer.Option(
        None,
        "--forward-window",
        help="Forward outcome window in minutes. Repeat to override defaults: 30, 60, 120.",
    ),
    min_price: float = typer.Option(0.25, "--min-price", help="Minimum event price."),
    max_price: float = typer.Option(10.0, "--max-price", help="Maximum event price."),
    min_rows_to_event: int = typer.Option(
        5,
        "--min-rows-to-event",
        min=1,
        help="Minimum regular-session bars up to the event time.",
    ),
    max_event_staleness_minutes: int = typer.Option(
        2,
        "--max-event-staleness-minutes",
        min=0,
        help="Maximum minutes between the event time and the latest available bar.",
    ),
    min_event_dollar_volume: float = typer.Option(
        250_000.0,
        "--min-event-dollar-volume",
        help="Minimum cumulative dollar volume up to the event time.",
    ),
    min_event_move_pct: float = typer.Option(
        5.0,
        "--min-event-move-pct",
        help="Minimum high-from-open percent observed by the event time.",
    ),
    min_candidate_events: int = typer.Option(
        1000,
        "--min-candidate-events",
        min=1,
        help="Minimum gross candidate events required for segmentation PASS.",
    ),
    min_active_months: int = typer.Option(
        36,
        "--min-active-months",
        min=1,
        help="Minimum active candidate months required for segmentation PASS.",
    ),
    chunk_months: int = typer.Option(
        3,
        "--chunk-months",
        min=1,
        help="Process the parquet in date chunks to reduce memory pressure.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Optional inclusive ET start date, YYYY-MM-DD.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Optional inclusive ET end date, YYYY-MM-DD.",
    ),
) -> None:
    """Segment HF 1m bars into event-time gross candidates with forward outcomes."""
    try:
        parsed_start = date.fromisoformat(start_date) if start_date is not None else None
        parsed_end = date.fromisoformat(end_date) if end_date is not None else None
        result = HfCandidateEventSegmenter().run(
            HfCandidateEventOptions(
                parquet_path=parquet_path,
                output_root=output_root,
                run_id=run_id,
                min_price=min_price,
                max_price=max_price,
                min_rows_to_event=min_rows_to_event,
                max_event_staleness_minutes=max_event_staleness_minutes,
                min_event_dollar_volume=min_event_dollar_volume,
                min_event_move_pct=min_event_move_pct,
                min_candidate_events=min_candidate_events,
                min_active_months=min_active_months,
                event_times_et=tuple(event_time or ("09:45", "10:30", "14:00", "15:30")),
                forward_windows_minutes=tuple(forward_window or (30, 60, 120)),
                chunk_months=chunk_months,
                start_date=parsed_start,
                end_date=parsed_end,
            )
        )
    except (Hf1mBarsAuditError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.summary.get("candidate_event_gate", {})
    console.print(f"HF candidate-event segmentation wrote [bold]{result.export_dir}[/bold].")
    console.print(f"CSV: [bold]{result.event_csv_path}[/bold]")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"candidate_events={result.summary.get('candidate_event_count', 0)} "
        f"active_months={result.summary.get('active_candidate_month_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("run-hf-event-random-benchmark")
def run_hf_event_random_benchmark(
    parquet_path: Path | None = typer.Option(
        None,
        "--parquet-path",
        help=(
            "Optional parquet path. Otherwise resolves PSR_HF_STOCKS_1M_PATH, "
            "then PSR_DATA_ROOT, then the repo-local fallback."
        ),
    ),
    candidate_event_root: Path = typer.Option(
        DEFAULT_HF_CANDIDATE_EVENT_ROOT,
        "--candidate-event-root",
        help="candidate_events.csv file, or root containing yearly candidate-event run folders.",
    ),
    output_root: Path = typer.Option(
        DEFAULT_HF_EVENT_RANDOM_BENCHMARK_ROOT,
        "--output-root",
        help="Directory where random benchmark outputs are written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    seed: int = typer.Option(
        20260507,
        "--seed",
        help="Deterministic random seed for same ticker/day controls.",
    ),
    random_mode: list[str] | None = typer.Option(
        None,
        "--random-mode",
        help="Random mode to run. Repeat to override defaults: same_time_bucket, any_regular_time.",
    ),
    primary_random_mode: str = typer.Option(
        "same_time_bucket",
        "--primary-random-mode",
        help="Benchmark mode used for the gate.",
    ),
    primary_cohort: str = typer.Option(
        "ex_top_10",
        "--primary-cohort",
        help="Cohort used for the gate. Defaults to top10 ticker excluded events.",
    ),
    primary_window_minutes: int = typer.Option(
        120,
        "--primary-window-minutes",
        min=1,
        help="Forward return window used for the gate.",
    ),
    min_primary_sample_count: int = typer.Option(
        500,
        "--min-primary-sample-count",
        min=1,
        help="Minimum paired candidate/random samples required for the gate.",
    ),
    min_mean_edge_pct: float = typer.Option(
        0.10,
        "--min-mean-edge-pct",
        help="Minimum candidate minus random mean return edge in percentage points.",
    ),
    min_median_edge_pct: float = typer.Option(
        0.0,
        "--min-median-edge-pct",
        help="Minimum candidate minus random median return edge in percentage points.",
    ),
    min_win_rate_edge_pct: float = typer.Option(
        2.0,
        "--min-win-rate-edge-pct",
        help="Minimum candidate minus random win-rate edge in percentage points.",
    ),
    outcome_chunk_months: int = typer.Option(
        3,
        "--outcome-chunk-months",
        min=1,
        help="Month chunks used when joining random controls back to parquet bars.",
    ),
) -> None:
    """Compare HF candidate events with same ticker/day deterministic random times."""
    try:
        result = HfEventRandomBenchmarkRunner().run(
            HfEventRandomBenchmarkOptions(
                parquet_path=parquet_path,
                candidate_event_root=candidate_event_root,
                output_root=output_root,
                run_id=run_id,
                seed=seed,
                random_modes=tuple(random_mode or ("same_time_bucket", "any_regular_time")),
                primary_random_mode=primary_random_mode,
                primary_cohort=primary_cohort,
                primary_window_minutes=primary_window_minutes,
                min_primary_sample_count=min_primary_sample_count,
                min_mean_edge_pct=min_mean_edge_pct,
                min_median_edge_pct=min_median_edge_pct,
                min_win_rate_edge_pct=min_win_rate_edge_pct,
                random_outcome_chunk_months=outcome_chunk_months,
            )
        )
    except (HfEventRandomBenchmarkError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("benchmark_gate", {})
    console.print(f"HF event random benchmark wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"candidate_events={result.report.get('candidate_event_count', 0)} "
        f"random_controls={result.report.get('random_control_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-hf-event-random-benchmark-breakdown")
def audit_hf_event_random_benchmark_breakdown(
    candidate_event_root: Path = typer.Option(
        DEFAULT_HF_CANDIDATE_EVENT_ROOT,
        "--candidate-event-root",
        help="candidate_events.csv file, or root containing candidate-event run folders.",
    ),
    benchmark_run_dir: Path = typer.Option(
        DEFAULT_HF_EVENT_RANDOM_BENCHMARK_ROOT,
        "--benchmark-run-dir",
        help="Benchmark run directory containing random_events.csv, or root whose latest run is used.",
    ),
    output_root: Path = typer.Option(
        DEFAULT_HF_EVENT_RANDOM_BREAKDOWN_ROOT,
        "--output-root",
        help="Directory where random benchmark breakdown outputs are written.",
    ),
    run_id: str | None = typer.Option(None, "--run-id"),
    dimension: list[str] | None = typer.Option(
        None,
        "--dimension",
        help="Breakdown dimension. Repeat to override defaults: event_year, market_month, event_time_et, time_bucket.",
    ),
    primary_random_mode: str = typer.Option("same_time_bucket", "--primary-random-mode"),
    min_sample_count: int = typer.Option(500, "--min-sample-count", min=1),
    min_mean_edge_pct: float = typer.Option(0.10, "--min-mean-edge-pct"),
    min_median_edge_pct: float = typer.Option(0.0, "--min-median-edge-pct"),
    min_win_rate_edge_pct: float = typer.Option(2.0, "--min-win-rate-edge-pct"),
) -> None:
    """Break down HF candidate-vs-random outcomes by year/time/window pockets."""
    try:
        result = HfEventRandomBenchmarkBreakdownAuditor().audit(
            HfEventRandomBenchmarkBreakdownOptions(
                candidate_event_root=candidate_event_root,
                benchmark_run_dir=benchmark_run_dir,
                output_root=output_root,
                run_id=run_id,
                dimensions=tuple(
                    dimension or ("event_year", "market_month", "event_time_et", "time_bucket")
                ),
                primary_random_mode=primary_random_mode,
                min_sample_count=min_sample_count,
                min_mean_edge_pct=min_mean_edge_pct,
                min_median_edge_pct=min_median_edge_pct,
                min_win_rate_edge_pct=min_win_rate_edge_pct,
            )
        )
    except (HfEventRandomBenchmarkError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("breakdown_gate", {})
    console.print(f"HF event random benchmark breakdown wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"breakdown_rows={result.report.get('breakdown_row_count', 0)} "
        f"surviving_pockets={result.report.get('surviving_pocket_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-hf-candidate-event-robustness")
def audit_hf_candidate_event_robustness(
    input_root: Path = typer.Option(
        DEFAULT_HF_CANDIDATE_EVENT_ROOT,
        "--input-root",
        help="Directory containing yearly candidate-event run folders with candidate_events.csv.",
    ),
    output_root: Path = typer.Option(
        DEFAULT_HF_CANDIDATE_EVENT_ROBUSTNESS_ROOT,
        "--output-root",
        help="Directory where robustness audit outputs are written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    min_total_events: int = typer.Option(
        1000,
        "--min-total-events",
        min=1,
        help="Minimum total event rows required before robustness can pass.",
    ),
    min_active_years: int = typer.Option(
        3,
        "--min-active-years",
        min=1,
        help="Minimum active event years required before robustness can pass.",
    ),
    max_top10_ticker_pct: float = typer.Option(
        50.0,
        "--max-top10-ticker-pct",
        help="Maximum all-events concentration allowed in the top 10 tickers.",
    ),
    min_remaining_pct_after_top10: float = typer.Option(
        40.0,
        "--min-remaining-pct-after-top10",
        help="Minimum percent of events that must remain after removing top 10 tickers.",
    ),
    min_remaining_events_after_top10: int = typer.Option(
        500,
        "--min-remaining-events-after-top10",
        min=1,
        help="Minimum event count that must remain after removing top 10 tickers.",
    ),
    top_n: int = typer.Option(
        25,
        "--top-n",
        min=1,
        help="Number of top tickers to include in the report.",
    ),
) -> None:
    """Audit HF candidate-event robustness after top-ticker removal."""
    try:
        result = HfCandidateEventRobustnessAuditor().audit(
            HfCandidateEventRobustnessOptions(
                input_root=input_root,
                output_root=output_root,
                run_id=run_id,
                min_total_events=min_total_events,
                min_active_years=min_active_years,
                max_top10_ticker_pct=max_top10_ticker_pct,
                min_remaining_pct_after_top10=min_remaining_pct_after_top10,
                min_remaining_events_after_top10=min_remaining_events_after_top10,
                top_n=top_n,
            )
        )
    except (HfCandidateEventRobustnessError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("candidate_event_robustness_gate", {})
    console.print(f"HF candidate-event robustness audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"events={result.report.get('event_count', 0)} "
        f"active_years={result.report.get('active_year_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-mito-ohlcv-1m")
def audit_mito_ohlcv_1m(
    input_root: Path | None = typer.Option(
        None,
        "--input-root",
        help=(
            "Mito monthly parquet file or directory. Otherwise resolves "
            "PSR_MITO_OHLCV_1M_PATH, then PSR_DATA_ROOT."
        ),
    ),
    output_root: Path = typer.Option(
        MITO_AUDIT_ROOT,
        "--output-root",
        help="Directory where mito OHLCV audit output is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    top_ticker_count: int = typer.Option(
        50,
        "--top-ticker-count",
        min=1,
        help="Number of top tickers to include in the audit.",
    ),
) -> None:
    """Audit mito0o852/OHLCV-1m monthly parquet files as gross OHLCV data."""
    try:
        result = Hf1mBarsAuditor().run(
            Hf1mBarsAuditOptions(
                parquet_path=_resolve_mito_input_path(input_root),
                output_root=output_root,
                run_id=run_id,
                top_ticker_count=top_ticker_count,
                dataset_name=MITO_DATASET_NAME,
            )
        )
    except Hf1mBarsAuditError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print(f"Mito OHLCV 1m audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"rows={result.summary.get('row_count', 0)} "
        f"tickers={result.summary.get('ticker_count', 0)} "
        "decision_grade=false cost_grade=none"
    )


@app.command("segment-mito-candidate-events")
def segment_mito_candidate_events(
    input_root: Path | None = typer.Option(
        None,
        "--input-root",
        help=(
            "Mito monthly parquet file or directory. Otherwise resolves "
            "PSR_MITO_OHLCV_1M_PATH, then PSR_DATA_ROOT."
        ),
    ),
    output_root: Path = typer.Option(
        MITO_CANDIDATE_EVENT_ROOT,
        "--output-root",
        help="Directory where mito candidate-event segmentation output is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    event_time: list[str] | None = typer.Option(
        None,
        "--event-time",
        help="Event time in HH:MM ET. Repeat to override defaults: 09:45, 10:30, 14:00, 15:30.",
    ),
    forward_window: list[int] | None = typer.Option(
        None,
        "--forward-window",
        help="Forward outcome window in minutes. Repeat to override defaults: 30, 60, 120.",
    ),
    min_price: float = typer.Option(0.25, "--min-price", help="Minimum event price."),
    max_price: float = typer.Option(10.0, "--max-price", help="Maximum event price."),
    min_rows_to_event: int = typer.Option(
        5,
        "--min-rows-to-event",
        min=1,
        help="Minimum regular-session bars up to the event time.",
    ),
    min_event_dollar_volume: float = typer.Option(
        250_000.0,
        "--min-event-dollar-volume",
        help="Minimum cumulative dollar volume up to the event time.",
    ),
    min_event_move_pct: float = typer.Option(
        5.0,
        "--min-event-move-pct",
        help="Minimum high-from-open percent observed by the event time.",
    ),
    min_candidate_events: int = typer.Option(
        1000,
        "--min-candidate-events",
        min=1,
        help="Minimum gross candidate events required for segmentation PASS.",
    ),
    min_active_months: int = typer.Option(
        12,
        "--min-active-months",
        min=1,
        help="Minimum active candidate months required for segmentation PASS.",
    ),
    chunk_months: int = typer.Option(
        1,
        "--chunk-months",
        min=1,
        help="Process the parquet in date chunks to reduce memory pressure.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Optional inclusive ET start date, YYYY-MM-DD.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Optional inclusive ET end date, YYYY-MM-DD.",
    ),
) -> None:
    """Segment mito monthly OHLCV files into event-time gross candidates."""
    try:
        parsed_start = date.fromisoformat(start_date) if start_date is not None else None
        parsed_end = date.fromisoformat(end_date) if end_date is not None else None
        result = HfCandidateEventSegmenter().run(
            HfCandidateEventOptions(
                parquet_path=_resolve_mito_input_path(input_root),
                output_root=output_root,
                run_id=run_id,
                min_price=min_price,
                max_price=max_price,
                min_rows_to_event=min_rows_to_event,
                min_event_dollar_volume=min_event_dollar_volume,
                min_event_move_pct=min_event_move_pct,
                min_candidate_events=min_candidate_events,
                min_active_months=min_active_months,
                event_times_et=tuple(event_time or ("09:45", "10:30", "14:00", "15:30")),
                forward_windows_minutes=tuple(forward_window or (30, 60, 120)),
                chunk_months=chunk_months,
                start_date=parsed_start,
                end_date=parsed_end,
                dataset_name=MITO_DATASET_NAME,
            )
        )
    except (Hf1mBarsAuditError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.summary.get("candidate_event_gate", {})
    console.print(f"Mito candidate-event segmentation wrote [bold]{result.export_dir}[/bold].")
    console.print(f"CSV: [bold]{result.event_csv_path}[/bold]")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"candidate_events={result.summary.get('candidate_event_count', 0)} "
        f"active_months={result.summary.get('active_candidate_month_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-mito-candidate-event-robustness")
def audit_mito_candidate_event_robustness(
    input_root: Path = typer.Option(
        MITO_CANDIDATE_EVENT_ROOT,
        "--input-root",
        help="Directory containing mito candidate-event run folders with candidate_events.csv.",
    ),
    output_root: Path = typer.Option(
        MITO_CANDIDATE_EVENT_ROBUSTNESS_ROOT,
        "--output-root",
        help="Directory where mito robustness audit outputs are written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to a UTC timestamp.",
    ),
    min_total_events: int = typer.Option(1000, "--min-total-events", min=1),
    min_active_years: int = typer.Option(2, "--min-active-years", min=1),
    max_top10_ticker_pct: float = typer.Option(50.0, "--max-top10-ticker-pct"),
    min_remaining_pct_after_top10: float = typer.Option(
        40.0,
        "--min-remaining-pct-after-top10",
    ),
    min_remaining_events_after_top10: int = typer.Option(
        500,
        "--min-remaining-events-after-top10",
        min=1,
    ),
    top_n: int = typer.Option(25, "--top-n", min=1),
) -> None:
    """Audit mito candidate-event robustness after top-ticker removal."""
    try:
        result = HfCandidateEventRobustnessAuditor().audit(
            HfCandidateEventRobustnessOptions(
                input_root=input_root,
                output_root=output_root,
                run_id=run_id,
                min_total_events=min_total_events,
                min_active_years=min_active_years,
                max_top10_ticker_pct=max_top10_ticker_pct,
                min_remaining_pct_after_top10=min_remaining_pct_after_top10,
                min_remaining_events_after_top10=min_remaining_events_after_top10,
                top_n=top_n,
                dataset_name=MITO_DATASET_NAME,
            )
        )
    except (HfCandidateEventRobustnessError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("candidate_event_robustness_gate", {})
    console.print(f"Mito candidate-event robustness audit wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"events={result.report.get('event_count', 0)} "
        f"active_years={result.report.get('active_year_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("run-mito-event-random-benchmark")
def run_mito_event_random_benchmark(
    input_root: Path | None = typer.Option(
        None,
        "--input-root",
        help=(
            "Mito monthly parquet file or directory. Otherwise resolves "
            "PSR_MITO_OHLCV_1M_PATH, then PSR_DATA_ROOT."
        ),
    ),
    candidate_event_root: Path = typer.Option(
        MITO_CANDIDATE_EVENT_ROOT,
        "--candidate-event-root",
        help="candidate_events.csv file, or root containing mito candidate-event run folders.",
    ),
    output_root: Path = typer.Option(
        MITO_EVENT_RANDOM_BENCHMARK_ROOT,
        "--output-root",
        help="Directory where mito random benchmark outputs are written.",
    ),
    run_id: str | None = typer.Option(None, "--run-id"),
    seed: int = typer.Option(20260507, "--seed"),
    random_mode: list[str] | None = typer.Option(
        None,
        "--random-mode",
        help="Random mode to run. Repeat to override defaults: same_time_bucket, any_regular_time.",
    ),
    primary_random_mode: str = typer.Option("same_time_bucket", "--primary-random-mode"),
    primary_cohort: str = typer.Option("ex_top_10", "--primary-cohort"),
    primary_window_minutes: int = typer.Option(120, "--primary-window-minutes", min=1),
    min_primary_sample_count: int = typer.Option(500, "--min-primary-sample-count", min=1),
    min_mean_edge_pct: float = typer.Option(0.10, "--min-mean-edge-pct"),
    min_median_edge_pct: float = typer.Option(0.0, "--min-median-edge-pct"),
    min_win_rate_edge_pct: float = typer.Option(2.0, "--min-win-rate-edge-pct"),
    outcome_chunk_months: int = typer.Option(
        1,
        "--outcome-chunk-months",
        min=1,
        help="Month chunks used when joining random controls back to mito parquet bars.",
    ),
) -> None:
    """Compare mito candidate events with same ticker/day deterministic random times."""
    try:
        result = HfEventRandomBenchmarkRunner().run(
            HfEventRandomBenchmarkOptions(
                parquet_path=_resolve_mito_input_path(input_root),
                candidate_event_root=candidate_event_root,
                output_root=output_root,
                run_id=run_id,
                seed=seed,
                random_modes=tuple(random_mode or ("same_time_bucket", "any_regular_time")),
                primary_random_mode=primary_random_mode,
                primary_cohort=primary_cohort,
                primary_window_minutes=primary_window_minutes,
                min_primary_sample_count=min_primary_sample_count,
                min_mean_edge_pct=min_mean_edge_pct,
                min_median_edge_pct=min_median_edge_pct,
                min_win_rate_edge_pct=min_win_rate_edge_pct,
                dataset_name=MITO_DATASET_NAME,
                random_outcome_chunk_months=outcome_chunk_months,
            )
        )
    except (HfEventRandomBenchmarkError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("benchmark_gate", {})
    console.print(f"Mito event random benchmark wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"candidate_events={result.report.get('candidate_event_count', 0)} "
        f"random_controls={result.report.get('random_control_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("audit-mito-event-random-benchmark-breakdown")
def audit_mito_event_random_benchmark_breakdown(
    candidate_event_root: Path = typer.Option(
        MITO_CANDIDATE_EVENT_ROOT,
        "--candidate-event-root",
        help="candidate_events.csv file, or root containing mito candidate-event run folders.",
    ),
    benchmark_run_dir: Path = typer.Option(
        MITO_EVENT_RANDOM_BENCHMARK_ROOT,
        "--benchmark-run-dir",
        help="Mito benchmark run directory containing random_events.csv, or root whose latest run is used.",
    ),
    output_root: Path = typer.Option(
        MITO_EVENT_RANDOM_BREAKDOWN_ROOT,
        "--output-root",
        help="Directory where mito random benchmark breakdown outputs are written.",
    ),
    run_id: str | None = typer.Option(None, "--run-id"),
    dimension: list[str] | None = typer.Option(
        None,
        "--dimension",
        help="Breakdown dimension. Repeat to override defaults: event_year, market_month, event_time_et, time_bucket.",
    ),
    primary_random_mode: str = typer.Option("same_time_bucket", "--primary-random-mode"),
    min_sample_count: int = typer.Option(500, "--min-sample-count", min=1),
    min_mean_edge_pct: float = typer.Option(0.10, "--min-mean-edge-pct"),
    min_median_edge_pct: float = typer.Option(0.0, "--min-median-edge-pct"),
    min_win_rate_edge_pct: float = typer.Option(2.0, "--min-win-rate-edge-pct"),
) -> None:
    """Break down mito candidate-vs-random outcomes by year/time/window pockets."""
    try:
        result = HfEventRandomBenchmarkBreakdownAuditor().audit(
            HfEventRandomBenchmarkBreakdownOptions(
                candidate_event_root=candidate_event_root,
                benchmark_run_dir=benchmark_run_dir,
                output_root=output_root,
                run_id=run_id,
                dataset_name=MITO_DATASET_NAME,
                dimensions=tuple(
                    dimension or ("event_year", "market_month", "event_time_et", "time_bucket")
                ),
                primary_random_mode=primary_random_mode,
                min_sample_count=min_sample_count,
                min_mean_edge_pct=min_mean_edge_pct,
                min_median_edge_pct=min_median_edge_pct,
                min_win_rate_edge_pct=min_win_rate_edge_pct,
            )
        )
    except (HfEventRandomBenchmarkError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    gate = result.report.get("breakdown_gate", {})
    console.print(f"Mito event random benchmark breakdown wrote [bold]{result.export_dir}[/bold].")
    console.print(f"JSON: [bold]{result.summary_json_path}[/bold]")
    console.print(f"Markdown: [bold]{result.summary_md_path}[/bold]")
    console.print(
        "Summary: "
        f"breakdown_rows={result.report.get('breakdown_row_count', 0)} "
        f"surviving_pockets={result.report.get('surviving_pocket_count', 0)} "
        f"gate={gate.get('status')} "
        "decision_grade=false cost_grade=none"
    )
    for reason in gate.get("reasons", [])[:8]:
        console.print(f"- {reason}")


@app.command("report-coverage-shortfall")
def report_coverage_shortfall(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to quantify. Defaults to data/backtest_lab/.",
    ),
    target_minute_bars_months: float = typer.Option(
        6.0,
        "--target-minute-bars-months",
        help="Required historical minute-bar span in months.",
    ),
    target_cost_eligible_overlap_pct: float = typer.Option(
        80.0,
        "--target-cost-eligible-overlap-pct",
        help="Required percent of minute-bar dates with cost-eligible quote/spread overlap.",
    ),
    target_corporate_action_months: float = typer.Option(
        12.0,
        "--target-corporate-action-months",
        help="Required corporate-action inventory span in months.",
    ),
    vendor_quote_source: str | None = typer.Option(
        None,
        "--vendor-quote-source",
        help="Vendor/source label for purchased quote data. Used only for planning metadata.",
    ),
    vendor_quote_cost_per_month_usd: float | None = typer.Option(
        None,
        "--vendor-quote-cost-per-month-usd",
        help="Input vendor unit cost per month in USD. No unknown vendor prices are inferred.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional JSON output path, e.g. automation/state/shortfall/<run_id>.json.",
    ),
) -> None:
    """Quantify coverage blockers for operational planning."""
    target = {
        "target_minute_bars_months": target_minute_bars_months,
        "target_cost_eligible_overlap_pct": target_cost_eligible_overlap_pct,
        "target_corporate_action_months": target_corporate_action_months,
        "vendor_quote_source": vendor_quote_source,
        "vendor_quote_cost_per_month_usd": vendor_quote_cost_per_month_usd,
    }
    try:
        payload = shortfall_report_to_dict(estimate_shortfall(db_path, target))
        payload["db_path"] = str(db_path)
        payload["target"] = target
        if out is not None:
            _write_run_json(out, payload)
    except (OSError, ValueError, sqlite3.Error) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    blockers = payload.get("blockers", [])
    console.print(
        "Coverage shortfall "
        f"blockers={len(blockers)} archive_days={payload['total_calendar_days_archive_path']} "
        f"vendor_cost_usd={payload['total_cost_usd_vendor_path']}."
    )
    console.print(f"Stamp: {payload['planning_stamp']}")
    console.print(payload["recommendation"])
    if out is not None:
        console.print(f"Coverage shortfall JSON: {out}")


@app.command("audit-universe-tradability")
def audit_universe_tradability(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to audit. Defaults to data/backtest_lab/.",
    ),
    universe_source: str = typer.Option(
        ...,
        "--universe-source",
        help="Universe source: watchlist, replay_log, or scanner_universe.",
    ),
    replay_dir: Path | None = typer.Option(
        None,
        "--replay-dir",
        help="Replay output directory containing paper_trade_log.csv.",
    ),
    trade_log: Path | None = typer.Option(
        None,
        "--trade-log",
        help="Explicit replay paper_trade_log.csv path.",
    ),
    scan_id: str | None = typer.Option(
        None,
        "--scan-id",
        help="Optional scan_id for watchlist or scanner_universe sources.",
    ),
    out_dir: Path = typer.Option(
        Path("automation/state/tradability"),
        "--out-dir",
        help="Directory for JSON, CSV, and Markdown tradability artifacts.",
    ),
    enable_yfinance: bool = typer.Option(
        False,
        "--enable-yfinance/--no-enable-yfinance",
        help="Allow yfinance exchange lookup for unknown symbols. Disabled by default.",
    ),
    yfinance_cache_path: Path | None = typer.Option(
        None,
        "--yfinance-cache-path",
        help="Optional JSON cache path for yfinance exchange lookups.",
    ),
) -> None:
    """Audit whether a backtest universe is transferable to KIS tradable listings."""
    if universe_source not in {"watchlist", "replay_log", "scanner_universe"}:
        console.print("--universe-source must be one of watchlist, replay_log, scanner_universe.")
        raise typer.Exit(code=1)
    try:
        report = audit_universe(
            db_path,
            universe_source,  # type: ignore[arg-type]
            {
                "replay_dir": replay_dir,
                "trade_log": trade_log,
                "scan_id": scan_id,
                "enable_yfinance": enable_yfinance,
                "yfinance_cache_path": yfinance_cache_path,
            },
        )
        report_path = write_report(report, out_dir)
    except (OSError, ValueError, sqlite3.Error) as exc:
        console.print(f"Failed to audit universe tradability: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Universe tradability audit wrote [bold]{out_dir}[/bold].")
    console.print(f"Report: [bold]{report_path}[/bold]")
    console.print(
        "KIS tradable universe: "
        f"[bold]{report.tradable_count}/{report.total_symbols}[/bold] "
        f"({(report.tradable_count / report.total_symbols * 100.0) if report.total_symbols else 0.0:.2f}%)."
    )
    if report.decision_blocker:
        console.print("Blocker: universe_kis_untradable_pct_high")


@app.command("run-falsification-audit")
def run_falsification_audit(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to audit. Defaults to data/backtest_lab/.",
    ),
    export_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--export-root",
        help="Directory where a timestamped falsification audit run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to falsification_<timestamp>.",
    ),
    strategy_run_dir: Path | None = typer.Option(
        None,
        "--strategy-run-dir",
        help="Replay output directory containing paper_trade_log.csv for matched-entry null.",
    ),
    strategy_trade_log: Path | None = typer.Option(
        None,
        "--strategy-trade-log",
        help="Explicit paper_trade_log.csv path for matched-entry null.",
    ),
    strategy_bucket: str | None = typer.Option(
        None,
        "--strategy-bucket",
        help="Optional bucket filter for strategy entry schedule, e.g. predictor_weighted.",
    ),
    seed: int = typer.Option(
        20260505,
        "--seed",
        help="Deterministic random seed for the null baseline sample.",
    ),
    null_sample_count: int = typer.Option(
        1500,
        "--null-sample-count",
        min=1,
        help="Maximum same-universe random-time entries for the null baseline.",
    ),
    fixed_stop_pct: float = typer.Option(
        0.05,
        "--fixed-stop-pct",
        min=1e-9,
        help="Fixed stop distance for the baseline stop geometry, as a decimal ratio.",
    ),
    atr_window: int = typer.Option(
        14,
        "--atr-window",
        min=1,
        help="Prior bar count for ATR-normalized stop geometry.",
    ),
    structure_window: int = typer.Option(
        10,
        "--structure-window",
        min=1,
        help="Prior bar count for structure-stop geometry.",
    ),
    spread_sample_limit: int = typer.Option(
        10000,
        "--spread-sample-limit",
        min=1,
        help="Maximum deterministic spread rows to sample for cost audit.",
    ),
    max_pit_universe_staleness_days: int = typer.Option(
        0,
        "--max-pit-universe-staleness-days",
        min=0,
        help="Maximum allowed point-in-time universe staleness. Zero requires same market date.",
    ),
    live_loss_cap_usd: float = typer.Option(
        5000.0,
        "--live-loss-cap-usd",
        min=0.0,
        help="Research governance live loss cap to record before any future live phase.",
    ),
    hard_cap_months: int = typer.Option(
        9,
        "--hard-cap-months",
        min=1,
        help="Research governance hard time cap.",
    ),
    weekly_hour_cap: int = typer.Option(
        20,
        "--weekly-hour-cap",
        min=1,
        help="Research governance weekly cognitive/time budget.",
    ),
) -> None:
    """Write a falsification-first data/bias/cost/null benchmark audit."""
    try:
        result = FalsificationResearchAuditor().run(
            FalsificationAuditOptions(
                db_path=db_path,
                export_root=export_root,
                run_id=run_id,
                strategy_run_dir=strategy_run_dir,
                strategy_trade_log=strategy_trade_log,
                strategy_bucket=strategy_bucket,
                seed=seed,
                null_sample_count=null_sample_count,
                fixed_stop_pct=fixed_stop_pct,
                atr_window=atr_window,
                structure_window=structure_window,
                spread_sample_limit=spread_sample_limit,
                max_pit_universe_staleness_days=max_pit_universe_staleness_days,
                live_loss_cap_usd=live_loss_cap_usd,
                hard_cap_months=hard_cap_months,
                weekly_hour_cap=weekly_hour_cap,
            )
        )
    except sqlite3.Error as exc:
        console.print(f"Failed to audit research database: {exc}")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write falsification audit: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Falsification audit [bold]{result.status}[/bold] at [bold]{result.export_dir}[/bold]."
    )
    console.print(f"Report: [bold]{result.report_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(f"Null baseline CSV: [bold]{result.null_baseline_path}[/bold]")
    console.print(f"Matched random-entry CSV: [bold]{result.matched_random_entry_path}[/bold]")


@app.command("run-benchmark-suite")
def run_benchmark_suite(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to inspect before benchmark entry generation.",
    ),
    export_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--export-root",
        help="Directory where a timestamped benchmark suite run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to benchmark_suite_<timestamp>.",
    ),
    strategy_run_dir: Path | None = typer.Option(
        None,
        "--strategy-run-dir",
        help="Replay output directory containing paper_trade_log.csv for matched diagnostics.",
    ),
    strategy_trade_log: Path | None = typer.Option(
        None,
        "--strategy-trade-log",
        help="Explicit paper_trade_log.csv path for opposite-side and random-time matching.",
    ),
    strategy_bucket: str | None = typer.Option(
        None,
        "--strategy-bucket",
        help="Optional bucket filter for strategy entry schedule, e.g. predictor_weighted.",
    ),
    strategy_kpis_path: Path | None = typer.Option(
        None,
        "--strategy-kpis-path",
        help="Optional JSON file with strategy KPI values for incremental comparison.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Optional inclusive first market date in YYYY-MM-DD.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Optional inclusive last market date in YYYY-MM-DD.",
    ),
    seed: int = typer.Option(
        20260506,
        "--seed",
        help="Deterministic benchmark seed.",
    ),
    entries_per_date: int = typer.Option(
        1,
        "--entries-per-date",
        min=1,
        help="Random-entry samples to generate per market date once cost policy passes.",
    ),
    top_n: int = typer.Option(
        5,
        "--top-n",
        min=1,
        help="Naive top-gainer and volume-leader entries per market date.",
    ),
) -> None:
    """Generate the Phase 0 benchmark-suite report, or a blocked report if cost evidence is missing."""
    try:
        market_dates = (
            tuple(date.fromisoformat(value) for value in _date_strings(start_date, end_date))
            if start_date and end_date
            else ()
        )
        result = BenchmarkSuiteRunner().run(
            BenchmarkSuiteOptions(
                db_path=db_path,
                export_root=export_root,
                run_id=run_id,
                market_dates=market_dates,
                strategy_run_dir=strategy_run_dir,
                strategy_trade_log=strategy_trade_log,
                strategy_bucket=strategy_bucket,
                strategy_kpis_path=strategy_kpis_path,
                seed=seed,
                entries_per_date=entries_per_date,
                top_n=top_n,
            )
        )
    except ValueError as exc:
        console.print(f"Invalid benchmark-suite date option: {exc}")
        raise typer.Exit(code=1) from exc
    except sqlite3.Error as exc:
        console.print(f"Failed to inspect benchmark-suite database: {exc}")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write benchmark-suite artifacts: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Benchmark suite [bold]{result.status}[/bold] at [bold]{result.export_dir}[/bold]."
    )
    console.print(f"Report: [bold]{result.report_path}[/bold]")
    console.print(f"Entries CSV: [bold]{result.entries_path}[/bold]")


@app.command("run-multiday-catalyst-replay")
def run_multiday_catalyst_replay_command(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB with PIT universe, SEC filings, and historical bars.",
    ),
    export_root: Path = typer.Option(
        Path("data/backtest_lab/replays/multiday_catalyst"),
        "--export-root",
        help="Directory where the replay run directory is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to multiday_catalyst_<dates>_<timestamp>.",
    ),
    start_date: str = typer.Option(
        ...,
        "--start-date",
        help="Inclusive first catalyst market date in YYYY-MM-DD.",
    ),
    end_date: str = typer.Option(
        ...,
        "--end-date",
        help="Inclusive last catalyst market date in YYYY-MM-DD.",
    ),
    entry_time: str = typer.Option(
        "d_plus_1_open",
        "--entry-time",
        help="Entry timing: d_close, d_plus_1_open, or d_plus_1_0935.",
    ),
    require_sec_filing_within_days: int = typer.Option(
        1,
        "--require-sec-filing-within-days",
        min=1,
        help="Require a SEC filing effective within this many calendar days ending on D.",
    ),
    min_dollar_volume_d: float = typer.Option(
        1_000_000.0,
        "--min-dollar-volume-d",
        min=0.0,
        help="Minimum catalyst-day dollar volume from historical bars.",
    ),
    max_holding_days: int = typer.Option(
        5,
        "--max-holding-days",
        min=1,
        help="Maximum trading days to hold from entry day.",
    ),
    exit_on_volume_exhaustion: bool = typer.Option(
        True,
        "--exit-on-volume-exhaustion/--no-exit-on-volume-exhaustion",
        help="Exit when volume dries up after catalyst day.",
    ),
    exit_on_follow_on_filing: bool = typer.Option(
        True,
        "--exit-on-follow-on-filing/--no-exit-on-follow-on-filing",
        help="Exit when a follow-on SEC filing appears after catalyst day.",
    ),
    structure_stop: str = typer.Option(
        "d_minus_1_low",
        "--structure-stop",
        help="Structure stop: d_minus_1_low, n_day_low, or atr_multiple.",
    ),
    structure_stop_param: float = typer.Option(
        1.0,
        "--structure-stop-param",
        min=0.01,
        help="N for n_day_low, or ATR multiple for atr_multiple.",
    ),
    symbol: list[str] | None = typer.Option(
        None,
        "--symbol",
        help="Optional explicit symbol filter. Repeat or comma-separate.",
    ),
    passed_only: bool = typer.Option(
        True,
        "--passed-only/--all-universe-rows",
        help="Use only passed PIT universe rows when resolving the universe.",
    ),
    require_kis_tradable: bool = typer.Option(
        True,
        "--require-kis-tradable/--allow-kis-untradable",
        help="Exclude KIS-untradable PIT universe symbols before entry generation.",
    ),
    allow_live_db: bool = typer.Option(
        False,
        "--allow-live-db",
        help="Allow using data/penny_stock_radar.sqlite3. Disabled by default for replay safety.",
    ),
) -> None:
    """Run the scaffolded D to D+5 multi-day catalyst replay."""
    try:
        parsed_entry_time = _parse_catalyst_entry_time(entry_time)
        parsed_structure_stop = _parse_catalyst_structure_stop(structure_stop)
        filters = {
            "export_root": export_root,
            "run_id": run_id,
            "symbols": _normalize_symbol_list(symbol),
            "passed_only": passed_only,
            "require_kis_tradable": require_kis_tradable,
            "allow_live_db": allow_live_db,
        }
        result = MultidayCatalystReplayRunner().run(
            db_path=db_path,
            market_date_start=date.fromisoformat(start_date),
            market_date_end=date.fromisoformat(end_date),
            entry_rule=CatalystEntryRule(
                entry_time=parsed_entry_time,
                require_sec_filing_within_days=require_sec_filing_within_days,
                min_dollar_volume_d=min_dollar_volume_d,
            ),
            exit_rule=CatalystExitRule(
                max_holding_days=max_holding_days,
                exit_on_volume_exhaustion=exit_on_volume_exhaustion,
                exit_on_follow_on_filing=exit_on_follow_on_filing,
                structure_stop=parsed_structure_stop,
                structure_stop_param=structure_stop_param,
            ),
            universe_filter=filters,
        )
    except (ValueError, OSError, sqlite3.Error) as exc:
        console.print(f"Failed to run multiday catalyst replay: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Multiday catalyst replay wrote [bold]{result.export_dir}[/bold].")
    console.print(f"Manifest: [bold]{result.manifest_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(f"Trades CSV: [bold]{result.trades_path}[/bold]")


def _parse_catalyst_entry_time(
    value: str,
) -> Literal["d_close", "d_plus_1_open", "d_plus_1_0935"]:
    normalized = value.strip().lower()
    if normalized not in {"d_close", "d_plus_1_open", "d_plus_1_0935"}:
        raise ValueError("--entry-time must be d_close, d_plus_1_open, or d_plus_1_0935.")
    return normalized  # type: ignore[return-value]


def _parse_catalyst_structure_stop(
    value: str,
) -> Literal["d_minus_1_low", "n_day_low", "atr_multiple"]:
    normalized = value.strip().lower()
    if normalized not in {"d_minus_1_low", "n_day_low", "atr_multiple"}:
        raise ValueError("--structure-stop must be d_minus_1_low, n_day_low, or atr_multiple.")
    return normalized  # type: ignore[return-value]


@app.command("audit-pit-universe-reconstruction")
def audit_pit_universe_reconstruction(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical research DB to audit. Defaults to data/backtest_lab/.",
    ),
    output_root: Path = typer.Option(
        Path("data/backtest_lab/research_runs"),
        "--output-root",
        help="Directory where the PIT universe audit run is written.",
    ),
    run_id: str | None = typer.Option(
        None,
        "--run-id",
        help="Optional stable run id. Defaults to pit_universe_<timestamp>.",
    ),
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        help="Optional inclusive first market date in YYYY-MM-DD.",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Optional inclusive last market date in YYYY-MM-DD.",
    ),
    min_bars_per_symbol: int = typer.Option(
        30,
        "--min-bars-per-symbol",
        min=1,
        help="Minimum valid minute bars required to count a symbol as diagnostic bar-universe input.",
    ),
) -> None:
    """Audit whether historical dates can support exact or diagnostic PIT universes."""
    try:
        result = PointInTimeUniverseAuditor().run(
            PointInTimeUniverseAuditOptions(
                db_path=db_path,
                output_root=output_root,
                run_id=run_id,
                start_date=start_date,
                end_date=end_date,
                min_bars_per_symbol=min_bars_per_symbol,
            )
        )
    except sqlite3.Error as exc:
        console.print(f"Failed to audit point-in-time universe inputs: {exc}")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write point-in-time universe audit: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"PIT universe audit [bold]{result.status}[/bold] at [bold]{result.export_dir}[/bold]."
    )
    console.print(f"Report: [bold]{result.report_path}[/bold]")
    console.print(f"Summary: [bold]{result.summary_path}[/bold]")
    console.print(f"Dates CSV: [bold]{result.csv_path}[/bold]")


@app.command("tag-pit-universe-scan")
def tag_pit_universe_scan(
    scan_id: str = typer.Option(..., "--scan-id", help="Existing scan_runs.scan_id to tag."),
    market_date: str = typer.Option(..., "--market-date", help="Point-in-time market date in YYYY-MM-DD."),
    point_in_time_tag: str = typer.Option(
        "retro_scan_created_at",
        "--point-in-time-tag",
        help="Provenance label to store on scan_runs.point_in_time_tag.",
    ),
    cutoff_time: str = typer.Option(
        "08:00",
        "--cutoff-time",
        help="ET cutoff that the scan must not be later than unless --allow-after-cutoff is set.",
    ),
    allow_after_cutoff: bool = typer.Option(
        False,
        "--allow-after-cutoff",
        help="Allow tagging scans created after the cutoff. Use only for diagnostic replay plumbing.",
    ),
    diff_output: Path | None = typer.Option(
        None,
        "--diff-output",
        help="Optional JSON path for the PIT-vs-current universe difference report.",
    ),
) -> None:
    """Tag an explicit existing scan as point-in-time with cutoff guardrails."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    try:
        cutoff = _parse_optional_time(cutoff_time, "--cutoff-time")
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    assert cutoff is not None

    with get_connection(settings.database_path) as connection:
        scan = connection.execute(
            """
            SELECT scan_id, source, symbol_count, market_date, snapshot_role,
                   point_in_time_tag, created_at
            FROM scan_runs
            WHERE scan_id = ?
            """,
            (scan_id,),
        ).fetchone()
        if scan is None:
            console.print(f"No scan found for scan_id={scan_id}")
            raise typer.Exit(code=1)
        counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN passed_filters = 1 THEN 1 ELSE 0 END) AS passed_count
            FROM universe
            WHERE scan_id = ?
            """,
            (scan_id,),
        ).fetchone()

    created_at = _parse_datetime(str(scan["created_at"]))
    if created_at is None:
        console.print(f"Cannot parse scan created_at={scan['created_at']!r}")
        raise typer.Exit(code=1)
    cutoff_at = datetime.combine(datetime.fromisoformat(market_date).date(), cutoff, tzinfo=EASTERN)
    created_at_et = created_at.astimezone(EASTERN)
    if created_at_et > cutoff_at and not allow_after_cutoff:
        console.print(
            "Refusing to tag scan after cutoff: "
            f"created_at_et={created_at_et.isoformat()} cutoff_at={cutoff_at.isoformat()}. "
            "Pass --allow-after-cutoff only for diagnostic replay plumbing."
        )
        raise typer.Exit(code=1)

    manager = BacktestDataManager(settings)
    manager.tag_universe_snapshot(
        scan_id,
        market_date=market_date,
        point_in_time_tag=point_in_time_tag,
    )
    diff = manager.build_universe_difference_report(market_date)
    if diff_output is not None:
        diff_output.parent.mkdir(parents=True, exist_ok=True)
        diff_output.write_text(
            json.dumps(asdict(diff), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    console.print(
        "Tagged PIT scan "
        f"[bold]{scan_id}[/bold] for [bold]{market_date}[/bold] "
        f"source={scan['source']} created_at_et={created_at_et.isoformat()} "
        f"total={int(counts['total_count'] or 0)} passed={int(counts['passed_count'] or 0)}."
    )
    console.print(
        "PIT/current diff: "
        f"pit={diff.point_in_time_count} current={diff.current_count} "
        f"common={diff.common_symbols} added={len(diff.added_symbols)} removed={len(diff.removed_symbols)}."
    )
    if diff_output is not None:
        console.print(f"Diff JSON: [bold]{diff_output}[/bold]")


@app.command("run-premkt-validation-plan")
def run_premkt_validation_plan(
    db_path: Path = typer.Option(
        DEFAULT_REPLAY_DB,
        "--db-path",
        help="Historical replay DB copy under data/backtest_lab/.",
    ),
    model_path: Path | None = typer.Option(
        Path("data/ml/premkt_model.joblib"),
        "--model-path",
        help="Fixed trained model artifact for calibration and OOS replay.",
    ),
    calibration_start: str = typer.Option(..., "--calibration-start", help="YYYY-MM-DD."),
    calibration_end: str = typer.Option(..., "--calibration-end", help="YYYY-MM-DD."),
    oos_start: str = typer.Option(..., "--oos-start", help="YYYY-MM-DD."),
    oos_end: str = typer.Option(..., "--oos-end", help="YYYY-MM-DD."),
    export_root: Path | None = typer.Option(
        None,
        "--export-root",
        help="Validation output root. Defaults to data/backtest_lab/replays/validation_<timestamp>.",
    ),
    score_mode: str = typer.Option("blend", "--score-mode", help="Scoring mode: rule, ml, or blend."),
    ml_weight: float = typer.Option(0.5, "--ml-weight", min=0.0, max=1.0),
    cutoff_time: str = typer.Option("08:00", "--cutoff-time", help="Prediction cutoff time in ET, HH:MM."),
    allow_live_db: bool = typer.Option(
        False,
        "--allow-live-db",
        help="Explicitly allow data/penny_stock_radar.sqlite3. Off by default.",
    ),
) -> None:
    """Run calibration and OOS replay, then write evaluation_report.json."""
    import penny_stock_radar.cli as root_cli

    try:
        cutoff = datetime.strptime(cutoff_time, "%H:%M").time()
        root = export_root or Path("data/backtest_lab/replays") / datetime.now().strftime(
            "validation_%Y%m%d_%H%M%S"
        )
        settings = root_cli.get_settings()
        calibration = PremktHistoricalReplayRunner(
            settings,
            options=ReplayOptions(
                start_date=calibration_start,
                end_date=calibration_end,
                db_path=db_path,
                model_path=model_path,
                score_mode=score_mode,
                ml_weight=ml_weight,
                export_dir=root / "calibration",
                cutoff_time=cutoff,
                allow_live_db=allow_live_db,
            ),
        ).run()
        out_of_sample = PremktHistoricalReplayRunner(
            settings,
            options=ReplayOptions(
                start_date=oos_start,
                end_date=oos_end,
                db_path=db_path,
                model_path=model_path,
                score_mode=score_mode,
                ml_weight=ml_weight,
                export_dir=root / "out_of_sample",
                cutoff_time=cutoff,
                allow_live_db=allow_live_db,
            ),
        ).run()
        evaluation = PremktReplayValidationEvaluator().evaluate(
            calibration_dir=calibration.export_dir,
            out_of_sample_dir=out_of_sample.export_dir,
            output_path=root / "evaluation_report.json",
        )
    except (FileNotFoundError, ReplaySafetyError, ValueError, OSError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print(f"Validation root: [bold]{root}[/bold]")
    console.print(f"Calibration summary: [bold]{calibration.summary_path}[/bold]")
    console.print(f"OOS summary: [bold]{out_of_sample.summary_path}[/bold]")
    console.print(
        "Evaluation report: "
        f"[bold]{evaluation.output_path}[/bold] "
        f"decision_gate=[bold]{evaluation.report['decision_gate']['status']}[/bold]"
    )


@app.command("backfill-kis-minute")
def backfill_kis_minute(
    market_date: str = typer.Option(..., help="Target market date in YYYY-MM-DD."),
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to backfill. Repeat the flag for multiple symbols. Defaults to the point-in-time universe for the date.",
    ),
    symbol_limit: int | None = typer.Option(
        None,
        help="Cap how many symbols are backfilled when the universe is resolved from the database.",
    ),
    nmin: int = typer.Option(1, help="Minute interval to request from KIS."),
    max_pages: int = typer.Option(10, help="Maximum KIS pagination depth per symbol."),
    infer_halts: bool = typer.Option(
        True,
        "--infer-halts/--no-infer-halts",
        help="Infer halt events from the stored minute bars after the backfill completes.",
    ),
) -> None:
    """Backfill KIS overseas minute bars into the historical backtest tables."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    service = KISHistoricalDataService(settings)
    manager = BacktestDataManager(settings)
    try:
        summary = service.backfill_minute_bars(
            market_date,
            symbols=symbol,
            symbol_limit=symbol_limit,
            nmin=nmin,
            max_pages=max_pages,
        )
    finally:
        service.close()

    console.print(
        f"KIS minute backfill stored [bold]{summary.inserted_rows}[/bold] bars for "
        f"[bold]{summary.requested_symbols}[/bold] symbols on [bold]{summary.market_date}[/bold]."
    )
    if summary.unresolved_symbols:
        console.print(f"Unresolved exchange symbols: {', '.join(summary.unresolved_symbols)}")
    if summary.skipped_symbols:
        console.print(f"No minute rows returned: {', '.join(summary.skipped_symbols)}")
    if infer_halts:
        inferred = manager.infer_and_store_halt_events(
            market_date,
            symbols=symbol or None,
        )
        console.print(f"Inferred [bold]{len(inferred)}[/bold] halt events from minute bars.")


@app.command("capture-kis-l1")
def capture_kis_l1(
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to snapshot. Repeat the flag for multiple symbols. Defaults to the latest watchlist.",
    ),
    limit: int = typer.Option(
        20,
        help="Maximum symbols to snapshot when symbols are resolved from stored data.",
    ),
) -> None:
    """Capture KIS top-of-book quotes into the historical L1 table for forward archive building."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    symbols = _resolve_l1_capture_symbols(
        settings,
        symbol=symbol,
        limit=limit,
    )
    if not symbols:
        console.print("No symbols available. Build a watchlist first or pass `--symbol` explicitly.")
        raise typer.Exit(code=1)

    service = KISHistoricalDataService(settings)
    try:
        summary = service.capture_l1_quotes(symbols=symbols[:limit])
    finally:
        service.close()

    console.print(
        f"KIS L1 snapshot stored [bold]{summary.inserted_rows}[/bold] quotes for "
        f"[bold]{summary.requested_symbols}[/bold] symbols on [bold]{summary.market_date}[/bold]."
    )
    if summary.unresolved_symbols:
        console.print(f"Unresolved exchange symbols: {', '.join(summary.unresolved_symbols)}")
    if summary.skipped_symbols:
        console.print(f"No L1 quote returned: {', '.join(summary.skipped_symbols)}")


@app.command("evaluate-kis-quote-consolidation")
def evaluate_kis_quote_consolidation_command(
    sample_window_days: int = typer.Option(
        5,
        min=1,
        help="How many recent market dates of KIS L1 rows to evaluate.",
    ),
    reference_source: str | None = typer.Option(
        None,
        help="Optional reference source to compare at identical quote timestamps.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        help="JSON output path for this validation run.",
    ),
) -> None:
    """Evaluate whether KIS L1 snapshots have NBBO-like consolidation evidence."""
    import penny_stock_radar.cli as root_cli

    normalized_reference = reference_source.lower() if reference_source else None
    if normalized_reference not in {None, "ibkr_nbbo", "polygon_nbbo_free"}:
        console.print("--reference-source must be ibkr_nbbo, polygon_nbbo_free, or omitted.")
        raise typer.Exit(code=1)

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    verdict = evaluate_kis_quote_consolidation(
        settings.database_path,
        sample_window_days=sample_window_days,
        reference_source=normalized_reference,
    )
    output_path, latest_path = write_consolidation_verdict(verdict, out)
    console.print(
        "KIS quote consolidation: "
        f"[bold]{verdict.classification}[/bold] "
        f"sample_size={verdict.evidence.sample_size} "
        f"freq_hz={verdict.evidence.quote_update_frequency_hz:.3f}"
    )
    console.print(f"Reason: {verdict.reason}")
    console.print(f"Validation JSON: {output_path}")
    console.print(f"Latest verdict JSON: {latest_path}")


@app.command("capture-kis-l1-window")
def capture_kis_l1_window(
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to snapshot repeatedly. Defaults to the latest watchlist.",
    ),
    limit: int = typer.Option(
        20,
        help="Maximum symbols to snapshot when symbols are resolved from stored data.",
    ),
    iterations: int = typer.Option(
        10,
        min=1,
        help="How many repeated capture passes to run.",
    ),
    interval_seconds: float = typer.Option(
        60.0,
        min=1e-9,
        help="How long to wait between capture passes.",
    ),
    coverage_session: str = typer.Option(
        "premarket",
        help="Session window to report into the Step 0 coverage gate after capture.",
    ),
    update_coverage_gate: bool = typer.Option(
        True,
        "--update-coverage-gate/--no-update-coverage-gate",
        help="Refresh the latest Step 0 L1 coverage report and gate status after capture.",
    ),
    gate_status_path: Path | None = typer.Option(
        None,
        help="Optional JSON path for the latest coverage gate snapshot.",
    ),
    rotation_tier1_size: int | None = typer.Option(
        None,
        min=0,
        help=(
            "Continuous subscription tier size. Defaults to the resolved universe size, "
            "which keeps the legacy non-rotation path."
        ),
    ),
    rotation_tier2_concurrent: int = typer.Option(
        10,
        min=0,
        help="Number of tier2 symbols to rotate into each active capture window.",
    ),
    rotation_window_seconds: int = typer.Option(
        300,
        min=1,
        help="Seconds between tier2 rotation steps.",
    ),
    rotation_priority_metric: str = typer.Option(
        "watchlist_rank",
        help="Rotation priority metric: dollar_volume, watchlist_rank, or volatility.",
    ),
) -> None:
    """Capture KIS L1 quotes repeatedly to build interval coverage for the current session."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    if not settings.kis_app_key or not settings.kis_app_secret:
        console.print("KIS API credentials are missing.")
        raise typer.Exit(code=1)

    symbols = _resolve_l1_capture_symbols(
        settings,
        symbol=symbol,
        limit=limit,
    )
    if not symbols:
        console.print("No symbols available. Build a watchlist first or pass `--symbol` explicitly.")
        raise typer.Exit(code=1)

    capture_universe = symbols[:limit]
    try:
        priority_data = _rotation_priority_data(
            capture_universe,
            priority_metric=rotation_priority_metric,
        )
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    rotation_manager = KisWebSocketRotationManager()
    rotation_policy = RotationPolicy(
        tier1_size=len(capture_universe) if rotation_tier1_size is None else rotation_tier1_size,
        tier2_window_seconds=rotation_window_seconds,
        tier2_concurrent=rotation_tier2_concurrent,
        priority_metric=rotation_priority_metric.strip().lower(),  # type: ignore[arg-type]
    )
    slots = rotation_manager.assign_tiers(capture_universe, priority_data, rotation_policy)
    slot_counts = rotation_manager.slot_counts(slots)
    service = KISHistoricalDataService(settings)
    total_inserted = 0
    unresolved: set[str] = set()
    skipped: set[str] = set()
    market_date: str | None = None
    elapsed_since_rotation = 0.0
    try:
        for index in range(iterations):
            if index > 0:
                elapsed_since_rotation += interval_seconds
                slots = rotation_manager.next_rotation_step(
                    slots,
                    int(elapsed_since_rotation),
                )
                elapsed_since_rotation %= rotation_window_seconds
            active_symbols = rotation_manager.active_symbols(slots)
            summary = service.capture_l1_quotes(
                symbols=active_symbols,
                quote_stamper=rotation_manager.stamp_quote,
            )
            market_date = summary.market_date
            total_inserted += summary.inserted_rows
            unresolved.update(summary.unresolved_symbols)
            skipped.update(summary.skipped_symbols)
            typer.echo(
                "iteration="
                f"{index + 1} symbols={summary.requested_symbols} "
                f"universe={len(capture_universe)} "
                f"new_rows={summary.inserted_rows} distinct_minutes={summary.distinct_minute_keys}",
                err=True,
            )
            if summary.snapshot_mismatch_count:
                typer.echo(
                    f"snapshot_date mismatch rows={summary.snapshot_mismatch_count}",
                    err=True,
                )
            if summary.duplicate_minute_bucket_count:
                typer.echo(
                    f"duplicate minute bucket rows={summary.duplicate_minute_bucket_count}",
                    err=True,
                )
            if summary.stale_timestamp_fallback_count:
                typer.echo(
                    f"stale timestamp fallback rows={summary.stale_timestamp_fallback_count}",
                    err=True,
                )
            console.print(
                f"L1 capture pass {index + 1}/{iterations}: "
                f"stored [bold]{summary.inserted_rows}[/bold] quotes for "
                f"[bold]{summary.requested_symbols}[/bold] symbols."
            )
            if index + 1 < iterations:
                time.sleep(interval_seconds)
    finally:
        service.close()

    console.print(
        f"KIS L1 archive window stored [bold]{total_inserted}[/bold] quotes across "
        f"[bold]{iterations}[/bold] passes"
        + (f" on [bold]{market_date}[/bold]." if market_date is not None else ".")
    )
    if unresolved:
        console.print(f"Unresolved exchange symbols: {', '.join(sorted(unresolved))}")
    if skipped:
        console.print(f"No L1 quote returned: {', '.join(sorted(skipped))}")
    if update_coverage_gate and market_date is not None:
        try:
            manager = BacktestDataManager(settings)
            report = manager.build_l1_coverage_report(
                market_date,
                symbols=capture_universe,
                session=coverage_session,
                source="kis_l1_snapshot",
                tier1_continuous_symbol_count=slot_counts[
                    "tier1_continuous_symbol_count"
                ],
                tier2_rotation_symbol_count=slot_counts[
                    "tier2_rotation_symbol_count"
                ],
                rotation_gap_seconds_p90=rotation_manager.rotation_gap_seconds_p90(slots),
            )
            report_path = manager.export_coverage_report_json(report)
            gate_status = manager.build_coverage_gate_status(
                report,
                report_path=report_path,
            )
            gate_path = manager.export_coverage_gate_status(
                gate_status,
                output_path=gate_status_path,
            )
        except ValueError as exc:
            console.print(str(exc))
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            console.print(f"Failed to write coverage outputs: {exc}")
            raise typer.Exit(code=1) from exc
        console.print(
            f"L1 coverage gate {gate_status.status}: "
            f"symbols={gate_status.covered_symbol_count}/{gate_status.expected_symbol_count} "
            f"({gate_status.symbol_coverage_pct:.1f}%), "
            f"intervals={gate_status.covered_interval_count}/{gate_status.expected_interval_count} "
            f"({gate_status.interval_coverage_pct:.1f}%)."
        )
        console.print(f"Coverage report JSON: {report_path}")
        console.print(f"Coverage gate status JSON: {gate_path}")


@app.command("report-backtest-coverage")
def report_backtest_coverage(
    market_date: str = typer.Option(..., help="Target market date in YYYY-MM-DD."),
    symbol: list[str] = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbols to evaluate. Defaults to the point-in-time universe for the date.",
    ),
    limit: int | None = typer.Option(
        None,
        help="Optional cap when coverage symbols are resolved from the database.",
    ),
    session: str = typer.Option(
        "premarket",
        help="Coverage window to evaluate: premarket, regular, or full_day.",
    ),
    infer_halts: bool = typer.Option(
        False,
        "--infer-halts/--no-infer-halts",
        help="Infer halt events from minute bars as part of the report run.",
    ),
    json_output: Path | None = typer.Option(
        None,
        help="Optional JSON path for the full coverage report. Defaults to the configured coverage report directory.",
    ),
    gate_status_path: Path | None = typer.Option(
        None,
        help="Optional JSON path for the latest coverage gate snapshot. Defaults to the configured gate status path.",
    ),
) -> None:
    """Build a historical L1 coverage report for a backtest date."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    manager = BacktestDataManager(settings)
    symbols = [value.upper() for value in (symbol or [])]
    if not symbols:
        point_in_time_rows = manager.fetch_point_in_time_universe(market_date)
        symbols = [str(row["symbol"]).upper() for row in point_in_time_rows]
    if not symbols:
        latest_rows = fetch_latest_passed_universe(
            settings.database_path,
            prefer_reportable=False,
        )
        symbols = [str(row["symbol"]).upper() for row in latest_rows]
    if limit is not None:
        symbols = symbols[:limit]
    if not symbols:
        console.print("No symbols available. Tag a point-in-time universe first or build a universe snapshot.")
        raise typer.Exit(code=1)

    try:
        report = manager.build_l1_coverage_report(
            market_date,
            symbols=symbols,
            session=session,
            source="historical_l1_quotes",
        )
        report_path = manager.export_coverage_report_json(report, output_path=json_output)
        gate_status = manager.build_coverage_gate_status(
            report,
            report_path=report_path,
        )
        gate_path = manager.export_coverage_gate_status(
            gate_status,
            output_path=gate_status_path,
        )
    except ValueError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(f"Failed to write coverage outputs: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"L1 coverage {report.market_date} {session}: "
        f"symbols={report.covered_symbol_count}/{report.expected_symbol_count} "
        f"({report.symbol_coverage_pct:.1f}%), "
        f"intervals={report.covered_interval_count}/{report.expected_interval_count} "
        f"({report.interval_coverage_pct:.1f}%)."
    )
    console.print(f"Coverage report JSON: {report_path}")
    console.print(
        "Coverage gate "
        f"{gate_status.status}: threshold={gate_status.threshold_pct:.1f}% "
        f"(symbol={gate_status.symbol_coverage_pct:.1f}%, interval={gate_status.interval_coverage_pct:.1f}%)."
    )
    console.print(f"Coverage gate status JSON: {gate_path}")
    if infer_halts:
        inferred = manager.infer_and_store_halt_events(market_date, symbols=symbols)
        console.print(f"Inferred [bold]{len(inferred)}[/bold] halt events from minute bars.")


@app.command("scan-market-activity")
def scan_market_activity(
    phase: str = typer.Option(
        "auto",
        help="Which phase to scan: auto, premarket, or regular.",
    ),
    scan_limit: int | None = typer.Option(
        None,
        help="Optional cap on how many symbols to scan from the current market scope.",
    ),
    top_limit: int = typer.Option(
        10,
        help="How many symbols count as top leaders for prediction-vs-outcome matching.",
    ),
    comparison_csv: Path | None = typer.Option(
        None,
        help="Optional CSV path for prediction-vs-outcome export. Defaults to sample_outputs/<phase>_prediction_vs_actual.csv.",
    ),
) -> None:
    """Rank the latest in-scope market movers by live % change and volume, then compare them with the watchlist."""
    import penny_stock_radar.cli as root_cli

    settings = root_cli.get_settings()
    root_cli.init_database(settings.database_path)
    scanner = MarketActivityScanner(settings)
    if comparison_csv is None:
        resolved_phase = scanner.resolve_market_phase(phase)
        if resolved_phase in {"premarket", "regular"}:
            comparison_csv = Path("sample_outputs") / f"{resolved_phase}_prediction_vs_actual.csv"

    try:
        result = scanner.scan(
            phase=phase,
            scan_limit=scan_limit,
            top_limit=top_limit,
            comparison_csv=comparison_csv,
        )
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)

    pct_table = Table(title=f"Live {result.market_phase.title()} Movers · % Change")
    pct_table.add_column("Rank")
    pct_table.add_column("Symbol")
    pct_table.add_column("Pred")
    pct_table.add_column("Price")
    pct_table.add_column("%Chg")
    pct_table.add_column("Volume")
    pct_table.add_column("DV")
    pct_table.add_column("Spread")
    pct_table.add_column("Read")
    for row in sorted(result.activity, key=lambda item: (item.pct_rank, item.symbol))[:top_limit]:
        pct_table.add_row(
            str(row.pct_rank),
            row.symbol,
            "Y" if row.predicted else "-",
            format_optional_number(row.last_price, 4),
            format_optional_number(row.pct_change, 2),
            format_optional_number(row.volume, 0),
            format_optional_number(row.dollar_volume, 0),
            format_optional_percent(row.spread_pct),
            trade_call_label(row.analysis_label),
        )

    volume_table = Table(title=f"Live {result.market_phase.title()} Movers · Volume")
    volume_table.add_column("Rank")
    volume_table.add_column("Symbol")
    volume_table.add_column("Pred")
    volume_table.add_column("Volume")
    volume_table.add_column("DV")
    volume_table.add_column("%Chg")
    volume_table.add_column("Spread")
    volume_table.add_column("Read")
    for row in sorted(result.activity, key=lambda item: (item.volume_rank, item.symbol))[:top_limit]:
        volume_table.add_row(
            str(row.volume_rank),
            row.symbol,
            "Y" if row.predicted else "-",
            format_optional_number(row.volume, 0),
            format_optional_number(row.dollar_volume, 0),
            format_optional_number(row.pct_change, 2),
            format_optional_percent(row.spread_pct),
            trade_call_label(row.analysis_label),
        )

    outcome_table = Table(title=f"Prediction vs Outcome · {result.market_phase.title()}")
    outcome_table.add_column("Symbol")
    outcome_table.add_column("Pred")
    outcome_table.add_column("WRank")
    outcome_table.add_column("%Rank")
    outcome_table.add_column("VRank")
    outcome_table.add_column("Outcome")
    outcome_table.add_column("Read")
    for row in result.outcomes[: max(top_limit * 2, 12)]:
        outcome_table.add_row(
            row.symbol,
            "Y" if row.predicted else "-",
            str(row.watchlist_rank or "-"),
            str(row.pct_rank or "-"),
            str(row.volume_rank or "-"),
            row.outcome,
            trade_call_label(row.analysis_label),
        )

    console.print(
        f"Scanned {result.scanned_symbol_count} symbols from the {settings.market_scope_label} scope for the {result.market_phase} phase."
    )
    console.print(pct_table)
    console.print(volume_table)
    console.print(outcome_table)
    if result.comparison_csv is not None:
        console.print(f"Prediction comparison CSV written to [bold]{result.comparison_csv}[/bold]")
