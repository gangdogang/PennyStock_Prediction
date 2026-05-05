from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable

from .paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
    write_csv,
)

DEFAULT_SIGNAL_ENTRY_LABELS = ("OPENING_RANGE_CANDIDATE",)
DEFAULT_SIGNAL_SETUP_STATES = ("STARTER_VALID",)
CANONICAL_BUCKETS = {
    "predictor_weighted": PREDICTOR_WEIGHTED_BUCKET,
    "watchlist_momentum": MOMENTUM_ONLY_BUCKET,
    "momentum_only": MOMENTUM_ONLY_BUCKET,
    "watchlist_blind_momentum": WATCHLIST_BLIND_MOMENTUM_BUCKET,
}
AUDIT_CSV_NAMES = {
    "overall": "entry_signal_overall.csv",
    "by_month": "entry_signal_by_month.csv",
    "by_symbol": "entry_signal_by_symbol.csv",
    "by_date": "entry_signal_by_date.csv",
    "feature_1r": "entry_signal_feature_1r.csv",
}


@dataclass(frozen=True)
class EntrySignalAuditResult:
    report: dict[str, Any]
    output_path: Path | None
    csv_paths: dict[str, Path]


@dataclass
class _RunArtifacts:
    run_dir: Path
    run_id: str
    manifest: dict[str, Any]
    summary: dict[str, Any]
    trade_rows: list[dict[str, str]]
    setup_features: dict[tuple[str, str, str, str, str], dict[str, str]]
    missing_artifacts: list[str]


@dataclass(frozen=True)
class _FeatureSpec:
    name: str
    bucket_fn: Callable[[dict[str, str]], str]


class PremktEntrySignalAuditor:
    """Audit whether an entry-label/setup-state signal is robust or concentrated."""

    def audit(
        self,
        *,
        run_dirs: Iterable[Path],
        output_path: Path | None = None,
        csv_dir: Path | None = None,
        entry_labels: tuple[str, ...] | None = DEFAULT_SIGNAL_ENTRY_LABELS,
        entry_setup_states: tuple[str, ...] | None = DEFAULT_SIGNAL_SETUP_STATES,
        buckets: tuple[str, ...] | None = None,
        top_n: int = 5,
    ) -> EntrySignalAuditResult:
        normalized_buckets = _normalize_buckets(buckets)
        normalized_labels = _normalize_filter_values(entry_labels)
        normalized_states = _normalize_filter_values(entry_setup_states)
        runs = [self._load_run(Path(run_dir)) for run_dir in run_dirs]
        selected_rows = self._selected_rows(
            runs,
            entry_labels=normalized_labels,
            entry_setup_states=normalized_states,
            buckets=normalized_buckets,
        )

        overall_rows = self._group_rows(
            selected_rows,
            keys=("run_id", "bucket"),
            top_n=top_n,
            include_stress=True,
        )
        by_month_rows = self._group_rows(
            selected_rows,
            keys=("run_id", "bucket", "month"),
            top_n=top_n,
        )
        by_symbol_rows = self._group_rows(
            selected_rows,
            keys=("run_id", "bucket", "symbol"),
            top_n=top_n,
        )
        by_date_rows = self._group_rows(
            selected_rows,
            keys=("run_id", "bucket", "market_date"),
            top_n=top_n,
        )
        self._attach_consistency_status(overall_rows, by_month_rows)
        feature_rows = self._feature_rows(selected_rows)
        warnings = self._warnings(
            runs=runs,
            selected_rows=selected_rows,
            overall_rows=overall_rows,
            by_month_rows=by_month_rows,
        )
        report: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "entry_labels": list(normalized_labels) if normalized_labels is not None else None,
                "entry_setup_states": list(normalized_states) if normalized_states is not None else None,
                "buckets": list(normalized_buckets) if normalized_buckets is not None else None,
                "top_n": top_n,
            },
            "run_dirs": [str(run.run_dir) for run in runs],
            "run_count": len(runs),
            "selected_trade_count": len(selected_rows),
            "overall": overall_rows,
            "by_month": by_month_rows,
            "top_symbols": _top_rows(by_symbol_rows, "total_net_pnl", limit=top_n),
            "worst_symbols": _bottom_rows(by_symbol_rows, "total_net_pnl", limit=top_n),
            "top_dates": _top_rows(by_date_rows, "total_net_pnl", limit=top_n),
            "worst_dates": _bottom_rows(by_date_rows, "total_net_pnl", limit=top_n),
            "warnings": warnings,
            "interpretation": (
                "This audit tests concentration and 1R separation only. It does not prove live edge, "
                "especially when replay data lacks historical bid/ask coverage."
            ),
        }

        csv_paths: dict[str, Path] = {}
        if csv_dir is not None:
            csv_dir.mkdir(parents=True, exist_ok=True)
            rows_by_name = {
                "overall": overall_rows,
                "by_month": by_month_rows,
                "by_symbol": by_symbol_rows,
                "by_date": by_date_rows,
                "feature_1r": feature_rows,
            }
            for key, filename in AUDIT_CSV_NAMES.items():
                path = csv_dir / filename
                write_csv(path, rows_by_name[key])
                csv_paths[key] = path

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return EntrySignalAuditResult(
            report=report,
            output_path=output_path,
            csv_paths=csv_paths,
        )

    def _load_run(self, run_dir: Path) -> _RunArtifacts:
        missing: list[str] = []
        manifest = _load_json(run_dir / "run_manifest.json", missing, allow_missing=True)
        summary = _load_json(run_dir / "replay_summary.json", missing, allow_missing=True)
        trade_rows = _read_csv(run_dir / "paper_trade_log.csv", missing)
        setup_feature_rows = _read_csv(
            run_dir / "paper_setup_features.csv",
            missing,
            allow_missing=True,
        )
        run_id = str(
            manifest.get("run_id")
            or summary.get("run_id")
            or run_dir.name
        )
        return _RunArtifacts(
            run_dir=run_dir,
            run_id=run_id,
            manifest=manifest,
            summary=summary,
            trade_rows=trade_rows,
            setup_features=_index_setup_features(setup_feature_rows),
            missing_artifacts=missing,
        )

    def _selected_rows(
        self,
        runs: list[_RunArtifacts],
        *,
        entry_labels: tuple[str, ...] | None,
        entry_setup_states: tuple[str, ...] | None,
        buckets: tuple[str, ...] | None,
    ) -> list[dict[str, str]]:
        selected: list[dict[str, str]] = []
        for run in runs:
            for raw in run.trade_rows:
                if str(raw.get("event") or "").upper() != "EXIT":
                    continue
                label = _entry_label(raw)
                setup_state = str(raw.get("entry_setup_state") or "").upper()
                bucket = _canonical_bucket(str(raw.get("bucket") or ""))
                if entry_labels is not None and label not in entry_labels:
                    continue
                if entry_setup_states is not None and setup_state not in entry_setup_states:
                    continue
                if buckets is not None and bucket not in buckets:
                    continue
                row = dict(raw)
                row["bucket"] = bucket
                row["entry_analysis_label"] = label
                row["entry_setup_state"] = setup_state
                row["run_id"] = run.run_id
                row["run_dir"] = str(run.run_dir)
                row["month"] = _month(row)
                feature = _matching_setup_feature(run.setup_features, row)
                if feature:
                    for key, value in feature.items():
                        row.setdefault(key, value)
                        if row.get(key) in (None, ""):
                            row[key] = value
                selected.append(row)
        return selected

    def _group_rows(
        self,
        rows: list[dict[str, str]],
        *,
        keys: tuple[str, ...],
        top_n: int,
        include_stress: bool = False,
    ) -> list[dict[str, object]]:
        grouped: dict[tuple[str, ...], list[dict[str, str]]] = {}
        for row in rows:
            grouped.setdefault(tuple(str(row.get(key) or "") for key in keys), []).append(row)

        output_rows: list[dict[str, object]] = []
        for key_values, group in sorted(grouped.items()):
            payload: dict[str, object] = dict(zip(keys, key_values, strict=True))
            payload.update(_trade_stats(group))
            if include_stress:
                payload.update(_stress_fields(group, "symbol", top_n=top_n))
                payload.update(_stress_fields(group, "market_date", top_n=top_n))
            output_rows.append(payload)
        return output_rows

    def _attach_consistency_status(
        self,
        overall_rows: list[dict[str, object]],
        by_month_rows: list[dict[str, object]],
    ) -> None:
        month_groups: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in by_month_rows:
            key = (str(row.get("run_id") or ""), str(row.get("bucket") or ""))
            month_groups.setdefault(key, []).append(row)

        for row in overall_rows:
            key = (str(row.get("run_id") or ""), str(row.get("bucket") or ""))
            months = month_groups.get(key, [])
            positive_months = sum(1 for item in months if _number(item.get("total_net_pnl")) > 0)
            positive_month_rate = positive_months / len(months) if months else 0.0
            row["positive_month_count"] = positive_months
            row["month_count"] = len(months)
            row["positive_month_rate"] = round(positive_month_rate, 6)
            row["signal_status"] = _signal_status(row, positive_month_rate)

    def _feature_rows(self, rows: list[dict[str, str]]) -> list[dict[str, object]]:
        feature_output: list[dict[str, object]] = []
        for scope_name, scoped_rows in _feature_scopes(rows):
            for feature in _feature_specs():
                grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
                for row in scoped_rows:
                    bucket = str(row.get("bucket") or "")
                    value = feature.bucket_fn(row)
                    grouped.setdefault((bucket, feature.name, value), []).append(row)
                for (bucket, feature_name, feature_value), group in sorted(grouped.items()):
                    stats = _trade_stats(group)
                    feature_output.append(
                        {
                            "scope": scope_name[0],
                            "scope_value": scope_name[1],
                            "bucket": bucket,
                            "feature": feature_name,
                            "feature_value": feature_value,
                            "trade_count": stats["trade_count"],
                            "reached_1r_count": stats["reached_1r_count"],
                            "reached_1r_rate": stats["reached_1r_rate"],
                            "stop_loss_rate": stats["stop_loss_rate"],
                            "total_net_pnl": stats["total_net_pnl"],
                            "avg_net_pnl": stats["avg_net_pnl"],
                            "median_net_pnl": stats["median_net_pnl"],
                            "avg_max_r_multiple": stats["avg_max_r_multiple"],
                            "median_max_r_multiple": stats["median_max_r_multiple"],
                        }
                    )
        feature_output.sort(
            key=lambda row: (
                str(row["scope"]),
                str(row["scope_value"]),
                str(row["bucket"]),
                str(row["feature"]),
                -int(row["trade_count"]),
                str(row["feature_value"]),
            )
        )
        return feature_output

    def _warnings(
        self,
        *,
        runs: list[_RunArtifacts],
        selected_rows: list[dict[str, str]],
        overall_rows: list[dict[str, object]],
        by_month_rows: list[dict[str, object]],
    ) -> list[str]:
        warnings: list[str] = []
        for run in runs:
            for artifact in run.missing_artifacts:
                warnings.append(f"{run.run_id}: missing artifact {artifact}")
        if not selected_rows:
            warnings.append("No closed trades matched the requested entry signal filters.")
        month_keys = {
            (str(row.get("run_id") or ""), str(row.get("bucket") or ""))
            for row in by_month_rows
        }
        for row in overall_rows:
            run_id = str(row.get("run_id") or "")
            bucket = str(row.get("bucket") or "")
            prefix = f"{run_id}/{bucket}"
            if (run_id, bucket) not in month_keys:
                warnings.append(f"{prefix}: no monthly rows for consistency check")
            if _number(row.get("trade_count")) < 30:
                warnings.append(f"{prefix}: sample size below 30 trades")
            if _number(row.get("total_net_pnl")) <= 0:
                warnings.append(f"{prefix}: selected signal is not profitable in this run")
            if _number(row.get("top_1_symbol_removed_net_pnl")) <= 0 < _number(row.get("total_net_pnl")):
                warnings.append(f"{prefix}: profit disappears after removing the top symbol")
            if _number(row.get("top_1_market_date_removed_net_pnl")) <= 0 < _number(row.get("total_net_pnl")):
                warnings.append(f"{prefix}: profit disappears after removing the top date")
            if _number(row.get("positive_month_rate")) < 0.67 and _number(row.get("month_count")) >= 2:
                warnings.append(f"{prefix}: month-to-month consistency is weak")
        return sorted(set(warnings))


def _load_json(path: Path, missing: list[str], *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        if not allow_missing:
            missing.append(path.name)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        missing.append(path.name)
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(
    path: Path,
    missing: list[str],
    *,
    allow_missing: bool = False,
) -> list[dict[str, str]]:
    if not path.exists():
        if not allow_missing:
            missing.append(path.name)
        return []
    if path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        if not allow_missing:
            missing.append(path.name)
        return []


def _index_setup_features(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str, str, str, str], dict[str, str]]:
    indexed: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (
            _canonical_bucket(str(row.get("bucket") or "")),
            str(row.get("symbol") or "").upper(),
            str(row.get("market_date") or ""),
            str(row.get("observed_at") or ""),
            str(row.get("position_state") or ""),
        )
        indexed[key] = row
    return indexed


def _matching_setup_feature(
    features: dict[tuple[str, str, str, str, str], dict[str, str]],
    row: dict[str, str],
) -> dict[str, str] | None:
    base = (
        _canonical_bucket(str(row.get("bucket") or "")),
        str(row.get("symbol") or "").upper(),
        str(row.get("market_date") or ""),
        str(row.get("entry_at") or ""),
    )
    return features.get((*base, "flat")) or features.get((*base, "open"))


def _normalize_filter_values(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        for part in str(value).split(","):
            item = part.strip().upper()
            if item == "ALL":
                return None
            if item and item not in normalized:
                normalized.append(item)
    return tuple(normalized)


def _normalize_buckets(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        for part in str(value).split(","):
            item = part.strip().lower()
            if item == "all":
                return None
            bucket = _canonical_bucket(item)
            if bucket and bucket not in normalized:
                normalized.append(bucket)
    return tuple(normalized)


def _canonical_bucket(value: str) -> str:
    normalized = str(value).strip().lower()
    return CANONICAL_BUCKETS.get(normalized, normalized)


def _entry_label(row: dict[str, Any]) -> str:
    return str(row.get("entry_analysis_label") or row.get("analysis_label") or "").upper()


def _month(row: dict[str, Any]) -> str:
    market_date = str(row.get("market_date") or "")
    if len(market_date) >= 7:
        return market_date[:7]
    entry_at = str(row.get("entry_at") or "")
    return entry_at[:7] if len(entry_at) >= 7 else ""


def _trade_stats(rows: list[dict[str, Any]]) -> dict[str, object]:
    pnl_values = [_number(row.get("net_pnl")) for row in rows]
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    max_r_values = [_number(row.get("max_r_multiple")) for row in rows if row.get("max_r_multiple") not in (None, "")]
    min_r_values = [_number(row.get("min_r_multiple")) for row in rows if row.get("min_r_multiple") not in (None, "")]
    holding_values = [_number(row.get("holding_minutes")) for row in rows if row.get("holding_minutes") not in (None, "")]
    first_1r_values = [_number(row.get("first_1r_minutes")) for row in rows if row.get("first_1r_minutes") not in (None, "")]
    reached_1r_count = sum(1 for row in rows if _truthy(row.get("reached_1r")))
    stop_loss_count = sum(1 for row in rows if str(row.get("exit_reason") or "") == "stop_loss")
    intrabar_stop_count = sum(1 for row in rows if _truthy(row.get("intrabar_stop_touched")))
    return {
        "trade_count": len(rows),
        "total_net_pnl": _round(sum(pnl_values)),
        "gross_profit": _round(sum(wins)),
        "gross_loss": _round(sum(losses)),
        "avg_net_pnl": _round(sum(pnl_values) / len(rows)) if rows else 0.0,
        "median_net_pnl": _round(median(pnl_values)) if pnl_values else 0.0,
        "win_rate": _round(len(wins) / len(rows)) if rows else 0.0,
        "profit_factor": _round(sum(wins) / abs(sum(losses))) if losses else 0.0,
        "stop_loss_count": stop_loss_count,
        "stop_loss_rate": _round(stop_loss_count / len(rows)) if rows else 0.0,
        "reached_1r_count": reached_1r_count,
        "reached_1r_rate": _round(reached_1r_count / len(rows)) if rows else 0.0,
        "intrabar_stop_touched_count": intrabar_stop_count,
        "intrabar_stop_touched_rate": _round(intrabar_stop_count / len(rows)) if rows else 0.0,
        "avg_max_r_multiple": _avg(max_r_values),
        "median_max_r_multiple": _round(median(max_r_values)) if max_r_values else 0.0,
        "avg_min_r_multiple": _avg(min_r_values),
        "median_min_r_multiple": _round(median(min_r_values)) if min_r_values else 0.0,
        "avg_holding_minutes": _avg(holding_values),
        "avg_first_1r_minutes": _avg(first_1r_values),
    }


def _stress_fields(rows: list[dict[str, Any]], key: str, *, top_n: int) -> dict[str, object]:
    grouped: dict[str, float] = {}
    for row in rows:
        group_key = str(row.get(key) or "")
        grouped[group_key] = grouped.get(group_key, 0.0) + _number(row.get("net_pnl"))
    ranked = sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    total_pnl = sum(grouped.values())
    total_positive_pnl = sum(value for value in grouped.values() if value > 0)
    fields: dict[str, object] = {}
    label = "market_date" if key == "market_date" else key
    for count in sorted({1, 3, max(1, top_n)}):
        removed_pnl = sum(value for _, value in ranked[:count])
        fields[f"top_{count}_{label}_removed_net_pnl"] = _round(total_pnl - removed_pnl)
    if ranked:
        top_key, top_pnl = ranked[0]
        fields[f"top_{label}"] = top_key
        fields[f"top_{label}_net_pnl"] = _round(top_pnl)
        fields[f"top_{label}_profit_share"] = (
            _round(top_pnl / total_positive_pnl) if top_pnl > 0 and total_positive_pnl > 0 else 0.0
        )
    else:
        fields[f"top_{label}"] = ""
        fields[f"top_{label}_net_pnl"] = 0.0
        fields[f"top_{label}_profit_share"] = 0.0
    return fields


def _feature_scopes(rows: list[dict[str, str]]) -> list[tuple[tuple[str, str], list[dict[str, str]]]]:
    scopes: list[tuple[tuple[str, str], list[dict[str, str]]]] = [(("all", "all"), rows)]
    by_month: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_month.setdefault(str(row.get("month") or ""), []).append(row)
    for month, group in sorted(by_month.items()):
        if month:
            scopes.append((("month", month), group))
    return scopes


def _feature_specs() -> tuple[_FeatureSpec, ...]:
    return (
        _FeatureSpec("entry_time_bucket", _entry_time_bucket),
        _FeatureSpec("analysis_score_bucket", lambda row: _numeric_bucket(row, "analysis_score", (3.0, 3.5, 4.0, 4.5))),
        _FeatureSpec("entry_setup_quality_bucket", lambda row: _numeric_bucket(row, "entry_setup_quality", (4.0, 6.0, 8.0))),
        _FeatureSpec("entry_setup_risk_bucket", lambda row: _numeric_bucket(row, "entry_setup_risk", (2.0, 4.0, 6.0))),
        _FeatureSpec("bar_dollar_volume_bucket", lambda row: _numeric_bucket(row, "bar_dollar_volume", (25_000.0, 100_000.0, 500_000.0, 1_000_000.0))),
        _FeatureSpec("dollar_volume_bucket", lambda row: _numeric_bucket(row, "dollar_volume", (100_000.0, 500_000.0, 1_000_000.0, 5_000_000.0))),
        _FeatureSpec("spread_pct_bucket", lambda row: _numeric_bucket(row, "spread_pct", (1.0, 3.0, 5.0, 10.0))),
        _FeatureSpec("distance_to_vwap_pct_bucket", lambda row: _numeric_bucket(row, "distance_to_vwap_pct", (-2.0, 0.0, 2.0, 5.0, 10.0))),
        _FeatureSpec("volume_bar_ratio_bucket", lambda row: _numeric_bucket(row, "volume_bar_ratio", (0.75, 1.0, 1.5, 2.5))),
        _FeatureSpec("minutes_since_hod_bucket", lambda row: _numeric_bucket(row, "minutes_since_hod", (1.0, 5.0, 15.0, 30.0))),
        _FeatureSpec("shares_pct_of_bar_volume_bucket", lambda row: _numeric_bucket(row, "shares_pct_of_bar_volume", (0.25, 0.5, 1.0, 2.0))),
        _FeatureSpec("notional_pct_of_bar_dollar_volume_bucket", lambda row: _numeric_bucket(row, "notional_pct_of_bar_dollar_volume", (0.25, 0.5, 1.0, 2.0))),
        _FeatureSpec("vwap_reclaim", lambda row: _binary_bucket(row, "vwap_reclaim")),
        _FeatureSpec("opening_range_breakout", lambda row: _binary_bucket(row, "opening_range_breakout")),
        _FeatureSpec("failed_breakout", lambda row: _binary_bucket(row, "failed_breakout")),
        _FeatureSpec("true_leader", lambda row: _binary_bucket(row, "true_leader")),
        _FeatureSpec("liquidity_ok", lambda row: _binary_bucket(row, "liquidity_ok")),
        _FeatureSpec("catalyst_risk_flag", lambda row: _binary_bucket(row, "catalyst_risk_flag")),
        _FeatureSpec("dilution_risk_flag", lambda row: _binary_bucket(row, "dilution_risk_flag")),
    )


def _entry_time_bucket(row: dict[str, str]) -> str:
    entry_at = str(row.get("entry_at") or "")
    try:
        parsed = datetime.fromisoformat(entry_at)
    except ValueError:
        return "missing"
    hour = parsed.hour
    minute = parsed.minute
    total_minutes = hour * 60 + minute
    if total_minutes < 9 * 60:
        return "before_09:00"
    if total_minutes < 9 * 60 + 30:
        return "09:00-09:29"
    if total_minutes < 10 * 60:
        return "09:30-09:59"
    return "10:00+"


def _numeric_bucket(row: dict[str, str], key: str, cuts: tuple[float, ...]) -> str:
    raw = row.get(key)
    if raw in (None, ""):
        return "missing"
    value = _number(raw)
    previous: float | None = None
    for cut in cuts:
        if value < cut:
            if previous is None:
                return f"<{_format_cut(cut)}"
            return f"{_format_cut(previous)}-{_format_cut(cut)}"
        previous = cut
    return f">={_format_cut(cuts[-1])}" if cuts else "all"


def _binary_bucket(row: dict[str, str], key: str) -> str:
    value = row.get(key)
    if value in (None, ""):
        return "missing"
    return "true" if _truthy(value) else "false"


def _signal_status(row: dict[str, object], positive_month_rate: float) -> str:
    trade_count = _number(row.get("trade_count"))
    total_pnl = _number(row.get("total_net_pnl"))
    if trade_count < 30:
        return "insufficient_sample"
    if total_pnl <= 0:
        return "no_positive_signal"
    if (
        _number(row.get("top_1_symbol_removed_net_pnl")) <= 0
        or _number(row.get("top_1_market_date_removed_net_pnl")) <= 0
    ):
        return "fragile_concentrated"
    if positive_month_rate < 0.67 and _number(row.get("month_count")) >= 2:
        return "regime_dependent"
    return "candidate_needs_oos"


def _top_rows(rows: list[dict[str, object]], key: str, *, limit: int) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (-_number(row.get(key)), str(row)))[:limit]


def _bottom_rows(rows: list[dict[str, object]], key: str, *, limit: int) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (_number(row.get(key)), str(row)))[:limit]


def _avg(values: list[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _round(value: float) -> float:
    return round(float(value), 6)


def _format_cut(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:g}"
