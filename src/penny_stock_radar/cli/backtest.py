from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import typer
from rich.table import Table

from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_premkt_predictions,
    fetch_latest_watchlist,
    get_connection,
)
from ..services.backtest_data import BacktestDataManager
from ..services.kis_historical import KISHistoricalDataService
from ..services.premkt_entry_signal_audit import (
    DEFAULT_SIGNAL_ENTRY_LABELS,
    DEFAULT_SIGNAL_SETUP_STATES,
    PremktEntrySignalAuditor,
)
from ..services.market_activity import MarketActivityScanner
from ..services.falsification_research import (
    FalsificationAuditOptions,
    FalsificationResearchAuditor,
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
from .common import console, format_optional_number, format_optional_percent, trade_call_label

app = typer.Typer()
EASTERN = ZoneInfo("America/New_York")


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

    service = KISHistoricalDataService(settings)
    total_inserted = 0
    unresolved: set[str] = set()
    skipped: set[str] = set()
    market_date: str | None = None
    try:
        for index in range(iterations):
            summary = service.capture_l1_quotes(symbols=symbols[:limit])
            market_date = summary.market_date
            total_inserted += summary.inserted_rows
            unresolved.update(summary.unresolved_symbols)
            skipped.update(summary.skipped_symbols)
            typer.echo(
                "iteration="
                f"{index + 1} symbols={summary.requested_symbols} "
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
                symbols=symbols[:limit],
                session=coverage_session,
                source="kis_l1_snapshot",
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
