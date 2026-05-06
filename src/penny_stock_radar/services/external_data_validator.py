from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ..db import get_connection
from .kis_quote_consolidation_check import (
    ConsolidationEvidence,
    ConsolidationVerdict,
    KIS_L1_SOURCE,
    LATEST_KIS_CONSOLIDATION_PATH,
    load_latest_kis_consolidation_verdict,
)

EASTERN = ZoneInfo("America/New_York")
ACTIVE_START = time(9, 30)
ACTIVE_END = time(16, 0)
DEFAULT_MIN_SAMPLE_SIZE = 30
DEFAULT_POLICY_FILE = Path("config/cost_source_policy.json")
SOURCE_VALIDATION_DIR = Path("automation/state/source_validation")

LEGACY_COST_ELIGIBLE_EXACT_SOURCES = frozenset(
    {
        "full_nbbo",
        "full_sip",
        "nbbo_sip",
        "cta_utp_sip",
    }
)
LEGACY_COST_ELIGIBLE_SOURCE_PREFIXES = (
    "full_nbbo_",
    "full_sip_",
    "nbbo_sip_",
    "vendor_nbbo_",
    "vendor_sip_",
)
LEGACY_DIAGNOSTIC_ONLY_EXACT_SOURCES = frozenset(
    {
        "alpaca_iex_historical_quotes",
        "alpaca_iex_diagnostic",
    }
)
LEGACY_DIAGNOSTIC_ONLY_SOURCE_PREFIXES = ("alpaca_iex_",)


@dataclass(frozen=True, slots=True)
class SourceLicenseInfo:
    license_type: str
    redistribution_allowed: bool
    citation_required: bool
    documented_at: str
    cost_evidence_allowed: bool = False


@dataclass(frozen=True, slots=True)
class SourceValidationResult:
    source_name: str
    nbbo_consolidation_verdict: ConsolidationVerdict | None
    coverage_market_hours_pct: float
    spread_sanity_check: dict[str, Any]
    license: SourceLicenseInfo
    decision: Literal["cost_eligible", "diagnostic_only", "rejected"]
    decision_reason: str
    validated_at: datetime


@dataclass(frozen=True, slots=True)
class EffectiveCostSourcePolicy:
    cost_eligible_sources: tuple[dict[str, Any], ...]
    diagnostic_only_sources: tuple[dict[str, Any], ...]
    rejected_sources: tuple[dict[str, Any], ...]
    load_status: Literal["loaded", "created_default", "fallback_default_invalid", "fallback_default_unreadable"]
    policy_file: Path


class ExternalDataValidator:
    def validate(
        self,
        source_name: str,
        sample_db_path: Path,
        license_info: SourceLicenseInfo,
    ) -> SourceValidationResult:
        normalized_source = _normalize_source_name(source_name)
        verdict, rows = _evaluate_source_consolidation(normalized_source, sample_db_path)
        coverage_pct = _coverage_market_hours_pct(rows)
        spread_sanity = _spread_sanity_check(rows)
        decision, reason = _validation_decision(
            verdict=verdict,
            coverage_market_hours_pct=coverage_pct,
            spread_sanity_check=spread_sanity,
            license_info=license_info,
        )
        return SourceValidationResult(
            source_name=normalized_source,
            nbbo_consolidation_verdict=verdict,
            coverage_market_hours_pct=coverage_pct,
            spread_sanity_check=spread_sanity,
            license=license_info,
            decision=decision,
            decision_reason=reason,
            validated_at=datetime.now(timezone.utc),
        )

    def register_to_policy(
        self,
        result: SourceValidationResult,
        policy_file: Path = DEFAULT_POLICY_FILE,
    ) -> None:
        policy = load_cost_source_policy(policy_file=policy_file)
        payload = {
            "cost_eligible_sources": [dict(entry) for entry in policy.cost_eligible_sources],
            "diagnostic_only_sources": [dict(entry) for entry in policy.diagnostic_only_sources],
            "rejected_sources": [dict(entry) for entry in policy.rejected_sources],
        }
        for bucket in payload.values():
            bucket[:] = [
                entry
                for entry in bucket
                if _normalize_source_name(entry.get("name")) != result.source_name
            ]

        validation_path = _latest_validation_path(
            result.source_name,
            policy_root=_policy_root(policy_file),
        )
        _write_json(validation_path, source_validation_result_to_dict(result))
        entry = {
            "name": result.source_name,
            "validated_at": _isoformat_utc(result.validated_at),
            "verdict_path": str(validation_path),
            "license": asdict(result.license),
            "decision_reason": result.decision_reason,
        }
        if result.decision == "cost_eligible":
            payload["cost_eligible_sources"].append(entry)
        elif result.decision == "diagnostic_only":
            payload["diagnostic_only_sources"].append(entry)
        else:
            payload["rejected_sources"].append(entry)
        _write_json(policy_file, _sorted_policy(payload))


def load_cost_source_policy(
    policy_file: Path = DEFAULT_POLICY_FILE,
) -> EffectiveCostSourcePolicy:
    if not policy_file.exists():
        payload = default_cost_source_policy_payload()
        _write_json(policy_file, payload)
        return _effective_policy(payload, policy_file=policy_file, load_status="created_default")
    try:
        payload = json.loads(policy_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _effective_policy(
            default_cost_source_policy_payload(),
            policy_file=policy_file,
            load_status="fallback_default_unreadable",
        )
    if not _valid_policy_payload(payload):
        return _effective_policy(
            default_cost_source_policy_payload(),
            policy_file=policy_file,
            load_status="fallback_default_invalid",
        )
    return _effective_policy(payload, policy_file=policy_file, load_status="loaded")


def default_cost_source_policy_payload() -> dict[str, Any]:
    now = "1970-01-01T00:00:00Z"
    default_license = SourceLicenseInfo(
        license_type="internal_default_policy",
        redistribution_allowed=True,
        citation_required=False,
        documented_at="docs/BACKTEST_ROADMAP_KO.md",
    )
    cost_entries = [
        _policy_entry(name=source, validated_at=now, license_info=default_license)
        for source in sorted(LEGACY_COST_ELIGIBLE_EXACT_SOURCES)
    ]
    cost_entries.extend(
        _policy_entry(name=f"{prefix}*", validated_at=now, license_info=default_license)
        for prefix in LEGACY_COST_ELIGIBLE_SOURCE_PREFIXES
    )
    cost_entries.append(
        _policy_entry(
            name=KIS_L1_SOURCE,
            validated_at=now,
            license_info=default_license,
            verdict_path=str(LATEST_KIS_CONSOLIDATION_PATH),
            decision_reason=(
                "Legacy conditional default: cost eligible only when latest KIS "
                "consolidation verdict is nbbo_consolidated."
            ),
        )
    )
    diagnostic_license = SourceLicenseInfo(
        license_type="personal_use",
        redistribution_allowed=False,
        citation_required=False,
        documented_at="docs/BACKTEST_ROADMAP_KO.md",
    )
    diagnostic_entries = [
        _policy_entry(name=source, validated_at=now, license_info=diagnostic_license)
        for source in sorted(LEGACY_DIAGNOSTIC_ONLY_EXACT_SOURCES)
    ]
    diagnostic_entries.extend(
        _policy_entry(name=f"{prefix}*", validated_at=now, license_info=diagnostic_license)
        for prefix in LEGACY_DIAGNOSTIC_ONLY_SOURCE_PREFIXES
    )
    return _sorted_policy(
        {
            "cost_eligible_sources": cost_entries,
            "diagnostic_only_sources": diagnostic_entries,
            "rejected_sources": [],
        }
    )


def is_cost_eligible_source(source: str | None, *, policy_file: Path = DEFAULT_POLICY_FILE) -> bool:
    normalized = _normalize_source_name(source)
    if not normalized:
        return False
    decision = policy_decision_for_source(normalized, policy_file=policy_file)
    return decision == "cost_eligible"


def is_diagnostic_only_source(source: str | None, *, policy_file: Path = DEFAULT_POLICY_FILE) -> bool:
    normalized = _normalize_source_name(source)
    if not normalized:
        return False
    decision = policy_decision_for_source(normalized, policy_file=policy_file)
    return decision == "diagnostic_only"


def policy_decision_for_source(
    source: str | None,
    *,
    policy_file: Path = DEFAULT_POLICY_FILE,
) -> Literal["cost_eligible", "diagnostic_only", "rejected", "unknown"]:
    normalized = _normalize_source_name(source)
    if not normalized:
        return "unknown"
    policy = load_cost_source_policy(policy_file=policy_file)
    if _matches_any(normalized, policy.rejected_sources):
        return "rejected"
    if _matches_any(normalized, policy.diagnostic_only_sources):
        return "diagnostic_only"
    if _matches_any(normalized, policy.cost_eligible_sources):
        if normalized == KIS_L1_SOURCE:
            verdict = load_latest_kis_consolidation_verdict()
            if verdict is None:
                return "unknown"
            if verdict.get("classification") == "nbbo_consolidated":
                return "cost_eligible"
            if verdict.get("classification") == "single_venue_proxy":
                return "diagnostic_only"
            return "unknown"
        return "cost_eligible"
    return "unknown"


def cost_policy_sql_matchers(
    policy_file: Path = DEFAULT_POLICY_FILE,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    policy = load_cost_source_policy(policy_file=policy_file)
    exact: list[str] = []
    prefixes: list[str] = []
    for entry in policy.cost_eligible_sources:
        name = _normalize_source_name(entry.get("name"))
        if not name:
            continue
        if name == KIS_L1_SOURCE and policy_decision_for_source(name, policy_file=policy_file) != "cost_eligible":
            continue
        if name.endswith("*"):
            prefixes.append(name[:-1])
        else:
            exact.append(name)
    return tuple(sorted(set(exact))), tuple(sorted(set(prefixes)))


def cost_source_policy_summary(policy_file: Path = DEFAULT_POLICY_FILE) -> dict[str, Any]:
    policy = load_cost_source_policy(policy_file=policy_file)
    cost_names = sorted(_normalize_source_name(entry.get("name")) for entry in policy.cost_eligible_sources)
    diagnostic_names = sorted(_normalize_source_name(entry.get("name")) for entry in policy.diagnostic_only_sources)
    rejected_names = sorted(_normalize_source_name(entry.get("name")) for entry in policy.rejected_sources)
    exact_sources = sorted(name for name in cost_names if name and not name.endswith("*") and name != KIS_L1_SOURCE)
    prefixes = sorted(name[:-1] for name in cost_names if name.endswith("*"))
    return {
        "policy_file": str(policy.policy_file),
        "policy_load_status": policy.load_status,
        "cost_eligible_exact_sources": exact_sources,
        "cost_eligible_source_prefixes": prefixes,
        "conditional_cost_eligible_sources": [KIS_L1_SOURCE],
        "kis_l1_snapshot_policy": _kis_l1_snapshot_policy_fields(policy_file=policy_file),
        "diagnostic_only_exact_sources": sorted(
            name for name in diagnostic_names if name and not name.endswith("*")
        ),
        "diagnostic_only_source_prefixes": sorted(name[:-1] for name in diagnostic_names if name.endswith("*")),
        "rejected_sources": rejected_names,
        "diagnostic_only_warning": (
            "Alpaca IEX is a single-exchange diagnostic feed. KIS L1 is accepted "
            "only when policy allows it and latest KIS consolidation verdict is "
            "nbbo_consolidated. Excluded sources cannot clear cost distributions, "
            "matched random-entry cost overlap, or edge approval evidence."
        ),
    }


def source_validation_result_to_dict(result: SourceValidationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["validated_at"] = _isoformat_utc(result.validated_at)
    verdict = result.nbbo_consolidation_verdict
    if verdict is not None:
        verdict_payload = asdict(verdict)
        sample_window = verdict.evidence.sample_window
        verdict_payload["evidence"]["sample_window"] = [
            sample_window[0].isoformat(),
            sample_window[1].isoformat(),
        ]
        payload["nbbo_consolidation_verdict"] = verdict_payload
    return payload


def _evaluate_source_consolidation(
    source_name: str,
    sample_db_path: Path,
) -> tuple[ConsolidationVerdict | None, list[dict[str, Any]]]:
    if not sample_db_path.exists():
        return None, []
    try:
        with get_connection(sample_db_path) as connection:
            columns = _table_columns(connection, "historical_l1_quotes")
            if not columns:
                return None, []
            rows = _load_source_rows(connection, source_name=source_name, columns=columns)
    except sqlite3.Error:
        return None, []
    active_rows = [row for row in rows if _is_active_quote(str(row["quote_at"]))]
    evidence = _build_evidence(active_rows or rows)
    if evidence.sample_size < DEFAULT_MIN_SAMPLE_SIZE:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=(
                    f"sample_size={evidence.sample_size} below threshold="
                    f"{DEFAULT_MIN_SAMPLE_SIZE} or active-hours data is insufficient"
                ),
            ),
            rows,
        )
    if not active_rows:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="insufficient_evidence",
                reason="no regular-session active-hours quote rows in sample",
            ),
            rows,
        )
    if evidence.bid_exchange_distinct_count == 0 and evidence.ask_exchange_distinct_count == 0:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="single_venue_proxy",
                reason="bid_exchange/ask_exchange information is absent from sample rows",
            ),
            rows,
        )
    if evidence.bid_exchange_distinct_count == 1 and evidence.ask_exchange_distinct_count <= 1:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="single_venue_proxy",
                reason="bid_exchange/ask_exchange distinct count indicates a single venue proxy",
            ),
            rows,
        )
    if evidence.bid_exchange_distinct_count + evidence.ask_exchange_distinct_count < 3:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=(
                    "distinct exchange evidence is too narrow: "
                    f"bid+ask distinct={evidence.bid_exchange_distinct_count + evidence.ask_exchange_distinct_count}"
                ),
            ),
            rows,
        )
    if evidence.quote_update_frequency_hz < 1.0:
        return (
            ConsolidationVerdict(
                source_name=source_name,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=f"active-hours quote update frequency below 1 Hz: {evidence.quote_update_frequency_hz:.3f}",
            ),
            rows,
        )
    return (
        ConsolidationVerdict(
            source_name=source_name,
            evidence=evidence,
            classification="nbbo_consolidated",
            reason="exchange diversity and active-hours update frequency satisfy NBBO consolidation evidence thresholds",
        ),
        rows,
    )


def _load_source_rows(
    connection: sqlite3.Connection,
    *,
    source_name: str,
    columns: set[str],
) -> list[dict[str, Any]]:
    bid_exchange_expr = "bid_exchange" if "bid_exchange" in columns else "NULL AS bid_exchange"
    ask_exchange_expr = "ask_exchange" if "ask_exchange" in columns else "NULL AS ask_exchange"
    rows = connection.execute(
        f"""
        SELECT
            symbol,
            market_date,
            quote_at,
            bid_price,
            ask_price,
            {bid_exchange_expr},
            {ask_exchange_expr}
        FROM historical_l1_quotes
        WHERE source = ?
          AND bid_price IS NOT NULL
          AND ask_price IS NOT NULL
          AND bid_price > 0
        ORDER BY market_date ASC, symbol ASC, quote_at ASC
        """,
        (source_name,),
    ).fetchall()
    return [dict(row) for row in rows]


def _coverage_market_hours_pct(rows: list[dict[str, Any]]) -> float:
    active_minutes_by_group: dict[tuple[str, str], set[datetime]] = {}
    for row in rows:
        timestamp = _parse_timestamp(str(row.get("quote_at") or ""))
        market_date = str(row.get("market_date") or "")
        symbol = str(row.get("symbol") or "")
        if timestamp is None or not market_date or not symbol:
            continue
        eastern = timestamp.astimezone(EASTERN)
        if not (ACTIVE_START <= eastern.time() <= ACTIVE_END):
            continue
        minute = eastern.replace(second=0, microsecond=0)
        active_minutes_by_group.setdefault((market_date, symbol), set()).add(minute)
    if not active_minutes_by_group:
        return 0.0
    covered = sum(len(minutes) for minutes in active_minutes_by_group.values())
    expected = len(active_minutes_by_group) * 390
    return round(min(100.0, covered / expected * 100.0), 6)


def _spread_sanity_check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_spread_pct: list[float] = []
    crossed_or_negative = 0
    invalid = 0
    for row in rows:
        bid = _none_or_float(row.get("bid_price"))
        ask = _none_or_float(row.get("ask_price"))
        if bid is None or ask is None or bid <= 0:
            invalid += 1
            continue
        if ask < bid:
            crossed_or_negative += 1
            continue
        midpoint = (bid + ask) / 2.0
        if midpoint <= 0:
            invalid += 1
            continue
        valid_spread_pct.append((ask - bid) / midpoint * 100.0)
    return {
        "sample_size": len(rows),
        "valid_spread_count": len(valid_spread_pct),
        "invalid_spread_count": invalid,
        "crossed_or_negative_spread_count": crossed_or_negative,
        "p50_spread_pct": _percentile(valid_spread_pct, 0.50),
        "p90_spread_pct": _percentile(valid_spread_pct, 0.90),
        "p99_spread_pct": _percentile(valid_spread_pct, 0.99),
        "max_spread_pct": max(valid_spread_pct) if valid_spread_pct else None,
    }


def _validation_decision(
    *,
    verdict: ConsolidationVerdict | None,
    coverage_market_hours_pct: float,
    spread_sanity_check: dict[str, Any],
    license_info: SourceLicenseInfo,
) -> tuple[Literal["cost_eligible", "diagnostic_only", "rejected"], str]:
    if verdict is None:
        return "rejected", "sample database is missing or has no readable historical_l1_quotes rows"
    sample_size = int(spread_sanity_check.get("sample_size") or 0)
    if sample_size <= 0:
        return "rejected", "source has no usable bid/ask sample rows"
    if not license_info.redistribution_allowed and not license_info.cost_evidence_allowed:
        return "diagnostic_only", "license prohibits redistribution; cost-eligible registration is not allowed"
    if int(spread_sanity_check.get("crossed_or_negative_spread_count") or 0) > 0:
        return "diagnostic_only", "sample contains crossed or negative bid/ask spreads"
    if verdict.classification == "nbbo_consolidated" and coverage_market_hours_pct > 0:
        return "cost_eligible", "NBBO consolidation verdict and license policy allow cost eligibility"
    if verdict.classification == "single_venue_proxy":
        return "diagnostic_only", verdict.reason
    return "diagnostic_only", verdict.reason


def _build_evidence(rows: list[dict[str, Any]]) -> ConsolidationEvidence:
    timestamps = [_parse_timestamp(str(row.get("quote_at") or "")) for row in rows]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    if timestamps:
        sample_window = (min(timestamps), max(timestamps))
    else:
        now = datetime.now(EASTERN)
        sample_window = (now, now)
    spreads = [
        float(row["ask_price"]) - float(row["bid_price"])
        for row in rows
        if row.get("bid_price") is not None and row.get("ask_price") is not None
    ]
    bid_exchanges = {_normalize_exchange(row.get("bid_exchange")) for row in rows}
    ask_exchanges = {_normalize_exchange(row.get("ask_exchange")) for row in rows}
    bid_exchanges.discard(None)
    ask_exchanges.discard(None)
    comparable_exchange_rows = [
        row
        for row in rows
        if _normalize_exchange(row.get("bid_exchange")) is not None
        and _normalize_exchange(row.get("ask_exchange")) is not None
    ]
    differ_count = sum(
        1
        for row in comparable_exchange_rows
        if _normalize_exchange(row.get("bid_exchange")) != _normalize_exchange(row.get("ask_exchange"))
    )
    duration_seconds = max(0.0, (sample_window[1] - sample_window[0]).total_seconds())
    return ConsolidationEvidence(
        bid_exchange_distinct_count=len(bid_exchanges),
        ask_exchange_distinct_count=len(ask_exchanges),
        bid_ask_exchange_differ_rate=(
            differ_count / len(comparable_exchange_rows) if comparable_exchange_rows else 0.0
        ),
        quote_update_frequency_hz=(len(rows) / duration_seconds if duration_seconds > 0 else 0.0),
        spread_distribution_p50=_percentile(spreads, 0.50),
        spread_distribution_p90=_percentile(spreads, 0.90),
        spread_distribution_p99=_percentile(spreads, 0.99),
        sample_size=len(rows),
        sample_window=sample_window,
    )


def _kis_l1_snapshot_policy_fields(*, policy_file: Path) -> dict[str, Any]:
    verdict = load_latest_kis_consolidation_verdict()
    decision = policy_decision_for_source(KIS_L1_SOURCE, policy_file=policy_file)
    if verdict is None:
        return {
            "verdict_path": str(LATEST_KIS_CONSOLIDATION_PATH),
            "classification": None,
            "cost_eligible": False,
            "reason": "latest KIS consolidation verdict missing or unreadable",
        }
    classification = str(verdict.get("classification") or "")
    return {
        "verdict_path": str(LATEST_KIS_CONSOLIDATION_PATH),
        "classification": classification,
        "cost_eligible": decision == "cost_eligible",
        "reason": verdict.get("reason"),
    }


def _effective_policy(
    payload: dict[str, Any],
    *,
    policy_file: Path,
    load_status: EffectiveCostSourcePolicy.__annotations__["load_status"],
) -> EffectiveCostSourcePolicy:
    return EffectiveCostSourcePolicy(
        cost_eligible_sources=tuple(dict(entry) for entry in payload["cost_eligible_sources"]),
        diagnostic_only_sources=tuple(dict(entry) for entry in payload["diagnostic_only_sources"]),
        rejected_sources=tuple(dict(entry) for entry in payload["rejected_sources"]),
        load_status=load_status,
        policy_file=policy_file,
    )


def _valid_policy_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("cost_eligible_sources", "diagnostic_only_sources", "rejected_sources"):
        if not isinstance(payload.get(key), list):
            return False
        for entry in payload[key]:
            if not isinstance(entry, dict):
                return False
            if not _normalize_source_name(entry.get("name")):
                return False
            if not isinstance(entry.get("validated_at"), str) or not entry["validated_at"].strip():
                return False
            if not isinstance(entry.get("verdict_path"), str) or not entry["verdict_path"].strip():
                return False
            if not _valid_license_payload(entry.get("license")):
                return False
    return True


def _valid_license_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    required_fields = (
        "license_type",
        "redistribution_allowed",
        "citation_required",
        "documented_at",
    )
    for field in required_fields:
        if field not in payload:
            return False
    return (
        isinstance(payload["license_type"], str)
        and isinstance(payload["redistribution_allowed"], bool)
        and isinstance(payload["citation_required"], bool)
        and isinstance(payload["documented_at"], str)
        and (
            "cost_evidence_allowed" not in payload
            or isinstance(payload["cost_evidence_allowed"], bool)
        )
        and bool(payload["license_type"].strip())
        and bool(payload["documented_at"].strip())
    )


def _matches_any(source: str, entries: tuple[dict[str, Any], ...]) -> bool:
    for entry in entries:
        name = _normalize_source_name(entry.get("name"))
        if not name:
            continue
        if name.endswith("*") and source.startswith(name[:-1]):
            return True
        if source == name:
            return True
    return False


def _policy_entry(
    *,
    name: str,
    validated_at: str,
    license_info: SourceLicenseInfo,
    verdict_path: str | None = None,
    decision_reason: str = "legacy default cost source policy",
) -> dict[str, Any]:
    return {
        "name": name,
        "validated_at": validated_at,
        "verdict_path": verdict_path or "config/cost_source_policy.json",
        "license": asdict(license_info),
        "decision_reason": decision_reason,
    }


def _sorted_policy(payload: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "cost_eligible_sources": sorted(
            payload["cost_eligible_sources"],
            key=lambda entry: _normalize_source_name(entry.get("name")),
        ),
        "diagnostic_only_sources": sorted(
            payload["diagnostic_only_sources"],
            key=lambda entry: _normalize_source_name(entry.get("name")),
        ),
        "rejected_sources": sorted(
            payload["rejected_sources"],
            key=lambda entry: _normalize_source_name(entry.get("name")),
        ),
    }


def _latest_validation_path(source_name: str, *, policy_root: Path) -> Path:
    return policy_root / SOURCE_VALIDATION_DIR / f"latest_{_safe_source_name(source_name)}_validation.json"


def _policy_root(policy_file: Path) -> Path:
    return policy_file.parent.parent if policy_file.parent.name == "config" else policy_file.parent


def _safe_source_name(source_name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _normalize_source_name(source_name)).strip("_") or "source"


def _normalize_source_name(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_exchange(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["name"]) for row in rows}


def _is_active_quote(value: str) -> bool:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return False
    eastern = timestamp.astimezone(EASTERN)
    return ACTIVE_START <= eastern.time() <= ACTIVE_END


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _none_or_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _isoformat_utc(value: datetime) -> str:
    timestamp = value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
