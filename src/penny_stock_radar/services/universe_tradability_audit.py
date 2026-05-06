from __future__ import annotations

import csv
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from ..db import get_connection
from .paper_runtime import write_csv

KIS_TRADABLE_EXCHANGES = frozenset(
    {
        "NASDAQ",
        "NASDAQ_NMS",
        "NASDAQ_CAPITAL",
        "NASDAQ_GLOBAL",
        "NYSE",
        "NYSE_AMERICAN",
        "ARCA",
    }
)
KIS_UNTRADABLE_EXCHANGES = frozenset(
    {
        "OTC",
        "OTC_PINK",
        "OTC_OTCQB",
        "OTC_OTCQX",
        "OTCBB",
        "PINK",
        "GREY",
    }
)
DEFAULT_BLOCKER_THRESHOLD = 0.30


@dataclass(frozen=True, slots=True)
class SymbolTradability:
    symbol: str
    listing_exchange: str | None
    tradable_via_kis: bool
    classification: Literal["tradable", "untradable", "unknown"]
    evidence_source: str


@dataclass(frozen=True, slots=True)
class UniverseTradabilityReport:
    universe_id: str
    market_date_range: tuple[date, date]
    total_symbols: int
    tradable_count: int
    untradable_count: int
    unknown_count: int
    untradable_pct: float
    unknown_pct: float
    by_symbol: list[SymbolTradability]
    decision_blocker: bool
    decision_grade: bool
    grade_reason: str


def audit_universe(
    db_path: Path,
    universe_source: Literal["watchlist", "replay_log", "scanner_universe"],
    universe_args: dict,
) -> UniverseTradabilityReport:
    symbols, market_dates, universe_id = _resolve_universe(
        db_path,
        universe_source,
        universe_args,
    )
    if not market_dates:
        today = date.today()
        market_range = (today, today)
    else:
        market_range = (min(market_dates), max(market_dates))

    manual_exchanges = {
        _normalize_symbol(symbol): str(exchange)
        for symbol, exchange in dict(universe_args.get("manual_listing_exchanges") or {}).items()
    }
    yfinance_cache = _load_yfinance_cache(universe_args.get("yfinance_cache_path"))
    enable_yfinance = bool(universe_args.get("enable_yfinance", False))
    yfinance_delay_seconds = float(universe_args.get("yfinance_delay_seconds", 0.25))

    rows: list[SymbolTradability] = []
    for symbol in sorted({_normalize_symbol(item) for item in symbols if _normalize_symbol(item)}):
        exchange, evidence_source = _lookup_listing_exchange(
            db_path,
            symbol=symbol,
            market_dates=market_dates,
            manual_exchanges=manual_exchanges,
            yfinance_cache=yfinance_cache,
            enable_yfinance=enable_yfinance,
            yfinance_delay_seconds=yfinance_delay_seconds,
        )
        normalized_exchange = normalize_listing_exchange(exchange)
        classification = classify_listing_exchange(normalized_exchange)
        rows.append(
            SymbolTradability(
                symbol=symbol,
                listing_exchange=normalized_exchange,
                tradable_via_kis=classification == "tradable",
                classification=classification,
                evidence_source=evidence_source,
            )
        )

    total = len(rows)
    tradable_count = sum(1 for row in rows if row.classification == "tradable")
    untradable_count = sum(1 for row in rows if row.classification == "untradable")
    unknown_count = sum(1 for row in rows if row.classification == "unknown")
    untradable_pct = _pct(untradable_count, total)
    unknown_pct = _pct(unknown_count, total)
    decision_blocker = (
        total > 0
        and ((untradable_count + unknown_count) / total) >= DEFAULT_BLOCKER_THRESHOLD
    )
    grade_reason = (
        "untradable_plus_unknown_pct_below_30"
        if not decision_blocker
        else "universe_kis_untradable_pct_high"
    )
    _write_yfinance_cache(universe_args.get("yfinance_cache_path"), yfinance_cache)
    return UniverseTradabilityReport(
        universe_id=universe_id,
        market_date_range=market_range,
        total_symbols=total,
        tradable_count=tradable_count,
        untradable_count=untradable_count,
        unknown_count=unknown_count,
        untradable_pct=untradable_pct,
        unknown_pct=unknown_pct,
        by_symbol=rows,
        decision_blocker=decision_blocker,
        decision_grade=not decision_blocker,
        grade_reason=grade_reason,
    )


def write_report(report: UniverseTradabilityReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = report_to_dict(report)
    json_path = out_dir / "universe_tradability_report.json"
    csv_path = out_dir / "universe_tradability_by_symbol.csv"
    md_path = out_dir / "universe_tradability_summary.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(
        csv_path,
        [
            {
                **asdict(row),
                "decision_grade": report.decision_grade,
                "grade_reason": report.grade_reason,
            }
            for row in report.by_symbol
        ],
    )
    md_path.write_text(_markdown_summary(report), encoding="utf-8")
    return json_path


def report_to_dict(report: UniverseTradabilityReport) -> dict[str, Any]:
    return {
        "universe_id": report.universe_id,
        "market_date_range": [
            report.market_date_range[0].isoformat(),
            report.market_date_range[1].isoformat(),
        ],
        "total_symbols": report.total_symbols,
        "tradable_count": report.tradable_count,
        "untradable_count": report.untradable_count,
        "unknown_count": report.unknown_count,
        "untradable_pct": report.untradable_pct,
        "unknown_pct": report.unknown_pct,
        "kis_tradable_universe_pct": _pct(report.tradable_count, report.total_symbols),
        "by_symbol": [asdict(row) for row in report.by_symbol],
        "decision_blocker": report.decision_blocker,
        "decision_grade": report.decision_grade,
        "grade_reason": report.grade_reason,
    }


def summarize_kis_tradability_from_connection(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> dict[str, Any]:
    if "scan_runs" not in table_names or "universe" not in table_names:
        return _empty_tradability_summary("missing_scan_or_universe_table")
    rows = connection.execute(
        """
        SELECT u.symbol, u.exchange
        FROM universe u
        JOIN scan_runs sr ON sr.scan_id = u.scan_id
        WHERE sr.snapshot_role = 'point_in_time'
          AND sr.market_date IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return _empty_tradability_summary("no_point_in_time_universe_rows")
    by_symbol: dict[str, str | None] = {}
    for row in rows:
        symbol = _normalize_symbol(row["symbol"])
        if symbol and symbol not in by_symbol:
            by_symbol[symbol] = normalize_listing_exchange(row["exchange"])
    classifications = [classify_listing_exchange(exchange) for exchange in by_symbol.values()]
    total = len(classifications)
    tradable = sum(1 for item in classifications if item == "tradable")
    untradable = sum(1 for item in classifications if item == "untradable")
    unknown = sum(1 for item in classifications if item == "unknown")
    decision_blocker = total > 0 and ((untradable + unknown) / total) >= DEFAULT_BLOCKER_THRESHOLD
    return {
        "total_symbols": total,
        "tradable_count": tradable,
        "untradable_count": untradable,
        "unknown_count": unknown,
        "kis_tradable_universe_pct": _pct(tradable, total),
        "decision_blocker": decision_blocker,
        "decision_grade": not decision_blocker,
        "grade_reason": (
            "untradable_plus_unknown_pct_below_30"
            if not decision_blocker
            else "universe_kis_untradable_pct_high"
        ),
    }


def normalize_listing_exchange(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    compact = (
        raw.upper()
        .replace(".", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )
    compact = "_".join(part for part in compact.split("_") if part)
    aliases = {
        "Q": "NASDAQ",
        "NASDAQGS": "NASDAQ_GLOBAL",
        "NASDAQGM": "NASDAQ_GLOBAL",
        "NASDAQCM": "NASDAQ_CAPITAL",
        "NASDAQ_GLOBAL_SELECT": "NASDAQ_GLOBAL",
        "NASDAQ_GLOBAL_MARKET": "NASDAQ_GLOBAL",
        "NASDAQ_CAPITAL_MARKET": "NASDAQ_CAPITAL",
        "NMS": "NASDAQ_NMS",
        "N": "NYSE",
        "A": "NYSE_AMERICAN",
        "AMEX": "NYSE_AMERICAN",
        "NYSE_MKT": "NYSE_AMERICAN",
        "NYSE_AMEX": "NYSE_AMERICAN",
        "NYSE_AMERICAN": "NYSE_AMERICAN",
        "P": "ARCA",
        "NYSE_ARCA": "ARCA",
        "ARCA": "ARCA",
        "OTC_MARKET": "OTC",
        "OTC_MARKETS": "OTC",
        "OTC_PINK_CURRENT": "OTC_PINK",
        "OTC_PINK_LIMITED": "OTC_PINK",
        "OTC_PINK_NO_INFORMATION": "OTC_PINK",
        "OTCQB": "OTC_OTCQB",
        "OTC_QB": "OTC_OTCQB",
        "OTCQX": "OTC_OTCQX",
        "OTC_QX": "OTC_OTCQX",
        "PINK_SHEETS": "PINK",
        "GREY_MARKET": "GREY",
        "GRAY": "GREY",
    }
    return aliases.get(compact, compact)


def classify_listing_exchange(
    listing_exchange: str | None,
) -> Literal["tradable", "untradable", "unknown"]:
    if listing_exchange in KIS_TRADABLE_EXCHANGES:
        return "tradable"
    if listing_exchange in KIS_UNTRADABLE_EXCHANGES:
        return "untradable"
    return "unknown"


def _resolve_universe(
    db_path: Path,
    universe_source: Literal["watchlist", "replay_log", "scanner_universe"],
    universe_args: dict,
) -> tuple[list[str], list[date], str]:
    if "symbols" in universe_args:
        symbols = [_normalize_symbol(item) for item in universe_args.get("symbols") or []]
        dates = [_coerce_date(item) for item in universe_args.get("market_dates") or []]
        return (
            symbols,
            [item for item in dates if item is not None],
            f"{universe_source}:manual_symbols",
        )
    if universe_source == "replay_log":
        return _resolve_replay_log_universe(universe_args)
    if not db_path.exists():
        return [], [], f"{universe_source}:database_missing"
    with get_connection(db_path) as connection:
        table_names = _table_names(connection)
        if universe_source == "watchlist":
            return _resolve_watchlist_universe(connection, table_names, universe_args)
        return _resolve_scanner_universe(connection, table_names, universe_args)


def _resolve_replay_log_universe(universe_args: dict) -> tuple[list[str], list[date], str]:
    replay_dir = universe_args.get("replay_dir")
    trade_log = universe_args.get("trade_log")
    if trade_log is None and replay_dir is not None:
        trade_log = Path(replay_dir) / "paper_trade_log.csv"
    if trade_log is None or not Path(trade_log).exists():
        return [], [], f"replay_log:{Path(replay_dir or '').name or 'missing'}"
    symbols: list[str] = []
    dates: list[date] = []
    with Path(trade_log).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = _normalize_symbol(row.get("symbol"))
            if symbol:
                symbols.append(symbol)
                exchange = row.get("listing_exchange") or row.get("exchange")
                if exchange:
                    universe_args.setdefault("manual_listing_exchanges", {})[symbol] = exchange
            market_date = _coerce_date(row.get("market_date")) or _market_date_from_row(row)
            if market_date is not None:
                dates.append(market_date)
    run_id = Path(replay_dir).name if replay_dir is not None else Path(trade_log).stem
    return symbols, dates, f"replay_log:{run_id}"


def _resolve_watchlist_universe(
    connection: sqlite3.Connection,
    table_names: set[str],
    universe_args: dict,
) -> tuple[list[str], list[date], str]:
    if "watchlist" not in table_names:
        return [], [], "watchlist:missing_table"
    scan_id = universe_args.get("scan_id") or _latest_scan_id(connection, "watchlist")
    if scan_id is None:
        return [], [], "watchlist:missing_scan"
    rows = connection.execute(
        "SELECT symbol FROM watchlist WHERE scan_id = ? ORDER BY symbol",
        (scan_id,),
    ).fetchall()
    _add_scan_exchange_fallbacks(connection, table_names, str(scan_id), universe_args)
    return (
        [str(row["symbol"]) for row in rows],
        _scan_market_dates(connection, str(scan_id)),
        f"watchlist:{scan_id}",
    )


def _resolve_scanner_universe(
    connection: sqlite3.Connection,
    table_names: set[str],
    universe_args: dict,
) -> tuple[list[str], list[date], str]:
    if "universe" not in table_names:
        return [], [], "scanner_universe:missing_table"
    scan_id = universe_args.get("scan_id") or _latest_scan_id(connection, "universe")
    if scan_id is None:
        return [], [], "scanner_universe:missing_scan"
    passed_only = bool(universe_args.get("passed_only", False))
    if passed_only:
        rows = connection.execute(
            """
            SELECT symbol, exchange
            FROM universe
            WHERE scan_id = ? AND passed_filters = 1
            ORDER BY symbol
            """,
            (scan_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT symbol, exchange FROM universe WHERE scan_id = ? ORDER BY symbol",
            (scan_id,),
        ).fetchall()
    for row in rows:
        if row["exchange"]:
            universe_args.setdefault("manual_listing_exchanges", {})[str(row["symbol"])] = row[
                "exchange"
            ]
    return (
        [str(row["symbol"]) for row in rows],
        _scan_market_dates(connection, str(scan_id)),
        f"scanner_universe:{scan_id}",
    )


def _add_scan_exchange_fallbacks(
    connection: sqlite3.Connection,
    table_names: set[str],
    scan_id: str,
    universe_args: dict,
) -> None:
    if "universe" not in table_names:
        return
    rows = connection.execute(
        "SELECT symbol, exchange FROM universe WHERE scan_id = ?",
        (scan_id,),
    ).fetchall()
    for row in rows:
        if row["exchange"]:
            universe_args.setdefault("manual_listing_exchanges", {})[str(row["symbol"])] = (
                row["exchange"]
            )


def _lookup_listing_exchange(
    db_path: Path,
    *,
    symbol: str,
    market_dates: list[date],
    manual_exchanges: dict[str, str],
    yfinance_cache: dict[str, str | None],
    enable_yfinance: bool,
    yfinance_delay_seconds: float,
) -> tuple[str | None, str]:
    if db_path.exists():
        with get_connection(db_path) as connection:
            table_names = _table_names(connection)
            pit_exchange = _lookup_pit_exchange(connection, table_names, symbol, market_dates)
            if pit_exchange:
                return pit_exchange, "historical_universe_pit"
            nasdaq_exchange = _lookup_nasdaq_directory_exchange(connection, table_names, symbol)
            if nasdaq_exchange:
                return nasdaq_exchange, "nasdaq_symbol_directory"
    if symbol in manual_exchanges:
        return manual_exchanges[symbol], "manual"
    if enable_yfinance:
        exchange = _lookup_yfinance_exchange(
            symbol,
            cache=yfinance_cache,
            delay_seconds=yfinance_delay_seconds,
        )
        if exchange:
            return exchange, "yfinance"
    return None, "unknown"


def _lookup_pit_exchange(
    connection: sqlite3.Connection,
    table_names: set[str],
    symbol: str,
    market_dates: list[date],
) -> str | None:
    if "scan_runs" not in table_names or "universe" not in table_names:
        return None
    params: list[Any] = [symbol]
    date_clause = ""
    if market_dates:
        unique_dates = sorted(set(market_dates))
        if len(unique_dates) == 1:
            date_clause = "AND sr.market_date = ?"
            params.append(unique_dates[0].isoformat())
        else:
            date_clause = "AND sr.market_date BETWEEN ? AND ?"
            params.extend([unique_dates[0].isoformat(), unique_dates[-1].isoformat()])
    row = connection.execute(
        f"""
        SELECT u.exchange
        FROM universe u
        JOIN scan_runs sr ON sr.scan_id = u.scan_id
        WHERE UPPER(u.symbol) = ?
          AND sr.snapshot_role = 'point_in_time'
          AND sr.source != 'nasdaq_symbol_directory'
          AND u.exchange IS NOT NULL
          AND TRIM(u.exchange) != ''
          {date_clause}
        ORDER BY sr.market_date DESC, sr.created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return str(row["exchange"]) if row else None


def _lookup_nasdaq_directory_exchange(
    connection: sqlite3.Connection,
    table_names: set[str],
    symbol: str,
) -> str | None:
    if "scan_runs" not in table_names or "universe" not in table_names:
        return None
    row = connection.execute(
        """
        SELECT u.exchange
        FROM universe u
        JOIN scan_runs sr ON sr.scan_id = u.scan_id
        WHERE UPPER(u.symbol) = ?
          AND sr.source = 'nasdaq_symbol_directory'
          AND u.exchange IS NOT NULL
          AND TRIM(u.exchange) != ''
        ORDER BY sr.market_date DESC, sr.created_at DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return str(row["exchange"]) if row else None


def _lookup_yfinance_exchange(
    symbol: str,
    *,
    cache: dict[str, str | None],
    delay_seconds: float,
) -> str | None:
    if symbol in cache:
        return cache[symbol]
    try:
        import yfinance as yf  # type: ignore[import-not-found]
    except ImportError:
        cache[symbol] = None
        return None
    time.sleep(max(0.0, delay_seconds))
    try:
        exchange = yf.Ticker(symbol).info.get("exchange")
    except Exception:
        exchange = None
    cache[symbol] = str(exchange) if exchange else None
    return cache[symbol]


def _load_yfinance_cache(path_value: object) -> dict[str, str | None]:
    if path_value is None:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        _normalize_symbol(key): (str(value) if value else None)
        for key, value in payload.items()
    }


def _write_yfinance_cache(path_value: object, cache: dict[str, str | None]) -> None:
    if path_value is None:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _markdown_summary(report: UniverseTradabilityReport) -> str:
    return "\n".join(
        [
            "# Universe Tradability Audit",
            "",
            f"- Universe: `{report.universe_id}`",
            f"- Market date range: `{report.market_date_range[0]}` to `{report.market_date_range[1]}`",
            f"- Total symbols: {report.total_symbols}",
            f"- KIS tradable: {report.tradable_count} ({_pct(report.tradable_count, report.total_symbols):.2f}%)",
            f"- Untradable: {report.untradable_count} ({report.untradable_pct:.2f}%)",
            f"- Unknown: {report.unknown_count} ({report.unknown_pct:.2f}%)",
            f"- decision_grade: {report.decision_grade}",
            f"- grade_reason: {report.grade_reason}",
            "",
            "## Rule",
            "",
            "- `untradable_pct + unknown_pct >= 30%` blocks transferability claims.",
        ]
    ) + "\n"


def _empty_tradability_summary(reason: str) -> dict[str, Any]:
    return {
        "total_symbols": 0,
        "tradable_count": 0,
        "untradable_count": 0,
        "unknown_count": 0,
        "kis_tradable_universe_pct": None,
        "decision_blocker": False,
        "decision_grade": True,
        "grade_reason": reason,
    }


def _latest_scan_id(connection: sqlite3.Connection, table: str) -> str | None:
    rows = connection.execute(
        f"""
        SELECT sr.scan_id
        FROM scan_runs sr
        JOIN {table} t ON t.scan_id = sr.scan_id
        GROUP BY sr.scan_id, sr.created_at
        ORDER BY sr.created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return str(rows["scan_id"]) if rows else None


def _scan_market_dates(connection: sqlite3.Connection, scan_id: str) -> list[date]:
    row = connection.execute(
        "SELECT market_date FROM scan_runs WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()
    parsed = _coerce_date(row["market_date"]) if row else None
    return [parsed] if parsed is not None else []


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _coerce_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _market_date_from_row(row: dict[str, str]) -> date | None:
    for key in ("entry_at", "opened_at", "timestamp", "created_at"):
        parsed = _coerce_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _pct(count: int, total: int) -> float:
    return (count / total * 100.0) if total else 0.0
