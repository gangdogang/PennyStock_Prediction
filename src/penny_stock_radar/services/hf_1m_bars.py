from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    }
    missing = [name for name, column in mapping.items() if column is None]
    if missing:
        raise Hf1mBarsAuditError(
            "HF 1m bars parquet is missing required columns: "
            f"{', '.join(missing)}. Available columns: {', '.join(columns)}"
        )
    return {name: str(column) for name, column in mapping.items()}


def _first_column(lower_to_original: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in lower_to_original:
            return lower_to_original[candidate]
    return None


def _with_audit_columns(lf, schema: dict[str, Any], mapping: dict[str, str], pl):
    ts_col = mapping["timestamp"]
    ts_expr = _timestamp_expr(pl, ts_col, schema.get(ts_col))
    et_ts = ts_expr.dt.convert_time_zone(EASTERN_TZ)
    return lf.with_columns(
        [
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
    )


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


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    for index in range(2, 1000):
        candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate a unique export directory for {requested_dir}")
