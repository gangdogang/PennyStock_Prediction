from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import ResolvedDataPath, resolve_hf_stocks_1m_path

DATASET_NAME = "cryptospartan_stocks_bars_1m"
DEFAULT_AUDIT_ROOT = Path("data/backtest_lab/audits/hf_cryptospartan_alpaca_bars_1m")
EASTERN_TZ = "America/New_York"


class Hf1mBarsAuditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Hf1mBarsAuditOptions:
    parquet_path: Path | None = None
    output_root: Path = DEFAULT_AUDIT_ROOT
    run_id: str | None = None
    top_ticker_count: int = 50


@dataclass(frozen=True, slots=True)
class Hf1mBarsAuditResult:
    export_dir: Path
    summary_json_path: Path
    summary_md_path: Path
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HfCandidateDayOptions:
    parquet_path: Path | None = None
    output_root: Path = Path("data/backtest_lab/candidate_days/hf_cryptospartan_alpaca_bars_1m")
    run_id: str | None = None
    min_price: float = 0.25
    max_price: float = 10.0
    min_regular_rows: int = 120
    min_early_dollar_volume: float = 250_000.0
    min_early_move_pct: float = 5.0
    min_afternoon_dollar_volume: float = 250_000.0
    min_afternoon_move_pct: float = 5.0
    min_daily_high_open_pct: float = 20.0
    min_candidate_days: int = 1000
    min_active_months: int = 36
    max_top_ticker_pct: float = 8.0
    max_top10_ticker_pct: float = 35.0
    max_top_month_pct: float = 20.0
    top_n: int = 25
    chunk_months: int = 3
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class HfCandidateDayResult:
    export_dir: Path
    candidate_csv_path: Path
    summary_json_path: Path
    summary_md_path: Path
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HfCandidateEventOptions:
    parquet_path: Path | None = None
    output_root: Path = Path("data/backtest_lab/candidate_events/hf_cryptospartan_alpaca_bars_1m")
    run_id: str | None = None
    min_price: float = 0.25
    max_price: float = 10.0
    min_rows_to_event: int = 5
    max_event_staleness_minutes: int = 2
    min_event_dollar_volume: float = 250_000.0
    min_event_move_pct: float = 5.0
    min_candidate_events: int = 1000
    min_active_months: int = 36
    max_top_ticker_pct: float = 8.0
    max_top10_ticker_pct: float = 35.0
    max_top_month_pct: float = 20.0
    event_times_et: tuple[str, ...] = ("09:45", "10:30", "14:00", "15:30")
    forward_windows_minutes: tuple[int, ...] = (30, 60, 120)
    top_n: int = 25
    chunk_months: int = 3
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class HfCandidateEventResult:
    export_dir: Path
    event_csv_path: Path
    summary_json_path: Path
    summary_md_path: Path
    summary: dict[str, Any]


class Hf1mBarsAuditor:
    def run(self, options: Hf1mBarsAuditOptions) -> Hf1mBarsAuditResult:
        resolved = resolve_hf_stocks_1m_path(options.parquet_path)
        path = resolved.path
        if not path.exists():
            raise Hf1mBarsAuditError(
                "HF 1m bars parquet not found: "
                f"{path}. Set PSR_HF_STOCKS_1M_PATH to the parquet file, "
                "or set PSR_DATA_ROOT to the external data root containing "
                "raw/huggingface/cryptospartan_stocks_bars_1m/stocks_bars_1m.parquet."
            )

        pl = _load_polars()
        lf = pl.scan_parquet(str(path))
        schema = _collect_schema(lf)
        columns = list(schema.keys())
        mapping = _resolve_column_mapping(columns)
        auditable = _with_audit_columns(lf, schema, mapping, pl)

        run_id = options.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        stat = path.stat()
        summary = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "dataset": DATASET_NAME,
                "audit_scope": "gross_ohlcv_falsification_data_only",
                "decision_grade": False,
                "cost_grade": "none",
                "cost_grade_reason": (
                    "1m OHLCV bars do not provide NBBO/spread/execution-cost evidence."
                ),
            },
            "path_resolution": _path_resolution_payload(resolved),
            "file": {
                "path": str(path),
                "exists": True,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            },
            "schema": {
                "columns": [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()],
                "column_mapping": mapping,
            },
        }
        summary.update(_audit_payload(auditable, pl, top_n=options.top_ticker_count))

        summary_json_path = export_dir / "audit_summary.json"
        summary_md_path = export_dir / "audit_summary.md"
        summary_json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_markdown_summary(summary), encoding="utf-8")
        return Hf1mBarsAuditResult(
            export_dir=export_dir,
            summary_json_path=summary_json_path,
            summary_md_path=summary_md_path,
            summary=summary,
        )


class HfCandidateDaySegmenter:
    def run(self, options: HfCandidateDayOptions) -> HfCandidateDayResult:
        resolved = resolve_hf_stocks_1m_path(options.parquet_path)
        path = resolved.path
        if not path.exists():
            raise Hf1mBarsAuditError(
                "HF 1m bars parquet not found: "
                f"{path}. Set PSR_HF_STOCKS_1M_PATH to the parquet file, "
                "or set PSR_DATA_ROOT to the external data root containing "
                "raw/huggingface/cryptospartan_stocks_bars_1m/stocks_bars_1m.parquet."
            )
        pl = _load_polars()
        lf = pl.scan_parquet(str(path))
        schema = _collect_schema(lf)
        mapping = _resolve_column_mapping(list(schema.keys()))
        if mapping.get("volume") is None:
            raise Hf1mBarsAuditError(
                "HF candidate-day segmentation requires a volume column. "
                f"Available columns: {', '.join(schema.keys())}"
            )
        auditable = _with_audit_columns(lf, schema, mapping, pl)
        run_id = options.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)
        candidate_csv_path = export_dir / "candidate_days.csv"
        summary_json_path = export_dir / "candidate_day_summary.json"
        summary_md_path = export_dir / "candidate_day_summary.md"

        start_date, end_exclusive = _candidate_date_bounds(auditable, pl, options)
        chunks = list(_iter_date_chunks(start_date, end_exclusive, options.chunk_months))
        frames = []
        for chunk_start, chunk_end in chunks:
            chunk_auditable = auditable.filter(
                (pl.col("__psr_et_date") >= chunk_start)
                & (pl.col("__psr_et_date") < chunk_end)
            )
            daily = _candidate_day_lazy_frame(chunk_auditable, pl, options)
            candidate_rows = daily.filter(pl.col("gross_candidate_day") == 1)
            candidate_chunk = _collect_lazy(candidate_rows.sort(["market_date", "ticker"]))
            if candidate_chunk.height:
                frames.append(candidate_chunk)
        candidate_df = (
            pl.concat(frames, how="vertical_relaxed").sort(["market_date", "ticker"])
            if frames
            else _empty_candidate_day_df(pl)
        )
        candidate_df.write_csv(candidate_csv_path)
        summary = _candidate_day_summary(
            candidate_df,
            options=options,
            run_id=run_id,
            path=path,
            resolved=resolved,
            schema=schema,
            mapping=mapping,
            pl=pl,
            chunks=chunks,
        )
        summary_json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_candidate_day_markdown(summary), encoding="utf-8")
        return HfCandidateDayResult(
            export_dir=export_dir,
            candidate_csv_path=candidate_csv_path,
            summary_json_path=summary_json_path,
            summary_md_path=summary_md_path,
            summary=summary,
        )


class HfCandidateEventSegmenter:
    def run(self, options: HfCandidateEventOptions) -> HfCandidateEventResult:
        resolved = resolve_hf_stocks_1m_path(options.parquet_path)
        path = resolved.path
        if not path.exists():
            raise Hf1mBarsAuditError(
                "HF 1m bars parquet not found: "
                f"{path}. Set PSR_HF_STOCKS_1M_PATH to the parquet file, "
                "or set PSR_DATA_ROOT to the external data root containing "
                "raw/huggingface/cryptospartan_stocks_bars_1m/stocks_bars_1m.parquet."
            )
        pl = _load_polars()
        lf = pl.scan_parquet(str(path))
        schema = _collect_schema(lf)
        mapping = _resolve_column_mapping(list(schema.keys()))
        if mapping.get("volume") is None:
            raise Hf1mBarsAuditError(
                "HF event-time segmentation requires a volume column. "
                f"Available columns: {', '.join(schema.keys())}"
            )
        event_minutes = _event_minutes(options.event_times_et)
        forward_windows = tuple(
            sorted(
                {
                    int(window)
                    for window in options.forward_windows_minutes
                    if int(window) > 0
                }
            )
        )
        if not event_minutes:
            raise Hf1mBarsAuditError("At least one --event-time is required.")
        if not forward_windows:
            raise Hf1mBarsAuditError("At least one positive forward window is required.")

        auditable = _with_audit_columns(lf, schema, mapping, pl)
        run_id = options.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)
        event_csv_path = export_dir / "candidate_events.csv"
        summary_json_path = export_dir / "candidate_event_summary.json"
        summary_md_path = export_dir / "candidate_event_summary.md"

        start_date, end_exclusive = _candidate_date_bounds(auditable, pl, options)
        chunks = list(_iter_date_chunks(start_date, end_exclusive, options.chunk_months))
        frames = []
        for chunk_start, chunk_end in chunks:
            chunk_auditable = auditable.filter(
                (pl.col("__psr_et_date") >= chunk_start)
                & (pl.col("__psr_et_date") < chunk_end)
            )
            event_frame = _candidate_event_lazy_frame(
                chunk_auditable,
                pl,
                options,
                event_minutes=event_minutes,
                forward_windows=forward_windows,
            )
            event_rows = event_frame.filter(pl.col("gross_candidate_event") == 1)
            event_chunk = _collect_lazy(event_rows.sort(["market_date", "ticker", "event_time_et"]))
            if event_chunk.height:
                frames.append(event_chunk)
        event_df = (
            pl.concat(frames, how="vertical_relaxed").sort(["market_date", "ticker", "event_time_et"])
            if frames
            else _empty_candidate_event_df(pl, forward_windows=forward_windows)
        )
        event_df.write_csv(event_csv_path)
        summary = _candidate_event_summary(
            event_df,
            options=options,
            run_id=run_id,
            path=path,
            resolved=resolved,
            schema=schema,
            mapping=mapping,
            pl=pl,
            chunks=chunks,
            event_times=tuple(_minute_to_hhmm(minute) for minute in event_minutes),
            forward_windows=forward_windows,
        )
        summary_json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_candidate_event_markdown(summary), encoding="utf-8")
        return HfCandidateEventResult(
            export_dir=export_dir,
            event_csv_path=event_csv_path,
            summary_json_path=summary_json_path,
            summary_md_path=summary_md_path,
            summary=summary,
        )


def _load_polars():
    try:
        return importlib.import_module("polars")
    except ModuleNotFoundError as exc:
        raise Hf1mBarsAuditError(
            "Polars is required for the HF 1m bars audit. Install it with "
            "`pip install polars` or `pip install .[hf]`. The audit intentionally "
            "does not use pandas for full-file parquet reads."
        ) from exc


def _collect_schema(lf) -> dict[str, Any]:
    collect_schema = getattr(lf, "collect_schema", None)
    if callable(collect_schema):
        return dict(collect_schema())
    return dict(lf.schema)


def _resolve_column_mapping(columns: list[str]) -> dict[str, str]:
    lower_to_original = {column.lower(): column for column in columns}
    mapping = {
        "ticker": _first_column(lower_to_original, "ticker", "symbol"),
        "timestamp": _first_column(
            lower_to_original,
            "timestamp",
            "bar_at",
            "datetime",
            "time",
            "ts",
        ),
        "open": _first_column(lower_to_original, "open", "open_price", "o"),
        "high": _first_column(lower_to_original, "high", "high_price", "h"),
        "low": _first_column(lower_to_original, "low", "low_price", "l"),
        "close": _first_column(lower_to_original, "close", "close_price", "c"),
        "volume": _first_column(lower_to_original, "volume", "vol", "v"),
    }
    missing = [name for name, column in mapping.items() if column is None and name != "volume"]
    if missing:
        raise Hf1mBarsAuditError(
            "HF 1m bars parquet is missing required columns: "
            f"{', '.join(missing)}. Available columns: {', '.join(columns)}"
        )
    return {name: str(column) for name, column in mapping.items() if column is not None}


def _first_column(lower_to_original: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in lower_to_original:
            return lower_to_original[candidate]
    return None


def _with_audit_columns(lf, schema: dict[str, Any], mapping: dict[str, str], pl):
    ts_col = mapping["timestamp"]
    ts_expr = _timestamp_expr(pl, ts_col, schema.get(ts_col))
    et_ts = ts_expr.dt.convert_time_zone(EASTERN_TZ)
    expressions = [
        pl.col(mapping["ticker"]).cast(pl.Utf8).str.to_uppercase().alias("__psr_ticker"),
        ts_expr.alias("__psr_ts"),
        et_ts.alias("__psr_et_ts"),
        et_ts.dt.date().alias("__psr_et_date"),
        (
            et_ts.dt.hour().cast(pl.Int32) * 60
            + et_ts.dt.minute().cast(pl.Int32)
        ).alias("__psr_et_minute"),
        pl.col(mapping["open"]).cast(pl.Float64).alias("__psr_open"),
        pl.col(mapping["high"]).cast(pl.Float64).alias("__psr_high"),
        pl.col(mapping["low"]).cast(pl.Float64).alias("__psr_low"),
        pl.col(mapping["close"]).cast(pl.Float64).alias("__psr_close"),
    ]
    if "volume" in mapping:
        expressions.append(pl.col(mapping["volume"]).cast(pl.Float64).alias("__psr_volume"))
    return lf.with_columns(expressions)


def _timestamp_expr(pl, column: str, dtype: Any):
    dtype_name = str(dtype)
    if dtype_name in {"String", "Utf8"}:
        return pl.col(column).str.to_datetime(strict=False, time_zone="UTC")
    if dtype_name == "Date":
        return pl.col(column).cast(pl.Datetime).dt.replace_time_zone("UTC")
    if getattr(dtype, "time_zone", None) is None:
        return pl.col(column).dt.replace_time_zone("UTC")
    return pl.col(column)


def _audit_payload(auditable, pl, *, top_n: int) -> dict[str, Any]:
    o = pl.col("__psr_open")
    h = pl.col("__psr_high")
    l = pl.col("__psr_low")
    c = pl.col("__psr_close")
    minute = pl.col("__psr_et_minute")
    ohlc_bad = (
        o.is_null()
        | h.is_null()
        | l.is_null()
        | c.is_null()
        | (o <= 0)
        | (h <= 0)
        | (l <= 0)
        | (c <= 0)
        | (h < l)
        | (h < o)
        | (h < c)
        | (l > o)
        | (l > c)
    ).fill_null(True)

    metrics = _collect_lazy(
        auditable.select(
            [
                pl.len().alias("row_count"),
                pl.col("__psr_ticker").n_unique().alias("ticker_count"),
                pl.col("__psr_ts").min().alias("min_timestamp"),
                pl.col("__psr_ts").max().alias("max_timestamp"),
                ohlc_bad.cast(pl.Int64).sum().alias("ohlc_sanity_failure_count"),
                (minute.is_between(240, 569, closed="both"))
                .cast(pl.Int64)
                .sum()
                .alias("premarket_0400_0929"),
                (minute.is_between(570, 959, closed="both"))
                .cast(pl.Int64)
                .sum()
                .alias("regular_0930_1600"),
                (minute.is_between(960, 1199, closed="both"))
                .cast(pl.Int64)
                .sum()
                .alias("afterhours_1600_2000"),
                (c <= 10).fill_null(False).cast(pl.Int64).sum().alias("close_lte_10"),
                (c <= 5).fill_null(False).cast(pl.Int64).sum().alias("close_lte_5"),
                (c <= 1).fill_null(False).cast(pl.Int64).sum().alias("close_lte_1"),
            ]
        )
    ).to_dicts()[0]

    duplicate_count = _single_int(
        _collect_lazy(
            auditable.group_by(["__psr_ticker", "__psr_ts"])
            .agg(pl.len().alias("__psr_pair_count"))
            .filter(pl.col("__psr_pair_count") > 1)
            .select((pl.col("__psr_pair_count") - 1).sum().alias("duplicate_count"))
        ),
        "duplicate_count",
    )

    daily = auditable.group_by(["__psr_ticker", "__psr_et_date"]).agg(
        [
            pl.col("__psr_high").max().alias("__psr_daily_high"),
            pl.col("__psr_open").sort_by("__psr_ts").first().alias("__psr_daily_open"),
        ]
    )
    daily_ratio = (pl.col("__psr_daily_high") / pl.col("__psr_daily_open")).alias(
        "__psr_daily_high_open_ratio"
    )
    high_move = _collect_lazy(
        daily.with_columns(daily_ratio)
        .filter(pl.col("__psr_daily_open") > 0)
        .select(
            [
                (pl.col("__psr_daily_high_open_ratio") >= 1.10)
                .cast(pl.Int64)
                .sum()
                .alias("high_open_gte_1_10"),
                (pl.col("__psr_daily_high_open_ratio") >= 1.20)
                .cast(pl.Int64)
                .sum()
                .alias("high_open_gte_1_20"),
                (pl.col("__psr_daily_high_open_ratio") >= 1.50)
                .cast(pl.Int64)
                .sum()
                .alias("high_open_gte_1_50"),
            ]
        )
    ).to_dicts()[0]

    top_tickers = _collect_lazy(
        auditable.group_by("__psr_ticker")
        .agg(pl.len().alias("row_count"))
        .rename({"__psr_ticker": "ticker"})
        .sort(["row_count", "ticker"], descending=[True, False])
        .head(top_n)
    ).to_dicts()

    return {
        "row_count": int(metrics["row_count"] or 0),
        "ticker_count": int(metrics["ticker_count"] or 0),
        "timestamp": {
            "min": metrics.get("min_timestamp"),
            "max": metrics.get("max_timestamp"),
            "timezone_interpretation": "timestamps are converted to ET; naive timestamps are treated as UTC",
        },
        "ohlc_sanity_failure_count": int(metrics["ohlc_sanity_failure_count"] or 0),
        "duplicate_ticker_timestamp_count": duplicate_count,
        "extended_hours_row_counts": {
            "04:00-09:29_et": int(metrics["premarket_0400_0929"] or 0),
            "09:30-16:00_et": int(metrics["regular_0930_1600"] or 0),
            "16:00-20:00_et": int(metrics["afterhours_1600_2000"] or 0),
        },
        "low_price_candidate_counts": {
            "close_lte_10": int(metrics["close_lte_10"] or 0),
            "close_lte_5": int(metrics["close_lte_5"] or 0),
            "close_lte_1": int(metrics["close_lte_1"] or 0),
        },
        "high_move_candidate_day_counts": {
            "daily_high_open_gte_1_10": int(high_move["high_open_gte_1_10"] or 0),
            "daily_high_open_gte_1_20": int(high_move["high_open_gte_1_20"] or 0),
            "daily_high_open_gte_1_50": int(high_move["high_open_gte_1_50"] or 0),
        },
        "top_tickers_by_row_count": top_tickers,
    }


def _collect_lazy(lf):
    try:
        return lf.collect(engine="streaming")
    except TypeError:
        return lf.collect(streaming=True)


def _single_int(df, column: str) -> int:
    rows = df.to_dicts()
    if not rows:
        return 0
    value = rows[0].get(column)
    return int(value or 0)


def _candidate_day_lazy_frame(auditable, pl, options: HfCandidateDayOptions):
    minute = pl.col("__psr_et_minute")
    regular = minute.is_between(570, 959, closed="both")
    premarket = minute.is_between(240, 569, closed="both")
    early = minute.is_between(570, 630, closed="both")
    afternoon = minute.is_between(840, 930, closed="both")
    afterhours = minute.is_between(960, 1199, closed="both")
    volume = pl.col("__psr_volume").fill_null(0.0)
    dollar_volume = (pl.col("__psr_close") * volume).fill_null(0.0)
    o = pl.col("__psr_open")
    h = pl.col("__psr_high")
    l = pl.col("__psr_low")
    c = pl.col("__psr_close")
    ohlc_bad = (
        o.is_null()
        | h.is_null()
        | l.is_null()
        | c.is_null()
        | (o <= 0)
        | (h <= 0)
        | (l <= 0)
        | (c <= 0)
        | (h < l)
        | (h < o)
        | (h < c)
        | (l > o)
        | (l > c)
    ).fill_null(True)
    daily = (
        auditable.with_columns(dollar_volume.alias("__psr_dollar_volume"))
        .group_by(["__psr_ticker", "__psr_et_date"])
        .agg(
            [
                pl.len().alias("row_count"),
                premarket.cast(pl.Int64).sum().alias("premarket_row_count"),
                regular.cast(pl.Int64).sum().alias("regular_row_count"),
                afterhours.cast(pl.Int64).sum().alias("afterhours_row_count"),
                ohlc_bad.cast(pl.Int64).sum().alias("ohlc_sanity_failure_count"),
                pl.col("__psr_open")
                .filter(regular)
                .sort_by(pl.col("__psr_ts").filter(regular))
                .first()
                .alias("regular_open_price"),
                pl.col("__psr_close")
                .filter(regular)
                .sort_by(pl.col("__psr_ts").filter(regular))
                .last()
                .alias("regular_close_price"),
                pl.col("__psr_high").filter(regular).max().alias("regular_high_price"),
                pl.col("__psr_low").filter(regular).min().alias("regular_low_price"),
                pl.col("__psr_high").max().alias("full_day_high_price"),
                pl.col("__psr_low").min().alias("full_day_low_price"),
                pl.col("__psr_volume").sum().alias("full_day_volume"),
                pl.col("__psr_dollar_volume").sum().alias("full_day_dollar_volume"),
                pl.col("__psr_high").filter(early).max().alias("early_high_price"),
                pl.col("__psr_close")
                .filter(early)
                .sort_by(pl.col("__psr_ts").filter(early))
                .last()
                .alias("early_close_price"),
                pl.col("__psr_volume").filter(early).sum().alias("early_volume"),
                pl.col("__psr_dollar_volume").filter(early).sum().alias("early_dollar_volume"),
                pl.col("__psr_close")
                .filter(afternoon)
                .sort_by(pl.col("__psr_ts").filter(afternoon))
                .first()
                .alias("afternoon_start_price"),
                pl.col("__psr_high").filter(afternoon).max().alias("afternoon_high_price"),
                pl.col("__psr_volume").filter(afternoon).sum().alias("afternoon_volume"),
                pl.col("__psr_dollar_volume").filter(afternoon).sum().alias("afternoon_dollar_volume"),
            ]
        )
        .rename({"__psr_ticker": "ticker", "__psr_et_date": "market_date"})
    )
    daily = daily.with_columns(
        [
            pl.col("market_date").dt.strftime("%Y-%m").alias("market_month"),
            (((pl.col("early_high_price") / pl.col("regular_open_price")) - 1.0) * 100.0)
            .fill_null(0.0)
            .alias("early_high_from_open_pct"),
            (((pl.col("afternoon_high_price") / pl.col("afternoon_start_price")) - 1.0) * 100.0)
            .fill_null(0.0)
            .alias("afternoon_high_from_start_pct"),
            (((pl.col("full_day_high_price") / pl.col("regular_open_price")) - 1.0) * 100.0)
            .fill_null(0.0)
            .alias("full_day_high_from_open_pct"),
        ]
    )
    low_price = pl.col("regular_open_price").is_between(
        options.min_price,
        options.max_price,
        closed="both",
    )
    daily = daily.with_columns(
        [
            low_price.fill_null(False).cast(pl.Int64).alias("low_price_universe"),
            (
                low_price
                & (pl.col("regular_row_count") >= options.min_regular_rows)
                & (pl.col("early_dollar_volume") >= options.min_early_dollar_volume)
                & (pl.col("early_high_from_open_pct") >= options.min_early_move_pct)
            )
            .fill_null(False)
            .cast(pl.Int64)
            .alias("early_volume_momentum_candidate"),
            (
                low_price
                & (pl.col("regular_row_count") >= options.min_regular_rows)
                & (pl.col("afternoon_dollar_volume") >= options.min_afternoon_dollar_volume)
                & (pl.col("afternoon_high_from_start_pct") >= options.min_afternoon_move_pct)
            )
            .fill_null(False)
            .cast(pl.Int64)
            .alias("afternoon_runner_candidate"),
            (
                low_price
                & (pl.col("full_day_high_from_open_pct") >= options.min_daily_high_open_pct)
            )
            .fill_null(False)
            .cast(pl.Int64)
            .alias("posthoc_high_move_label"),
            (pl.col("regular_row_count") < options.min_regular_rows)
            .fill_null(True)
            .cast(pl.Int64)
            .alias("blocked_low_regular_session_coverage"),
        ]
    )
    return daily.with_columns(
        [
            (
                (pl.col("early_volume_momentum_candidate") == 1)
                | (pl.col("afternoon_runner_candidate") == 1)
                | (pl.col("posthoc_high_move_label") == 1)
            )
            .cast(pl.Int64)
            .alias("gross_candidate_day"),
            pl.lit(
                "gross_ohlcv_only_missing_l1_news_float_halt_kis_tradability"
            ).alias("blocked_context_reasons"),
            pl.lit(False).alias("decision_grade"),
            pl.lit("none").alias("cost_grade"),
        ]
    )


def _candidate_event_lazy_frame(
    auditable,
    pl,
    options: HfCandidateEventOptions,
    *,
    event_minutes: tuple[int, ...],
    forward_windows: tuple[int, ...],
):
    frames = [
        _candidate_event_for_minute_lazy_frame(
            auditable,
            pl,
            options,
            event_minute=event_minute,
            forward_windows=forward_windows,
        )
        for event_minute in event_minutes
    ]
    return pl.concat(frames, how="vertical_relaxed")


def _candidate_event_for_minute_lazy_frame(
    auditable,
    pl,
    options: HfCandidateEventOptions,
    *,
    event_minute: int,
    forward_windows: tuple[int, ...],
):
    minute = pl.col("__psr_et_minute")
    regular = minute.is_between(570, 959, closed="both")
    to_event = minute.is_between(570, event_minute, closed="both")
    volume = pl.col("__psr_volume").fill_null(0.0)
    dollar_volume = (pl.col("__psr_close") * volume).fill_null(0.0)
    o = pl.col("__psr_open")
    h = pl.col("__psr_high")
    l = pl.col("__psr_low")
    c = pl.col("__psr_close")
    ohlc_bad = (
        o.is_null()
        | h.is_null()
        | l.is_null()
        | c.is_null()
        | (o <= 0)
        | (h <= 0)
        | (l <= 0)
        | (c <= 0)
        | (h < l)
        | (h < o)
        | (h < c)
        | (l > o)
        | (l > c)
    ).fill_null(True)
    aggregations = [
        to_event.cast(pl.Int64).sum().alias("rows_to_event"),
        ohlc_bad.filter(to_event).cast(pl.Int64).sum().alias("event_ohlc_sanity_failure_count"),
        pl.col("__psr_open")
        .filter(regular)
        .sort_by(pl.col("__psr_ts").filter(regular))
        .first()
        .alias("regular_open_price"),
        pl.col("__psr_et_minute").filter(to_event).max().alias("latest_bar_minute_to_event"),
        pl.col("__psr_close")
        .filter(to_event)
        .sort_by(pl.col("__psr_ts").filter(to_event))
        .last()
        .alias("event_price"),
        pl.col("__psr_high").filter(to_event).max().alias("event_high_so_far"),
        pl.col("__psr_low").filter(to_event).min().alias("event_low_so_far"),
        pl.col("__psr_volume").filter(to_event).sum().alias("event_volume_to_time"),
        dollar_volume.filter(to_event).sum().alias("event_dollar_volume_to_time"),
    ]
    for window in forward_windows:
        forward = regular & minute.is_between(
            event_minute + 1,
            event_minute + window,
            closed="both",
        )
        aggregations.extend(
            [
                forward.cast(pl.Int64).sum().alias(f"forward_{window}m_row_count"),
                pl.col("__psr_close")
                .filter(forward)
                .sort_by(pl.col("__psr_ts").filter(forward))
                .last()
                .alias(f"forward_{window}m_close_price"),
                pl.col("__psr_high").filter(forward).max().alias(f"forward_{window}m_high_price"),
                pl.col("__psr_low").filter(forward).min().alias(f"forward_{window}m_low_price"),
            ]
        )
    event = (
        auditable.with_columns(dollar_volume.alias("__psr_dollar_volume"))
        .group_by(["__psr_ticker", "__psr_et_date"])
        .agg(aggregations)
        .rename({"__psr_ticker": "ticker", "__psr_et_date": "market_date"})
    )
    event = event.with_columns(
        [
            pl.col("market_date").dt.strftime("%Y-%m").alias("market_month"),
            pl.lit(_minute_to_hhmm(event_minute)).alias("event_time_et"),
            pl.lit(_event_time_bucket(event_minute)).alias("time_bucket"),
            (pl.lit(event_minute) - pl.col("latest_bar_minute_to_event"))
            .alias("event_staleness_minutes"),
            (((pl.col("event_price") / pl.col("regular_open_price")) - 1.0) * 100.0)
            .alias("event_return_from_open_pct"),
            (((pl.col("event_high_so_far") / pl.col("regular_open_price")) - 1.0) * 100.0)
            .alias("event_high_from_open_pct"),
            (((pl.col("event_low_so_far") / pl.col("regular_open_price")) - 1.0) * 100.0)
            .alias("event_low_from_open_pct"),
        ]
    )
    forward_exprs = []
    for window in forward_windows:
        forward_exprs.extend(
            [
                (((pl.col(f"forward_{window}m_close_price") / pl.col("event_price")) - 1.0) * 100.0)
                .alias(f"forward_{window}m_return_pct"),
                (((pl.col(f"forward_{window}m_high_price") / pl.col("event_price")) - 1.0) * 100.0)
                .alias(f"forward_{window}m_max_up_pct"),
                (((pl.col(f"forward_{window}m_low_price") / pl.col("event_price")) - 1.0) * 100.0)
                .alias(f"forward_{window}m_max_down_pct"),
            ]
        )
    event = event.with_columns(forward_exprs)
    low_price = pl.col("event_price").is_between(
        options.min_price,
        options.max_price,
        closed="both",
    )
    event = event.with_columns(
        [
            low_price.fill_null(False).cast(pl.Int64).alias("low_price_universe"),
            (
                low_price
                & (pl.col("rows_to_event") >= options.min_rows_to_event)
                & (
                    pl.col("event_staleness_minutes")
                    <= options.max_event_staleness_minutes
                )
                & (pl.col("event_dollar_volume_to_time") >= options.min_event_dollar_volume)
                & (pl.col("event_high_from_open_pct") >= options.min_event_move_pct)
            )
            .fill_null(False)
            .cast(pl.Int64)
            .alias("gross_candidate_event"),
            (pl.col("rows_to_event") < options.min_rows_to_event)
            .fill_null(True)
            .cast(pl.Int64)
            .alias("blocked_low_history_to_event"),
            (
                pl.col("event_staleness_minutes")
                > options.max_event_staleness_minutes
            )
            .fill_null(True)
            .cast(pl.Int64)
            .alias("blocked_stale_event_price"),
            pl.lit(
                "gross_ohlcv_event_only_missing_l1_news_float_halt_kis_tradability"
            ).alias("blocked_context_reasons"),
            pl.lit(False).alias("decision_grade"),
            pl.lit("none").alias("cost_grade"),
        ]
    )
    selected = [
        "ticker",
        "market_date",
        "market_month",
        "event_time_et",
        "time_bucket",
        "rows_to_event",
        "event_ohlc_sanity_failure_count",
        "regular_open_price",
        "latest_bar_minute_to_event",
        "event_staleness_minutes",
        "event_price",
        "event_high_so_far",
        "event_low_so_far",
        "event_volume_to_time",
        "event_dollar_volume_to_time",
        "event_return_from_open_pct",
        "event_high_from_open_pct",
        "event_low_from_open_pct",
    ]
    for window in forward_windows:
        selected.extend(
            [
                f"forward_{window}m_row_count",
                f"forward_{window}m_close_price",
                f"forward_{window}m_high_price",
                f"forward_{window}m_low_price",
                f"forward_{window}m_return_pct",
                f"forward_{window}m_max_up_pct",
                f"forward_{window}m_max_down_pct",
            ]
        )
    selected.extend(
        [
            "low_price_universe",
            "gross_candidate_event",
            "blocked_low_history_to_event",
            "blocked_stale_event_price",
            "blocked_context_reasons",
            "decision_grade",
            "cost_grade",
        ]
    )
    return event.select(selected)


def _candidate_date_bounds(auditable, pl, options: Any) -> tuple[date, date]:
    if options.start_date is not None:
        start = options.start_date
    else:
        row = _collect_lazy(
            auditable.select(pl.col("__psr_et_date").min().alias("min_date"))
        ).to_dicts()[0]
        value = row.get("min_date")
        if value is None:
            raise Hf1mBarsAuditError("HF candidate-day segmentation found no dated rows.")
        start = _coerce_date(value)

    if options.end_date is not None:
        end_exclusive = options.end_date + timedelta(days=1)
    else:
        row = _collect_lazy(
            auditable.select(pl.col("__psr_et_date").max().alias("max_date"))
        ).to_dicts()[0]
        value = row.get("max_date")
        if value is None:
            raise Hf1mBarsAuditError("HF candidate-day segmentation found no dated rows.")
        end_exclusive = _coerce_date(value) + timedelta(days=1)

    if end_exclusive <= start:
        raise Hf1mBarsAuditError(
            f"Invalid candidate-day date range: start={start} end_exclusive={end_exclusive}"
        )
    return start, end_exclusive


def _iter_date_chunks(
    start: date,
    end_exclusive: date,
    chunk_months: int,
) -> Iterable[tuple[date, date]]:
    current = start
    months = max(int(chunk_months), 1)
    while current < end_exclusive:
        next_date = min(_add_months(current, months), end_exclusive)
        yield current, next_date
        current = next_date


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _event_minutes(values: tuple[str, ...]) -> tuple[int, ...]:
    return tuple(sorted({_parse_hhmm(value) for value in values}))


def _parse_hhmm(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise Hf1mBarsAuditError(f"Invalid event time `{value}`. Use HH:MM ET.")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise Hf1mBarsAuditError(f"Invalid event time `{value}`. Use HH:MM ET.") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise Hf1mBarsAuditError(f"Invalid event time `{value}`. Use HH:MM ET.")
    return hour * 60 + minute


def _minute_to_hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _event_time_bucket(event_minute: int) -> str:
    if 570 <= event_minute <= 584:
        return "open_fakeout_risk"
    if 585 <= event_minute <= 630:
        return "real_money_morning"
    if 631 <= event_minute <= 720:
        return "late_morning_dead_zone"
    if 840 <= event_minute <= 930:
        return "afternoon_runner_window"
    if 931 <= event_minute <= 960:
        return "closing_ramp_dump_window"
    return "other_event_window"


def _empty_candidate_day_df(pl):
    return pl.DataFrame(
        schema={
            "ticker": pl.Utf8,
            "market_date": pl.Date,
            "row_count": pl.Int64,
            "premarket_row_count": pl.Int64,
            "regular_row_count": pl.Int64,
            "afterhours_row_count": pl.Int64,
            "ohlc_sanity_failure_count": pl.Int64,
            "regular_open_price": pl.Float64,
            "regular_close_price": pl.Float64,
            "regular_high_price": pl.Float64,
            "regular_low_price": pl.Float64,
            "full_day_high_price": pl.Float64,
            "full_day_low_price": pl.Float64,
            "full_day_volume": pl.Float64,
            "full_day_dollar_volume": pl.Float64,
            "early_high_price": pl.Float64,
            "early_close_price": pl.Float64,
            "early_volume": pl.Float64,
            "early_dollar_volume": pl.Float64,
            "afternoon_start_price": pl.Float64,
            "afternoon_high_price": pl.Float64,
            "afternoon_volume": pl.Float64,
            "afternoon_dollar_volume": pl.Float64,
            "market_month": pl.Utf8,
            "early_high_from_open_pct": pl.Float64,
            "afternoon_high_from_start_pct": pl.Float64,
            "full_day_high_from_open_pct": pl.Float64,
            "low_price_universe": pl.Int64,
            "early_volume_momentum_candidate": pl.Int64,
            "afternoon_runner_candidate": pl.Int64,
            "posthoc_high_move_label": pl.Int64,
            "blocked_low_regular_session_coverage": pl.Int64,
            "gross_candidate_day": pl.Int64,
            "blocked_context_reasons": pl.Utf8,
            "decision_grade": pl.Boolean,
            "cost_grade": pl.Utf8,
        }
    )


def _empty_candidate_event_df(pl, *, forward_windows: tuple[int, ...]):
    schema = {
        "ticker": pl.Utf8,
        "market_date": pl.Date,
        "market_month": pl.Utf8,
        "event_time_et": pl.Utf8,
        "time_bucket": pl.Utf8,
        "rows_to_event": pl.Int64,
        "event_ohlc_sanity_failure_count": pl.Int64,
        "regular_open_price": pl.Float64,
        "latest_bar_minute_to_event": pl.Int64,
        "event_staleness_minutes": pl.Int64,
        "event_price": pl.Float64,
        "event_high_so_far": pl.Float64,
        "event_low_so_far": pl.Float64,
        "event_volume_to_time": pl.Float64,
        "event_dollar_volume_to_time": pl.Float64,
        "event_return_from_open_pct": pl.Float64,
        "event_high_from_open_pct": pl.Float64,
        "event_low_from_open_pct": pl.Float64,
    }
    for window in forward_windows:
        schema.update(
            {
                f"forward_{window}m_row_count": pl.Int64,
                f"forward_{window}m_close_price": pl.Float64,
                f"forward_{window}m_high_price": pl.Float64,
                f"forward_{window}m_low_price": pl.Float64,
                f"forward_{window}m_return_pct": pl.Float64,
                f"forward_{window}m_max_up_pct": pl.Float64,
                f"forward_{window}m_max_down_pct": pl.Float64,
            }
        )
    schema.update(
        {
            "low_price_universe": pl.Int64,
            "gross_candidate_event": pl.Int64,
            "blocked_low_history_to_event": pl.Int64,
            "blocked_stale_event_price": pl.Int64,
            "blocked_context_reasons": pl.Utf8,
            "decision_grade": pl.Boolean,
            "cost_grade": pl.Utf8,
        }
    )
    return pl.DataFrame(schema=schema)


def _candidate_day_summary(
    candidate_df,
    *,
    options: HfCandidateDayOptions,
    run_id: str,
    path: Path,
    resolved: ResolvedDataPath,
    schema: dict[str, Any],
    mapping: dict[str, str],
    pl,
    chunks: list[tuple[date, date]],
) -> dict[str, Any]:
    candidate_count = int(candidate_df.height)
    active_months = _df_n_unique(candidate_df, "market_month")
    early_count = _df_sum_int(candidate_df, "early_volume_momentum_candidate")
    afternoon_count = _df_sum_int(candidate_df, "afternoon_runner_candidate")
    high_move_count = _df_sum_int(candidate_df, "posthoc_high_move_label")
    low_coverage_count = _df_sum_int(candidate_df, "blocked_low_regular_session_coverage")
    top_tickers = _top_counts(candidate_df, pl, "ticker", total=candidate_count, top_n=options.top_n)
    top_months = _top_counts(candidate_df, pl, "market_month", total=candidate_count, top_n=options.top_n)
    top1_ticker_pct = float(top_tickers[0]["pct_of_candidates"]) if top_tickers else 0.0
    top10_ticker_pct = round(sum(float(row["pct_of_candidates"]) for row in top_tickers[:10]), 6)
    top_month_pct = float(top_months[0]["pct_of_candidates"]) if top_months else 0.0
    gate_status, gate_reasons = _candidate_day_gate(
        candidate_count=candidate_count,
        active_months=active_months,
        top1_ticker_pct=top1_ticker_pct,
        top10_ticker_pct=top10_ticker_pct,
        top_month_pct=top_month_pct,
        options=options,
    )
    stat = path.stat()
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "dataset": DATASET_NAME,
            "scope": "hf_1m_candidate_day_segmentation_gross_only",
            "decision_grade": False,
            "cost_grade": "none",
            "cost_grade_reason": "Candidate days are derived from 1m OHLCV only; no bid/ask, NBBO, L2, news, float, halt, or KIS tradability evidence.",
            "pass_meaning": "PASS only means there are enough gross candidate days to continue falsification work. It does not approve setup backtests or live trading.",
        },
        "path_resolution": _path_resolution_payload(resolved),
        "file": {
            "path": str(path),
            "exists": True,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        },
        "schema": {
            "columns": [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()],
            "column_mapping": mapping,
        },
        "thresholds": {
            "min_price": options.min_price,
            "max_price": options.max_price,
            "min_regular_rows": options.min_regular_rows,
            "min_early_dollar_volume": options.min_early_dollar_volume,
            "min_early_move_pct": options.min_early_move_pct,
            "min_afternoon_dollar_volume": options.min_afternoon_dollar_volume,
            "min_afternoon_move_pct": options.min_afternoon_move_pct,
            "min_daily_high_open_pct": options.min_daily_high_open_pct,
            "min_candidate_days": options.min_candidate_days,
            "min_active_months": options.min_active_months,
            "max_top_ticker_pct": options.max_top_ticker_pct,
            "max_top10_ticker_pct": options.max_top10_ticker_pct,
            "max_top_month_pct": options.max_top_month_pct,
            "chunk_months": options.chunk_months,
        },
        "processing": {
            "start_date": chunks[0][0].isoformat() if chunks else None,
            "end_date": (chunks[-1][1] - timedelta(days=1)).isoformat() if chunks else None,
            "chunk_count": len(chunks),
            "chunks": [
                {
                    "start_date": start.isoformat(),
                    "end_date": (end - timedelta(days=1)).isoformat(),
                }
                for start, end in chunks
            ],
        },
        "candidate_day_count": candidate_count,
        "active_candidate_month_count": active_months,
        "candidate_flag_counts": {
            "early_volume_momentum_candidate": early_count,
            "afternoon_runner_candidate": afternoon_count,
            "posthoc_high_move_label": high_move_count,
            "blocked_low_regular_session_coverage": low_coverage_count,
        },
        "concentration": {
            "top1_ticker_pct": top1_ticker_pct,
            "top10_ticker_pct": top10_ticker_pct,
            "top_month_pct": top_month_pct,
            "top_tickers": top_tickers,
            "top_months": top_months,
        },
        "candidate_day_gate": {
            "status": gate_status,
            "reasons": gate_reasons,
        },
    }


def _candidate_event_summary(
    event_df,
    *,
    options: HfCandidateEventOptions,
    run_id: str,
    path: Path,
    resolved: ResolvedDataPath,
    schema: dict[str, Any],
    mapping: dict[str, str],
    pl,
    chunks: list[tuple[date, date]],
    event_times: tuple[str, ...],
    forward_windows: tuple[int, ...],
) -> dict[str, Any]:
    event_count = int(event_df.height)
    active_months = _df_n_unique(event_df, "market_month")
    blocked_low_history = _df_sum_int(event_df, "blocked_low_history_to_event")
    blocked_stale = _df_sum_int(event_df, "blocked_stale_event_price")
    top_tickers = _top_counts(
        event_df,
        pl,
        "ticker",
        total=event_count,
        top_n=options.top_n,
        count_label="candidate_event_count",
    )
    top_months = _top_counts(
        event_df,
        pl,
        "market_month",
        total=event_count,
        top_n=options.top_n,
        count_label="candidate_event_count",
    )
    top_event_times = _top_counts(
        event_df,
        pl,
        "event_time_et",
        total=event_count,
        top_n=len(event_times),
        count_label="candidate_event_count",
    )
    top_time_buckets = _top_counts(
        event_df,
        pl,
        "time_bucket",
        total=event_count,
        top_n=10,
        count_label="candidate_event_count",
    )
    top1_ticker_pct = float(top_tickers[0]["pct_of_candidates"]) if top_tickers else 0.0
    top10_ticker_pct = round(sum(float(row["pct_of_candidates"]) for row in top_tickers[:10]), 6)
    top_month_pct = float(top_months[0]["pct_of_candidates"]) if top_months else 0.0
    gate_status, gate_reasons = _candidate_event_gate(
        event_count=event_count,
        active_months=active_months,
        top1_ticker_pct=top1_ticker_pct,
        top10_ticker_pct=top10_ticker_pct,
        top_month_pct=top_month_pct,
        options=options,
    )
    stat = path.stat()
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "dataset": DATASET_NAME,
            "scope": "hf_1m_candidate_event_segmentation_gross_only",
            "decision_grade": False,
            "cost_grade": "none",
            "cost_grade_reason": "Candidate events are derived from 1m OHLCV only; no bid/ask, NBBO, L2, news, float, halt, or KIS tradability evidence.",
            "pass_meaning": "PASS only means there are enough event-time gross candidates to continue falsification work. It does not approve setup backtests or live trading.",
        },
        "path_resolution": _path_resolution_payload(resolved),
        "file": {
            "path": str(path),
            "exists": True,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        },
        "schema": {
            "columns": [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()],
            "column_mapping": mapping,
        },
        "thresholds": {
            "min_price": options.min_price,
            "max_price": options.max_price,
            "min_rows_to_event": options.min_rows_to_event,
            "max_event_staleness_minutes": options.max_event_staleness_minutes,
            "min_event_dollar_volume": options.min_event_dollar_volume,
            "min_event_move_pct": options.min_event_move_pct,
            "min_candidate_events": options.min_candidate_events,
            "min_active_months": options.min_active_months,
            "max_top_ticker_pct": options.max_top_ticker_pct,
            "max_top10_ticker_pct": options.max_top10_ticker_pct,
            "max_top_month_pct": options.max_top_month_pct,
            "event_times_et": list(event_times),
            "forward_windows_minutes": list(forward_windows),
            "chunk_months": options.chunk_months,
        },
        "processing": {
            "start_date": chunks[0][0].isoformat() if chunks else None,
            "end_date": (chunks[-1][1] - timedelta(days=1)).isoformat() if chunks else None,
            "chunk_count": len(chunks),
            "chunks": [
                {
                    "start_date": start.isoformat(),
                    "end_date": (end - timedelta(days=1)).isoformat(),
                }
                for start, end in chunks
            ],
        },
        "candidate_event_count": event_count,
        "active_candidate_month_count": active_months,
        "candidate_flag_counts": {
            "gross_candidate_event": event_count,
            "blocked_low_history_to_event": blocked_low_history,
            "blocked_stale_event_price": blocked_stale,
        },
        "concentration": {
            "top1_ticker_pct": top1_ticker_pct,
            "top10_ticker_pct": top10_ticker_pct,
            "top_month_pct": top_month_pct,
            "top_tickers": top_tickers,
            "top_months": top_months,
            "event_time_counts": top_event_times,
            "time_bucket_counts": top_time_buckets,
        },
        "candidate_event_gate": {
            "status": gate_status,
            "reasons": gate_reasons,
        },
    }


def _candidate_day_gate(
    *,
    candidate_count: int,
    active_months: int,
    top1_ticker_pct: float,
    top10_ticker_pct: float,
    top_month_pct: float,
    options: HfCandidateDayOptions,
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    failures: list[str] = []
    if candidate_count < options.min_candidate_days:
        blockers.append(
            f"candidate_day_count_lt_{options.min_candidate_days}: {candidate_count}"
        )
    if active_months < options.min_active_months:
        blockers.append(
            f"active_candidate_month_count_lt_{options.min_active_months}: {active_months}"
        )
    if top1_ticker_pct > options.max_top_ticker_pct:
        failures.append(
            f"top1_ticker_pct_gt_{options.max_top_ticker_pct}: {top1_ticker_pct}"
        )
    if top10_ticker_pct > options.max_top10_ticker_pct:
        failures.append(
            f"top10_ticker_pct_gt_{options.max_top10_ticker_pct}: {top10_ticker_pct}"
        )
    if top_month_pct > options.max_top_month_pct:
        failures.append(
            f"top_month_pct_gt_{options.max_top_month_pct}: {top_month_pct}"
        )
    if failures:
        return "FAIL", failures + blockers
    if blockers:
        return "BLOCKED", blockers
    return "PASS", ["gross_candidate_day_segmentation_passed_minimum_coverage_checks"]


def _candidate_event_gate(
    *,
    event_count: int,
    active_months: int,
    top1_ticker_pct: float,
    top10_ticker_pct: float,
    top_month_pct: float,
    options: HfCandidateEventOptions,
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    failures: list[str] = []
    if event_count < options.min_candidate_events:
        blockers.append(
            f"candidate_event_count_lt_{options.min_candidate_events}: {event_count}"
        )
    if active_months < options.min_active_months:
        blockers.append(
            f"active_candidate_month_count_lt_{options.min_active_months}: {active_months}"
        )
    if top1_ticker_pct > options.max_top_ticker_pct:
        failures.append(
            f"top1_ticker_pct_gt_{options.max_top_ticker_pct}: {top1_ticker_pct}"
        )
    if top10_ticker_pct > options.max_top10_ticker_pct:
        failures.append(
            f"top10_ticker_pct_gt_{options.max_top10_ticker_pct}: {top10_ticker_pct}"
        )
    if top_month_pct > options.max_top_month_pct:
        failures.append(
            f"top_month_pct_gt_{options.max_top_month_pct}: {top_month_pct}"
        )
    if failures:
        return "FAIL", failures + blockers
    if blockers:
        return "BLOCKED", blockers
    return "PASS", ["gross_candidate_event_segmentation_passed_minimum_coverage_checks"]


def _top_counts(
    df,
    pl,
    column: str,
    *,
    total: int,
    top_n: int,
    count_label: str = "candidate_day_count",
) -> list[dict[str, Any]]:
    if total <= 0 or column not in df.columns:
        return []
    top = (
        df.group_by(column)
        .agg(pl.len().alias(count_label))
        .sort([count_label, column], descending=[True, False])
        .head(top_n)
        .with_columns(
            ((pl.col(count_label) / float(total)) * 100.0)
            .round(6)
            .alias("pct_of_candidates")
        )
    )
    rows = top.to_dicts()
    return [{str(column): str(row[column]), **{k: v for k, v in row.items() if k != column}} for row in rows]


def _df_sum_int(df, column: str) -> int:
    if df.height == 0 or column not in df.columns:
        return 0
    value = df.select(pl_col_sum(column)).to_dicts()[0].get(column)
    return int(value or 0)


def pl_col_sum(column: str):
    pl = _load_polars()
    return pl.col(column).sum().alias(column)


def _df_n_unique(df, column: str) -> int:
    if df.height == 0 or column not in df.columns:
        return 0
    return int(df.select(_load_polars().col(column).n_unique().alias("n")).to_dicts()[0]["n"] or 0)


def _path_resolution_payload(resolved: ResolvedDataPath) -> dict[str, str | None]:
    return {
        "source": resolved.source,
        "data_root": str(resolved.data_root) if resolved.data_root is not None else None,
        "path": str(resolved.path),
    }


def _markdown_summary(summary: dict[str, Any]) -> str:
    metadata = summary.get("metadata", {})
    file_info = summary.get("file", {})
    ts = summary.get("timestamp", {})
    hours = summary.get("extended_hours_row_counts", {})
    low = summary.get("low_price_candidate_counts", {})
    high_move = summary.get("high_move_candidate_day_counts", {})
    lines = [
        "# Hugging Face 1m Bars Audit",
        "",
        f"- Dataset: `{metadata.get('dataset')}`",
        f"- File: `{file_info.get('path')}`",
        f"- Size bytes: {file_info.get('size_bytes')}",
        f"- Modified: {file_info.get('mtime')}",
        f"- Rows: {summary.get('row_count', 0)}",
        f"- Tickers: {summary.get('ticker_count', 0)}",
        f"- Timestamp range: {ts.get('min')} -> {ts.get('max')}",
        f"- OHLC sanity failures: {summary.get('ohlc_sanity_failure_count', 0)}",
        f"- Duplicate ticker/timestamp rows: {summary.get('duplicate_ticker_timestamp_count', 0)}",
        f"- Decision grade: `{metadata.get('decision_grade')}`",
        f"- Cost grade: `{metadata.get('cost_grade')}`",
        "",
        "## Session Counts",
        "",
        f"- 04:00-09:29 ET: {hours.get('04:00-09:29_et', 0)}",
        f"- 09:30-16:00 ET: {hours.get('09:30-16:00_et', 0)}",
        f"- 16:00-20:00 ET: {hours.get('16:00-20:00_et', 0)}",
        "",
        "## Candidate Counts",
        "",
        f"- close <= 10: {low.get('close_lte_10', 0)}",
        f"- close <= 5: {low.get('close_lte_5', 0)}",
        f"- close <= 1: {low.get('close_lte_1', 0)}",
        f"- daily high/open >= 1.10: {high_move.get('daily_high_open_gte_1_10', 0)}",
        f"- daily high/open >= 1.20: {high_move.get('daily_high_open_gte_1_20', 0)}",
        f"- daily high/open >= 1.50: {high_move.get('daily_high_open_gte_1_50', 0)}",
        "",
        "## Caveat",
        "",
        "- This dataset is gross OHLCV falsification data only.",
        "- It does not provide cost-grade execution evidence.",
    ]
    return "\n".join(lines) + "\n"


def _candidate_day_markdown(summary: dict[str, Any]) -> str:
    metadata = summary.get("metadata", {})
    gate = summary.get("candidate_day_gate", {})
    flags = summary.get("candidate_flag_counts", {})
    concentration = summary.get("concentration", {})
    thresholds = summary.get("thresholds", {})
    lines = [
        "# HF Candidate-Day Segmentation",
        "",
        f"- Dataset: `{metadata.get('dataset')}`",
        f"- Scope: `{metadata.get('scope')}`",
        f"- Decision grade: `{metadata.get('decision_grade')}`",
        f"- Cost grade: `{metadata.get('cost_grade')}`",
        f"- Candidate-day gate: `{gate.get('status')}`",
        f"- Candidate days: {summary.get('candidate_day_count', 0)}",
        f"- Active candidate months: {summary.get('active_candidate_month_count', 0)}",
        "",
        "## Flag Counts",
        "",
        f"- Early volume momentum: {flags.get('early_volume_momentum_candidate', 0)}",
        f"- Afternoon runner: {flags.get('afternoon_runner_candidate', 0)}",
        f"- Posthoc high-move label: {flags.get('posthoc_high_move_label', 0)}",
        f"- Low regular-session coverage: {flags.get('blocked_low_regular_session_coverage', 0)}",
        "",
        "## Concentration",
        "",
        f"- Top 1 ticker %: {concentration.get('top1_ticker_pct', 0)}",
        f"- Top 10 ticker %: {concentration.get('top10_ticker_pct', 0)}",
        f"- Top month %: {concentration.get('top_month_pct', 0)}",
        "",
        "## Thresholds",
        "",
        f"- Price: {thresholds.get('min_price')} to {thresholds.get('max_price')}",
        f"- Min regular rows: {thresholds.get('min_regular_rows')}",
        f"- Min candidate days: {thresholds.get('min_candidate_days')}",
        f"- Min active months: {thresholds.get('min_active_months')}",
        "",
        "## Gate Reasons",
        "",
    ]
    for reason in gate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- PASS only means gross OHLCV candidate-day coverage is sufficient for falsification work.",
            "- These rows are not setup backtest results and do not approve live execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def _candidate_event_markdown(summary: dict[str, Any]) -> str:
    metadata = summary.get("metadata", {})
    gate = summary.get("candidate_event_gate", {})
    flags = summary.get("candidate_flag_counts", {})
    concentration = summary.get("concentration", {})
    thresholds = summary.get("thresholds", {})
    lines = [
        "# HF Candidate-Event Segmentation",
        "",
        f"- Dataset: `{metadata.get('dataset')}`",
        f"- Scope: `{metadata.get('scope')}`",
        f"- Decision grade: `{metadata.get('decision_grade')}`",
        f"- Cost grade: `{metadata.get('cost_grade')}`",
        f"- Candidate-event gate: `{gate.get('status')}`",
        f"- Candidate events: {summary.get('candidate_event_count', 0)}",
        f"- Active candidate months: {summary.get('active_candidate_month_count', 0)}",
        "",
        "## Event Windows",
        "",
        f"- Event times ET: {', '.join(thresholds.get('event_times_et', []))}",
        f"- Forward windows minutes: {thresholds.get('forward_windows_minutes', [])}",
        "",
        "## Flag Counts",
        "",
        f"- Gross candidate events: {flags.get('gross_candidate_event', 0)}",
        f"- Low history to event: {flags.get('blocked_low_history_to_event', 0)}",
        "",
        "## Concentration",
        "",
        f"- Top 1 ticker %: {concentration.get('top1_ticker_pct', 0)}",
        f"- Top 10 ticker %: {concentration.get('top10_ticker_pct', 0)}",
        f"- Top month %: {concentration.get('top_month_pct', 0)}",
        "",
        "## Thresholds",
        "",
        f"- Price: {thresholds.get('min_price')} to {thresholds.get('max_price')}",
        f"- Min rows to event: {thresholds.get('min_rows_to_event')}",
        f"- Max event staleness minutes: {thresholds.get('max_event_staleness_minutes')}",
        f"- Min event dollar volume: {thresholds.get('min_event_dollar_volume')}",
        f"- Min event move pct: {thresholds.get('min_event_move_pct')}",
        f"- Min candidate events: {thresholds.get('min_candidate_events')}",
        "",
        "## Gate Reasons",
        "",
    ]
    for reason in gate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- Candidate generation uses only information available at the event time.",
            "- Forward returns/max-up/max-down are diagnostic outcome columns only.",
            "- These rows are not setup backtest results and do not approve live execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    for index in range(2, 1000):
        candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate a unique export directory for {requested_dir}")
