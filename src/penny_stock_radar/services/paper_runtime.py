from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..db import fetch_latest_paper_trading_run, fetch_paper_orders, fetch_paper_positions, fetch_paper_run_snapshots, fetch_paper_trading_run_by_id

EASTERN = ZoneInfo("America/New_York")
PRIMARY_PAPER_STRATEGY = "auto_paper_v1"
BASELINE_PCT_STRATEGY = "baseline_pct_leader_v1"
BASELINE_VOLUME_STRATEGY = "baseline_volume_leader_v1"
PAPER_STRATEGY_LABELS = {
    PRIMARY_PAPER_STRATEGY: "Adaptive",
    BASELINE_PCT_STRATEGY: "Pct Leader",
    BASELINE_VOLUME_STRATEGY: "Volume Leader",
}
PREDICTOR_WEIGHTED_BUCKET = "predictor_weighted"
MOMENTUM_ONLY_BUCKET = "momentum_only"
PAPER_BUCKET_LABELS = {
    PREDICTOR_WEIGHTED_BUCKET: "Predictor Weighted",
    MOMENTUM_ONLY_BUCKET: "Momentum Only",
    BASELINE_PCT_STRATEGY: "Baseline Pct",
    BASELINE_VOLUME_STRATEGY: "Baseline Volume",
}
MARKET_STATUS_STATE_PREFIX = "state:market_status:"


@dataclass(slots=True)
class PaperTradingStepResult:
    run_id: str
    strategy_name: str
    bucket: str
    market_phase: str
    actions: list[str]
    entered_count: int
    added_count: int
    exited_count: int
    open_position_count: int
    closed_trade_count: int
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    profit_factor: float
    reward_risk_ratio: float
    export_dir: Path
    comparison_path: Path | None = None


def paper_market_date(value: datetime) -> str:
    return value.astimezone(EASTERN).date().isoformat()


def default_paper_bucket(strategy_name: str, strategy_mode: str) -> str:
    return PREDICTOR_WEIGHTED_BUCKET


def csv_value(value: object) -> object:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return value


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key)) for key in fieldnames})


def export_run_csv(engine, run_id: str | None = None) -> Path:
    run = (
        fetch_latest_paper_trading_run(engine.settings.database_path, engine.strategy_name)
        if run_id is None
        else None
    )
    target_run_id = run_id or (run.run_id if run is not None else None)
    if target_run_id is None:
        return engine.export_dir

    target_run = run or fetch_paper_trading_run_by_id(
        engine.settings.database_path,
        target_run_id,
    )
    positions = fetch_paper_positions(engine.settings.database_path, target_run_id)
    orders = fetch_paper_orders(engine.settings.database_path, target_run_id)
    snapshots = fetch_paper_run_snapshots(engine.settings.database_path, target_run_id)

    engine.export_dir.mkdir(parents=True, exist_ok=True)
    summary_name = (
        "paper_run_summary.csv"
        if engine.bucket == PREDICTOR_WEIGHTED_BUCKET
        else f"paper_run_summary_{engine.bucket}.csv"
    )
    positions_name = (
        "paper_positions.csv"
        if engine.bucket == PREDICTOR_WEIGHTED_BUCKET
        else f"paper_positions_{engine.bucket}.csv"
    )
    orders_name = (
        "paper_orders.csv"
        if engine.bucket == PREDICTOR_WEIGHTED_BUCKET
        else f"paper_orders_{engine.bucket}.csv"
    )
    equity_name = (
        "paper_equity_curve.csv"
        if engine.bucket == PREDICTOR_WEIGHTED_BUCKET
        else f"paper_equity_curve_{engine.bucket}.csv"
    )
    if target_run is not None:
        write_csv(engine.export_dir / summary_name, [target_run.model_dump(mode="json")])
    write_csv(
        engine.export_dir / positions_name,
        [row.model_dump(mode="json") for row in positions],
    )
    write_csv(
        engine.export_dir / orders_name,
        [row.model_dump(mode="json") for row in orders],
    )
    write_csv(
        engine.export_dir / equity_name,
        [row.model_dump(mode="json") for row in snapshots],
    )
    return engine.export_dir.resolve()


def active_position_symbols(database_path: Path, run_id: str | None) -> tuple[str, ...]:
    if run_id is None:
        return ()
    positions = fetch_paper_positions(database_path, run_id)
    return tuple(
        sorted(
            {
                row.symbol.upper()
                for row in positions
                if row.status == "OPEN" and row.symbol
            }
        )
    )


def run_engine_once(
    engine,
    *,
    phase: str = "auto",
    activity: list | None = None,
    export_csv: bool = True,
):
    market_phase = engine.scanner.resolve_market_phase(phase)
    rows = list(activity or [])
    if not rows and market_phase in {"premarket", "regular"}:
        scan_result = engine.scanner.scan(
            phase=market_phase,
            top_limit=10,
            extra_symbols=engine._active_position_symbols(),
        )
        rows = scan_result.activity
    return engine.process_market_activity(
        market_phase=market_phase,
        activity=rows,
        export_csv=export_csv,
    )
