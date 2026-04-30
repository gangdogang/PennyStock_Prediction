from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time

import typer
from rich.table import Table

from ..db import (
    fetch_latest_passed_universe,
    fetch_latest_premkt_predictions,
    fetch_latest_watchlist,
)
from ..services.backtest_data import BacktestDataManager
from ..services.kis_historical import KISHistoricalDataService
from ..services.market_activity import MarketActivityScanner
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
        settings = root_cli.get_settings()
        settings_overrides = {}
        if predictor_k1 is not None:
            settings_overrides["paper_predictor_weight_k1"] = predictor_k1
        if predictor_k2 is not None:
            settings_overrides["paper_predictor_weight_k2"] = predictor_k2
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
