from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ..db import get_connection

EASTERN = ZoneInfo("America/New_York")
KIS_L1_SOURCE = "kis_l1_snapshot"
DEFAULT_MIN_SAMPLE_SIZE = 30
ACTIVE_START = time(9, 30)
ACTIVE_END = time(16, 0)
LATEST_KIS_CONSOLIDATION_PATH = Path(
    "automation/state/source_validation/latest_kis_consolidation.json"
)


@dataclass(frozen=True, slots=True)
class ConsolidationEvidence:
    bid_exchange_distinct_count: int
    ask_exchange_distinct_count: int
    bid_ask_exchange_differ_rate: float
    quote_update_frequency_hz: float
    spread_distribution_p50: float
    spread_distribution_p90: float
    spread_distribution_p99: float
    sample_size: int
    sample_window: tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class ConsolidationVerdict:
    source_name: str
    evidence: ConsolidationEvidence
    classification: Literal[
        "nbbo_consolidated",
        "single_venue_proxy",
        "insufficient_evidence",
    ]
    reason: str


def evaluate_kis_quote_consolidation(
    db_path: Path,
    sample_window_days: int = 5,
    reference_source: Literal["ibkr_nbbo", "polygon_nbbo_free", None] = None,
) -> ConsolidationVerdict:
    if sample_window_days < 1:
        raise ValueError("sample_window_days must be at least 1.")
    if not db_path.exists():
        return _empty_verdict("database_missing")

    with get_connection(db_path) as connection:
        columns = _table_columns(connection, "historical_l1_quotes")
        if not columns:
            return _empty_verdict("historical_l1_quotes_missing")
        latest_market_date = _latest_market_date(connection)
        if latest_market_date is None:
            return _empty_verdict("kis_l1_snapshot_missing")
        start_date = date.fromisoformat(latest_market_date)
        start_date = start_date.replace(day=start_date.day)
        window_start = start_date.toordinal() - sample_window_days + 1
        market_dates = {
            date.fromordinal(day).isoformat()
            for day in range(window_start, start_date.toordinal() + 1)
        }
        rows = _load_kis_rows(connection, columns=columns, market_dates=market_dates)
        active_rows = [row for row in rows if _is_active_quote(row["quote_at"])]
        evidence = _build_evidence(active_rows or rows)

        if evidence.sample_size < DEFAULT_MIN_SAMPLE_SIZE:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=(
                    f"sample_size={evidence.sample_size} below threshold="
                    f"{DEFAULT_MIN_SAMPLE_SIZE} or active-hours data is insufficient"
                ),
            )
        if not active_rows:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="insufficient_evidence",
                reason="no regular-session active-hours KIS quote rows in sample window",
            )

        bid_distinct = evidence.bid_exchange_distinct_count
        ask_distinct = evidence.ask_exchange_distinct_count
        distinct_total = bid_distinct + ask_distinct
        if bid_distinct == 0 and ask_distinct == 0:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="single_venue_proxy",
                reason="bid_exchange/ask_exchange information is absent from KIS L1 rows",
            )
        if bid_distinct == 1 and ask_distinct <= 1:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="single_venue_proxy",
                reason="bid_exchange/ask_exchange distinct count indicates a single venue proxy",
            )
        if distinct_total < 3:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=f"distinct exchange evidence is too narrow: bid+ask distinct={distinct_total}",
            )
        if evidence.quote_update_frequency_hz < 1.0:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="insufficient_evidence",
                reason=(
                    "active-hours quote update frequency below 1 Hz: "
                    f"{evidence.quote_update_frequency_hz:.3f}"
                ),
            )

        reference_reason = _reference_downgrade_reason(
            connection,
            columns=columns,
            market_dates=market_dates,
            reference_source=reference_source,
            kis_p50_spread=evidence.spread_distribution_p50,
        )
        if reference_reason is not None:
            return ConsolidationVerdict(
                source_name=KIS_L1_SOURCE,
                evidence=evidence,
                classification="single_venue_proxy",
                reason=reference_reason,
            )

    return ConsolidationVerdict(
        source_name=KIS_L1_SOURCE,
        evidence=evidence,
        classification="nbbo_consolidated",
        reason=(
            "exchange diversity and active-hours update frequency satisfy NBBO "
            "consolidation evidence thresholds"
        ),
    )


def write_consolidation_verdict(
    verdict: ConsolidationVerdict,
    output_path: Path,
    *,
    latest_path: Path = LATEST_KIS_CONSOLIDATION_PATH,
) -> tuple[Path, Path]:
    payload = verdict_to_dict(verdict)
    _write_json(output_path, payload)
    _write_json(latest_path, payload)
    return output_path, latest_path


def verdict_to_dict(verdict: ConsolidationVerdict) -> dict[str, Any]:
    payload = asdict(verdict)
    window = verdict.evidence.sample_window
    payload["evidence"]["sample_window"] = [window[0].isoformat(), window[1].isoformat()]
    return payload


def load_latest_kis_consolidation_verdict(
    path: Path = LATEST_KIS_CONSOLIDATION_PATH,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_market_date(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT MAX(market_date) AS market_date
        FROM historical_l1_quotes
        WHERE source = ?
        """,
        (KIS_L1_SOURCE,),
    ).fetchone()
    return str(row["market_date"]) if row and row["market_date"] else None


def _load_kis_rows(
    connection: sqlite3.Connection,
    *,
    columns: set[str],
    market_dates: set[str],
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in sorted(market_dates))
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
          AND market_date IN ({placeholders})
          AND bid_price IS NOT NULL
          AND ask_price IS NOT NULL
          AND bid_price > 0
          AND ask_price >= bid_price
        ORDER BY quote_at ASC, symbol ASC
        """,
        (KIS_L1_SOURCE, *sorted(market_dates)),
    ).fetchall()
    return [dict(row) for row in rows]


def _build_evidence(rows: list[dict[str, Any]]) -> ConsolidationEvidence:
    timestamps = [_parse_timestamp(str(row["quote_at"])) for row in rows]
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


def _reference_downgrade_reason(
    connection: sqlite3.Connection,
    *,
    columns: set[str],
    market_dates: set[str],
    reference_source: Literal["ibkr_nbbo", "polygon_nbbo_free", None],
    kis_p50_spread: float,
) -> str | None:
    if reference_source is None:
        return None
    if not market_dates:
        return None
    placeholders = ", ".join("?" for _ in sorted(market_dates))
    rows = connection.execute(
        f"""
        SELECT
            kis.symbol,
            kis.quote_at,
            ABS(((kis.bid_price + kis.ask_price) / 2.0) - ((ref.bid_price + ref.ask_price) / 2.0))
                AS mid_diff
        FROM historical_l1_quotes kis
        JOIN historical_l1_quotes ref
          ON ref.symbol = kis.symbol
         AND ref.quote_at = kis.quote_at
        WHERE kis.source = ?
          AND ref.source = ?
          AND kis.market_date IN ({placeholders})
          AND kis.bid_price IS NOT NULL
          AND kis.ask_price IS NOT NULL
          AND ref.bid_price IS NOT NULL
          AND ref.ask_price IS NOT NULL
          AND kis.bid_price > 0
          AND kis.ask_price >= kis.bid_price
          AND ref.bid_price > 0
          AND ref.ask_price >= ref.bid_price
        """,
        (KIS_L1_SOURCE, reference_source, *sorted(market_dates)),
    ).fetchall()
    diffs = [float(row["mid_diff"]) for row in rows if row["mid_diff"] is not None]
    if len(diffs) < DEFAULT_MIN_SAMPLE_SIZE:
        return None
    p90_mid_diff = _percentile(diffs, 0.90)
    if kis_p50_spread > 0 and p90_mid_diff > kis_p50_spread * 0.5:
        return (
            f"reference_source={reference_source} p90 mid-quote difference "
            f"{p90_mid_diff:.6f} exceeds 50% of KIS p50 spread {kis_p50_spread:.6f}"
        )
    return None


def _empty_verdict(reason: str) -> ConsolidationVerdict:
    now = datetime.now(EASTERN)
    evidence = ConsolidationEvidence(
        bid_exchange_distinct_count=0,
        ask_exchange_distinct_count=0,
        bid_ask_exchange_differ_rate=0.0,
        quote_update_frequency_hz=0.0,
        spread_distribution_p50=0.0,
        spread_distribution_p90=0.0,
        spread_distribution_p99=0.0,
        sample_size=0,
        sample_window=(now, now),
    )
    return ConsolidationVerdict(
        source_name=KIS_L1_SOURCE,
        evidence=evidence,
        classification="insufficient_evidence",
        reason=reason,
    )


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


def _normalize_exchange(value: object) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
