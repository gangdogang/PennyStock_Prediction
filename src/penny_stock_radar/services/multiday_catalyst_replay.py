from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from ..db import get_connection
from .external_data_validator import (
    cost_source_policy_summary,
    is_cost_eligible_source,
    is_diagnostic_only_source,
)
from .engine_rules.exit import planned_risk_pct
from .multiday_engine import MULTIDAY_ENGINE_SPEC
from .premkt_historical_replay import DEFAULT_REPLAY_DB, validate_replay_paths
from .replay_grade_stamper import csv_decision_grade_comment, stamp_replay_run
from .universe_tradability_audit import classify_listing_exchange

EASTERN = ZoneInfo("America/New_York")
DEFAULT_EXPORT_ROOT = Path("data/backtest_lab/replays/multiday_catalyst")
EOD_REPRESENTATIVE_COST_SOURCES = frozenset({"yfinance", "yfinance_eod", "yahoo_finance"})


@dataclass(frozen=True, slots=True)
class CatalystEntryRule:
    entry_time: Literal["d_close", "d_plus_1_open", "d_plus_1_0935"]
    require_sec_filing_within_days: int = 1
    min_dollar_volume_d: float = 1_000_000


@dataclass(frozen=True, slots=True, kw_only=True)
class CatalystExitRule:
    max_holding_days: int = 5
    exit_on_volume_exhaustion: bool = True
    exit_on_follow_on_filing: bool = True
    structure_stop: Literal["d_minus_1_low", "n_day_low", "atr_multiple"]
    structure_stop_param: float = 1.0


@dataclass(frozen=True, slots=True)
class MultidayCatalystReplayResult:
    export_dir: Path
    manifest_path: Path
    progress_path: Path
    summary_path: Path
    trades_path: Path
    entries_path: Path


@dataclass(frozen=True, slots=True)
class _DailyBar:
    symbol: str
    market_date: date
    open_at: datetime
    open_price: float
    at_0935: datetime
    price_0935: float
    close_at: datetime
    close_price: float
    high_price: float
    low_price: float
    volume: float
    dollar_volume: float
    source: str
    spread_pct: float | None


@dataclass(frozen=True, slots=True)
class _Entry:
    symbol: str
    catalyst_date: date
    entry_date: date
    entry_at: datetime
    entry_price: float
    catalyst_dollar_volume: float
    stop_price: float
    stop_rule: str
    filing_count: int
    filing_forms: tuple[str, ...]
    source: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Trade:
    symbol: str
    catalyst_date: date
    entry_date: date
    entry_at: datetime
    entry_price: float
    exit_date: date
    exit_at: datetime
    exit_price: float
    exit_reason: str
    holding_days: int
    gross_return_pct: float
    stop_price: float
    filing_count: int
    source: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def run_multiday_catalyst_replay(
    db_path: Path,
    market_date_start: date,
    market_date_end: date,
    entry_rule: CatalystEntryRule,
    exit_rule: CatalystExitRule,
    universe_filter: dict,
) -> Path:
    """Run a local EOD multi-day catalyst replay and return the artifact directory."""
    result = MultidayCatalystReplayRunner().run(
        db_path=db_path,
        market_date_start=market_date_start,
        market_date_end=market_date_end,
        entry_rule=entry_rule,
        exit_rule=exit_rule,
        universe_filter=universe_filter,
    )
    return result.export_dir


class MultidayCatalystReplayRunner:
    def run(
        self,
        *,
        db_path: Path = DEFAULT_REPLAY_DB,
        market_date_start: date,
        market_date_end: date,
        entry_rule: CatalystEntryRule,
        exit_rule: CatalystExitRule,
        universe_filter: dict[str, Any] | None = None,
    ) -> MultidayCatalystReplayResult:
        if market_date_end < market_date_start:
            raise ValueError("market_date_end must be greater than or equal to market_date_start.")
        if entry_rule.require_sec_filing_within_days < 1:
            raise ValueError("require_sec_filing_within_days must be at least 1.")
        if exit_rule.max_holding_days < 1:
            raise ValueError("max_holding_days must be at least 1.")

        filters = dict(universe_filter or {})
        export_root = Path(filters.pop("export_root", DEFAULT_EXPORT_ROOT))
        run_id = str(filters.pop("run_id", "") or _default_run_id(market_date_start, market_date_end))
        allow_live_db = bool(filters.pop("allow_live_db", False))
        export_dir = _unique_export_dir(export_root / run_id)
        validate_replay_paths(db_path=db_path, export_dir=export_dir, allow_live_db=allow_live_db)
        export_dir.mkdir(parents=True, exist_ok=True)

        start_load = market_date_start - timedelta(days=14)
        end_load = market_date_end + timedelta(days=max(exit_rule.max_holding_days * 3, 14))
        with get_connection(db_path) as connection:
            table_names = _table_names(connection)
            bars = _load_daily_bars(connection, start_load, end_load)
            filings = _load_filings(connection, table_names, start_load, end_load)
            universe = _load_universe(connection, table_names, market_date_start, market_date_end, filters)
            cost_audit = _cost_audit(connection, market_date_start, market_date_end)

        entries = _generate_entries(
            bars=bars,
            filings=filings,
            universe=universe,
            market_date_start=market_date_start,
            market_date_end=market_date_end,
            entry_rule=entry_rule,
            exit_rule=exit_rule,
        )
        trades = [
            _simulate_trade(entry, bars=bars, filings=filings, exit_rule=exit_rule)
            for entry in entries
        ]
        trades = [trade for trade in trades if trade is not None]

        manifest = {
            "command": "run-multiday-catalyst-replay",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(db_path),
            "export_dir": str(export_dir),
            "market_date_start": market_date_start.isoformat(),
            "market_date_end": market_date_end.isoformat(),
            "entry_rule": asdict(entry_rule),
            "exit_rule": asdict(exit_rule),
            "universe_filter": filters,
            "engine_boundary": "multiday_catalyst_replay_policy_only",
            "engine_spec": {
                "engine_id": MULTIDAY_ENGINE_SPEC.engine_id,
                "strategy_name": MULTIDAY_ENGINE_SPEC.strategy_name,
                "holding_period": MULTIDAY_ENGINE_SPEC.holding_period,
                "allows_overnight": MULTIDAY_ENGINE_SPEC.allows_overnight,
            },
            "reused_policy": {
                "multiday_engine": "penny_stock_radar.services.multiday_engine",
                "exit_rules": "penny_stock_radar.services.engine_rules.exit",
                "cost_source_policy": "penny_stock_radar.services.external_data_validator",
                "grade_stamp": "penny_stock_radar.services.replay_grade_stamper",
            },
        }
        progress = _progress_payload(
            market_date_start=market_date_start,
            market_date_end=market_date_end,
            bars=bars,
            entries=entries,
            trades=trades,
        )
        summary = _summary_payload(
            entries=entries,
            trades=trades,
            universe=universe,
            cost_audit=cost_audit,
        )

        manifest_path = export_dir / "run_manifest.json"
        progress_path = export_dir / "progress.json"
        summary_path = export_dir / "replay_summary.json"
        research_audit_path = export_dir / "research_audit_report.json"
        entries_path = export_dir / "catalyst_entries.csv"
        trades_path = export_dir / "catalyst_trades.csv"

        _write_json(manifest_path, manifest)
        _write_json(progress_path, progress)
        _write_json(summary_path, summary)
        _write_json(research_audit_path, _research_audit_payload(summary, cost_audit, universe))
        grade = stamp_replay_run(export_dir)
        _write_entries_csv(entries_path, entries, grade)
        _write_trades_csv(trades_path, trades, grade)
        return MultidayCatalystReplayResult(
            export_dir=export_dir,
            manifest_path=manifest_path,
            progress_path=progress_path,
            summary_path=summary_path,
            trades_path=trades_path,
            entries_path=entries_path,
        )


def _generate_entries(
    *,
    bars: dict[tuple[str, date], _DailyBar],
    filings: dict[str, list[dict[str, Any]]],
    universe: dict[str, dict[str, Any]],
    market_date_start: date,
    market_date_end: date,
    entry_rule: CatalystEntryRule,
    exit_rule: CatalystExitRule,
) -> list[_Entry]:
    entries: list[_Entry] = []
    symbols = sorted(universe)
    for catalyst_date in _date_range(market_date_start, market_date_end):
        for symbol in symbols:
            d_bar = bars.get((symbol, catalyst_date))
            if d_bar is None or d_bar.dollar_volume < entry_rule.min_dollar_volume_d:
                continue
            filing_rows = _filings_within_days(
                filings.get(symbol, []),
                catalyst_date,
                entry_rule.require_sec_filing_within_days,
            )
            if not filing_rows:
                continue
            entry_date = catalyst_date
            if entry_rule.entry_time != "d_close":
                entry_date = _next_trading_date(bars, symbol, catalyst_date)
                if entry_date is None:
                    continue
            entry_bar = bars.get((symbol, entry_date))
            if entry_bar is None:
                continue
            entry_at, entry_price = _entry_point(entry_bar, entry_rule.entry_time)
            if entry_price <= 0:
                continue
            stop_price, stop_rule = _structure_stop(
                bars=bars,
                symbol=symbol,
                catalyst_date=catalyst_date,
                entry_date=entry_date,
                entry_price=entry_price,
                exit_rule=exit_rule,
            )
            if stop_price <= 0:
                continue
            entries.append(
                _Entry(
                    symbol=symbol,
                    catalyst_date=catalyst_date,
                    entry_date=entry_date,
                    entry_at=entry_at,
                    entry_price=entry_price,
                    catalyst_dollar_volume=d_bar.dollar_volume,
                    stop_price=stop_price,
                    stop_rule=stop_rule,
                    filing_count=len(filing_rows),
                    filing_forms=tuple(sorted({str(row["form"]) for row in filing_rows})),
                    source=entry_bar.source,
                    reasons=(
                        f"entry_time:{entry_rule.entry_time}",
                        f"sec_filing_within_days:{entry_rule.require_sec_filing_within_days}",
                        f"min_dollar_volume_d:{entry_rule.min_dollar_volume_d:.0f}",
                        f"structure_stop:{stop_rule}",
                    ),
                )
            )
    return sorted(entries, key=lambda row: (row.entry_at, row.symbol))


def _simulate_trade(
    entry: _Entry,
    *,
    bars: dict[tuple[str, date], _DailyBar],
    filings: dict[str, list[dict[str, Any]]],
    exit_rule: CatalystExitRule,
) -> _Trade | None:
    trade_dates = _symbol_dates_on_or_after(bars, entry.symbol, entry.entry_date)
    if not trade_dates:
        return None
    catalyst_bar = bars.get((entry.symbol, entry.catalyst_date))
    catalyst_volume = catalyst_bar.volume if catalyst_bar else 0.0
    previous_close = entry.entry_price
    for index, current_date in enumerate(trade_dates, start=1):
        bar = bars[(entry.symbol, current_date)]
        if current_date == entry.entry_date and entry.entry_at >= bar.close_at:
            continue
        reason = ""
        exit_price = bar.close_price
        reasons: list[str] = []
        if bar.low_price <= entry.stop_price:
            reason = "structure_stop"
            exit_price = entry.stop_price
            reasons.append(entry.stop_rule)
        elif (
            exit_rule.exit_on_follow_on_filing
            and _has_follow_on_filing(filings.get(entry.symbol, []), entry.catalyst_date, current_date)
        ):
            reason = "follow_on_filing"
            reasons.append(f"follow_on_date:{current_date.isoformat()}")
        elif (
            exit_rule.exit_on_volume_exhaustion
            and current_date > entry.catalyst_date
            and catalyst_volume > 0
            and bar.volume < catalyst_volume * 0.40
            and bar.close_price <= previous_close
        ):
            reason = "volume_exhaustion"
            reasons.append(f"volume_ratio:{bar.volume / catalyst_volume:.4f}")
        elif index >= exit_rule.max_holding_days:
            reason = "max_holding_days"
            reasons.append(f"max_holding_days:{exit_rule.max_holding_days}")
        previous_close = bar.close_price
        if not reason:
            continue
        return _Trade(
            symbol=entry.symbol,
            catalyst_date=entry.catalyst_date,
            entry_date=entry.entry_date,
            entry_at=entry.entry_at,
            entry_price=entry.entry_price,
            exit_date=current_date,
            exit_at=bar.close_at,
            exit_price=exit_price,
            exit_reason=reason,
            holding_days=index,
            gross_return_pct=((exit_price - entry.entry_price) / entry.entry_price) * 100.0,
            stop_price=entry.stop_price,
            filing_count=entry.filing_count,
            source=entry.source,
            reasons=tuple(reasons),
        )
    return None


def _load_daily_bars(
    connection: sqlite3.Connection,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, date], _DailyBar]:
    if "historical_minute_bars" not in _table_names(connection):
        return {}
    rows = connection.execute(
        """
        SELECT *
        FROM historical_minute_bars
        WHERE market_date BETWEEN ? AND ?
          AND open_price > 0
          AND close_price > 0
        ORDER BY symbol ASC, market_date ASC, bar_at ASC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    grouped: dict[tuple[str, date], list[sqlite3.Row]] = {}
    for row in rows:
        try:
            market_date = date.fromisoformat(str(row["market_date"]))
        except ValueError:
            continue
        grouped.setdefault((str(row["symbol"]).upper(), market_date), []).append(row)

    bars: dict[tuple[str, date], _DailyBar] = {}
    for key, group in grouped.items():
        first = group[0]
        last = group[-1]
        at_0935_row = _first_row_at_or_after(group, time(9, 35)) or first
        volume = sum(float(row["volume"] or 0.0) for row in group)
        dollar_volume = sum(
            float(row["volume"] or 0.0) * float(row["close_price"] or 0.0)
            for row in group
        )
        spreads = [
            float(row["spread_pct"])
            for row in group
            if row["spread_pct"] is not None and float(row["spread_pct"]) >= 0
        ]
        bars[key] = _DailyBar(
            symbol=key[0],
            market_date=key[1],
            open_at=_parse_datetime(str(first["bar_at"])),
            open_price=float(first["open_price"]),
            at_0935=_parse_datetime(str(at_0935_row["bar_at"])),
            price_0935=float(at_0935_row["open_price"]),
            close_at=_parse_datetime(str(last["bar_at"])),
            close_price=float(last["close_price"]),
            high_price=max(float(row["high_price"]) for row in group),
            low_price=min(float(row["low_price"]) for row in group),
            volume=volume,
            dollar_volume=dollar_volume,
            source=str(last["source"] or ""),
            spread_pct=(sum(spreads) / len(spreads)) if spreads else None,
        )
    return bars


def _load_filings(
    connection: sqlite3.Connection,
    table_names: set[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]]:
    if "filings" not in table_names:
        return {}
    rows = connection.execute(
        """
        SELECT symbol, form, filing_date, acceptance_datetime, accession_number, summary
        FROM filings
        WHERE filing_date BETWEEN ? AND ?
        ORDER BY symbol ASC, filing_date ASC, acceptance_datetime ASC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    filings: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row["symbol"]).upper()
        accepted_at = _parse_optional_datetime(row["acceptance_datetime"])
        filing_date = _parse_optional_date(row["filing_date"])
        effective_date = accepted_at.date() if accepted_at is not None else filing_date
        if effective_date is None:
            continue
        filings.setdefault(symbol, []).append(
            {
                "symbol": symbol,
                "form": str(row["form"] or ""),
                "filing_date": filing_date,
                "accepted_at": accepted_at,
                "effective_date": effective_date,
                "accession_number": str(row["accession_number"] or ""),
                "summary": str(row["summary"] or ""),
            }
        )
    return filings


def _load_universe(
    connection: sqlite3.Connection,
    table_names: set[str],
    start_date: date,
    end_date: date,
    filters: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    explicit_symbols = [_normalize_symbol(item) for item in filters.get("symbols", [])]
    require_kis_tradable = bool(filters.get("require_kis_tradable", True))
    passed_only = bool(filters.get("passed_only", True))
    if explicit_symbols:
        return {
            symbol: {"exchange": None, "classification": "manual"}
            for symbol in explicit_symbols
            if symbol
        }
    if "scan_runs" not in table_names or "universe" not in table_names:
        return {}
    passed_clause = "AND u.passed_filters = 1" if passed_only else ""
    rows = connection.execute(
        f"""
        SELECT u.symbol, u.exchange
        FROM universe u
        JOIN scan_runs sr ON sr.scan_id = u.scan_id
        WHERE sr.snapshot_role = 'point_in_time'
          AND sr.market_date BETWEEN ? AND ?
          {passed_clause}
        ORDER BY u.symbol ASC, sr.market_date DESC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    universe: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row["symbol"])
        if not symbol or symbol in universe:
            continue
        exchange = _normalize_exchange(row["exchange"])
        classification = classify_listing_exchange(exchange)
        if require_kis_tradable and classification == "untradable":
            continue
        universe[symbol] = {"exchange": exchange, "classification": classification}
    return universe


def _cost_audit(
    connection: sqlite3.Connection,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    base = {
        "cost_source_policy": cost_source_policy_summary(),
        "eod_representative_sources": sorted(EOD_REPRESENTATIVE_COST_SOURCES),
        "minute_bar_source_counts": {},
        "minute_bar_cost_eligible_source_counts": {},
        "minute_bar_diagnostic_only_source_counts": {},
        "excluded_minute_bar_sources": [],
        "cost_eligible_sample_count": 0,
    }
    if "historical_minute_bars" not in _table_names(connection):
        return {**base, "status": "blocked", "reason": "historical_minute_bars_missing"}
    rows = connection.execute(
        """
        SELECT source, COUNT(*) AS row_count
        FROM historical_minute_bars
        WHERE market_date BETWEEN ? AND ?
        GROUP BY source
        ORDER BY source ASC
        """,
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    counts = {str(row["source"] or ""): int(row["row_count"] or 0) for row in rows}
    eligible = {
        source: count
        for source, count in counts.items()
        if _is_multiday_cost_eligible_source(source)
    }
    diagnostic = {
        source: count
        for source, count in counts.items()
        if is_diagnostic_only_source(source)
    }
    eligible_count = sum(eligible.values())
    return {
        **base,
        "status": "ready" if eligible_count > 0 else "blocked",
        "reason": None if eligible_count > 0 else "cost_distribution_eligible_source_missing",
        "minute_bar_source_counts": counts,
        "minute_bar_cost_eligible_source_counts": eligible,
        "minute_bar_diagnostic_only_source_counts": diagnostic,
        "excluded_minute_bar_sources": sorted(set(counts) - set(eligible)),
        "cost_eligible_sample_count": eligible_count,
    }


def _research_audit_payload(
    summary: dict[str, Any],
    cost_audit: dict[str, Any],
    universe: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    blockers = ["multiday_catalyst_replay_scaffold_not_falsification_pass"]
    if not universe:
        blockers.append("point_in_time_universe_missing")
    if int(cost_audit.get("cost_eligible_sample_count") or 0) <= 0:
        blockers.append(str(cost_audit.get("reason") or "cost_distribution_eligible_source_missing"))
    return {
        "decision_gate": {"decision": "BLOCKED", "status": "BLOCKED"},
        "blockers": blockers,
        "universe_tradability_audit": {
            "decision_blocker": False,
            "decision_grade": True,
            "grade_reason": "kis_untradable_symbols_excluded",
            "total_symbols": summary["universe_symbol_count"],
        },
        "cost_audit": {
            "l1_source_counts": {},
            "l1_cost_eligible_source_counts": {},
            "minute_spread_source_counts": cost_audit["minute_bar_source_counts"],
            "minute_spread_cost_eligible_source_counts": cost_audit[
                "minute_bar_cost_eligible_source_counts"
            ],
            "excluded_l1_sources": [],
            "excluded_minute_spread_sources": cost_audit["excluded_minute_bar_sources"],
            "cost_source_policy": cost_audit["cost_source_policy"],
        },
    }


def _summary_payload(
    *,
    entries: list[_Entry],
    trades: list[_Trade],
    universe: dict[str, dict[str, Any]],
    cost_audit: dict[str, Any],
) -> dict[str, Any]:
    closed = len(trades)
    winners = sum(1 for trade in trades if trade.gross_return_pct > 0)
    total_return = sum(trade.gross_return_pct for trade in trades)
    return {
        "strategy": "multiday_catalyst_replay_v1",
        "status": "completed",
        "entry_count": len(entries),
        "closed_trade_count": closed,
        "win_rate_pct": (winners / closed * 100.0) if closed else 0.0,
        "average_return_pct": (total_return / closed) if closed else 0.0,
        "total_return_pct_points": total_return,
        "exit_reason_counts": _counts(trade.exit_reason for trade in trades),
        "universe_symbol_count": len(universe),
        "cost_audit": cost_audit,
        "note": "Scaffold output; decision grade requires the standard falsification gate.",
    }


def _progress_payload(
    *,
    market_date_start: date,
    market_date_end: date,
    bars: dict[tuple[str, date], _DailyBar],
    entries: list[_Entry],
    trades: list[_Trade],
) -> dict[str, Any]:
    bar_dates = {market_date for _, market_date in bars}
    entries_by_date = _counts(entry.catalyst_date.isoformat() for entry in entries)
    exits_by_date = _counts(trade.exit_date.isoformat() for trade in trades)
    return {
        "status": "completed",
        "dates": {
            day.isoformat(): {
                "status": "completed" if day in bar_dates else "skipped",
                "entry_count": entries_by_date.get(day.isoformat(), 0),
                "exit_count": exits_by_date.get(day.isoformat(), 0),
            }
            for day in _date_range(market_date_start, market_date_end)
        },
    }


def _entry_point(bar: _DailyBar, entry_time: str) -> tuple[datetime, float]:
    if entry_time == "d_close":
        return bar.close_at, bar.close_price
    if entry_time == "d_plus_1_0935":
        return bar.at_0935, bar.price_0935
    return bar.open_at, bar.open_price


def _structure_stop(
    *,
    bars: dict[tuple[str, date], _DailyBar],
    symbol: str,
    catalyst_date: date,
    entry_date: date,
    entry_price: float,
    exit_rule: CatalystExitRule,
) -> tuple[float, str]:
    prior_dates = [day for day in _symbol_dates_before(bars, symbol, entry_date)]
    if exit_rule.structure_stop == "d_minus_1_low":
        catalyst_prior_dates = [day for day in _symbol_dates_before(bars, symbol, catalyst_date)]
        if catalyst_prior_dates:
            prior = bars[(symbol, catalyst_prior_dates[-1])]
            return prior.low_price, f"d_minus_1_low:{prior.market_date.isoformat()}"
        catalyst = bars.get((symbol, catalyst_date))
        return (catalyst.low_price if catalyst else entry_price * 0.9), "d_minus_1_low:fallback"
    if exit_rule.structure_stop == "n_day_low":
        window = max(int(exit_rule.structure_stop_param), 1)
        lows = [bars[(symbol, day)].low_price for day in prior_dates[-window:]]
        if lows:
            return min(lows), f"n_day_low:{window}"
        return entry_price * 0.9, f"n_day_low:{window}:fallback"
    ranges = [
        bars[(symbol, day)].high_price - bars[(symbol, day)].low_price
        for day in prior_dates[-14:]
    ]
    atr = (sum(ranges) / len(ranges)) if ranges else entry_price * 0.05
    return max(entry_price - atr * exit_rule.structure_stop_param, 0.01), (
        f"atr_multiple:{exit_rule.structure_stop_param:g}"
    )


def _filings_within_days(
    rows: list[dict[str, Any]],
    catalyst_date: date,
    days: int,
) -> list[dict[str, Any]]:
    start = catalyst_date - timedelta(days=days - 1)
    return [
        row
        for row in rows
        if start <= row["effective_date"] <= catalyst_date
    ]


def _has_follow_on_filing(
    rows: list[dict[str, Any]],
    catalyst_date: date,
    current_date: date,
) -> bool:
    return any(catalyst_date < row["effective_date"] <= current_date for row in rows)


def _next_trading_date(
    bars: dict[tuple[str, date], _DailyBar],
    symbol: str,
    market_date: date,
) -> date | None:
    dates = _symbol_dates_on_or_after(bars, symbol, market_date + timedelta(days=1))
    return dates[0] if dates else None


def _symbol_dates_before(
    bars: dict[tuple[str, date], _DailyBar],
    symbol: str,
    market_date: date,
) -> list[date]:
    return sorted(day for sym, day in bars if sym == symbol and day < market_date)


def _symbol_dates_on_or_after(
    bars: dict[tuple[str, date], _DailyBar],
    symbol: str,
    market_date: date,
) -> list[date]:
    return sorted(day for sym, day in bars if sym == symbol and day >= market_date)


def _first_row_at_or_after(rows: list[sqlite3.Row], target: time) -> sqlite3.Row | None:
    for row in rows:
        if _parse_datetime(str(row["bar_at"])).timetz().replace(tzinfo=None) >= target:
            return row
    return None


def _write_entries_csv(path: Path, entries: list[_Entry], grade) -> None:
    fieldnames = [
        "symbol",
        "catalyst_date",
        "entry_date",
        "entry_at",
        "entry_price",
        "catalyst_dollar_volume",
        "stop_price",
        "planned_risk_pct",
        "stop_rule",
        "filing_count",
        "filing_forms",
        "source",
        "reasons",
    ]
    rows = [
        {
            **_entry_to_dict(entry),
            "planned_risk_pct": _round_optional(
                planned_risk_pct(entry.entry_price, entry.stop_price)
            ),
            "filing_forms": "|".join(entry.filing_forms),
            "reasons": "|".join(entry.reasons),
        }
        for entry in entries
    ]
    _write_csv(path, fieldnames, rows, grade)


def _write_trades_csv(path: Path, trades: list[_Trade], grade) -> None:
    fieldnames = [
        "symbol",
        "catalyst_date",
        "entry_date",
        "entry_at",
        "entry_price",
        "exit_date",
        "exit_at",
        "exit_price",
        "exit_reason",
        "holding_days",
        "gross_return_pct",
        "stop_price",
        "planned_risk_pct",
        "filing_count",
        "source",
        "reasons",
    ]
    rows = [
        {
            **_trade_to_dict(trade),
            "planned_risk_pct": _round_optional(
                planned_risk_pct(trade.entry_price, trade.stop_price)
            ),
            "reasons": "|".join(trade.reasons),
        }
        for trade in trades
    ]
    _write_csv(path, fieldnames, rows, grade)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]], grade) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(csv_decision_grade_comment(grade) + "\n")
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _entry_to_dict(entry: _Entry) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "catalyst_date": entry.catalyst_date.isoformat(),
        "entry_date": entry.entry_date.isoformat(),
        "entry_at": entry.entry_at.isoformat(),
        "entry_price": round(entry.entry_price, 6),
        "catalyst_dollar_volume": round(entry.catalyst_dollar_volume, 2),
        "stop_price": round(entry.stop_price, 6),
        "stop_rule": entry.stop_rule,
        "filing_count": entry.filing_count,
        "filing_forms": entry.filing_forms,
        "source": entry.source,
        "reasons": entry.reasons,
    }


def _trade_to_dict(trade: _Trade) -> dict[str, Any]:
    return {
        "symbol": trade.symbol,
        "catalyst_date": trade.catalyst_date.isoformat(),
        "entry_date": trade.entry_date.isoformat(),
        "entry_at": trade.entry_at.isoformat(),
        "entry_price": round(trade.entry_price, 6),
        "exit_date": trade.exit_date.isoformat(),
        "exit_at": trade.exit_at.isoformat(),
        "exit_price": round(trade.exit_price, 6),
        "exit_reason": trade.exit_reason,
        "holding_days": trade.holding_days,
        "gross_return_pct": round(trade.gross_return_pct, 6),
        "stop_price": round(trade.stop_price, 6),
        "filing_count": trade.filing_count,
        "source": trade.source,
        "reasons": trade.reasons,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _round_optional(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=EASTERN) if parsed.tzinfo is None else parsed


def _parse_optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _parse_datetime(str(value))
    except ValueError:
        return None


def _parse_optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _date_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _normalize_exchange(value: object) -> str | None:
    text = str(value or "").strip().upper()
    aliases = {"Q": "NASDAQ", "N": "NYSE", "A": "NYSE_AMERICAN"}
    return aliases.get(text, text) if text else None


def _is_multiday_cost_eligible_source(source: str) -> bool:
    normalized = str(source or "").strip().lower()
    return is_cost_eligible_source(source) or normalized in EOD_REPRESENTATIVE_COST_SOURCES


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _unique_export_dir(path: Path) -> Path:
    if not path.exists() or not any(path.iterdir()):
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate unique export directory for {path}")


def _default_run_id(start_date: date, end_date: date) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"multiday_catalyst_{start_date.isoformat()}_{end_date.isoformat()}_{timestamp}"
