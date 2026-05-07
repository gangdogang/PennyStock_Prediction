from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import get_connection

DEFAULT_OUTPUT_ROOT = Path("data/backtest_lab/filing_catalyst_tiers")

TRAP_FORM_HINTS = {"S-1", "S-3", "S-1/A", "S-3/A", "424B5", "424B4", "424B3"}
TRAP_KEYWORDS = (
    "at-the-market",
    "atm offering",
    "public offering",
    "registered direct",
    "private placement",
    "prospectus supplement",
    "shelf registration",
    "resale",
    "warrant",
    "exercise price",
    "convertible note",
    "convertible preferred",
    "equity line",
    "purchase agreement with",
    "reverse split",
    "reverse stock split",
    "minimum bid",
    "deficiency",
    "delisting",
    "going concern",
)
TIER1_KEYWORDS = (
    "fda approval",
    "approved by the fda",
    "clinical trial positive",
    "positive topline",
    "positive top-line",
    "phase 3",
    "phase iii",
    "contract award",
    "awarded a contract",
    "purchase order",
    "merger agreement",
    "definitive merger",
    "acquisition agreement",
    "tender offer",
)
TIER2_KEYWORDS = (
    "partnership",
    "collaboration",
    "strategic alliance",
    "product launch",
    "commercial launch",
    "insider purchase",
    "insider buying",
    "patent",
    "grant",
    "letter of intent",
    "loi",
    "material definitive agreement",
)
TIER3_KEYWORDS = (
    "corporate update",
    "business update",
    "name change",
    "presentation",
    "conference",
    "other events",
)


class FilingCatalystTierAuditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FilingCatalystTierAuditOptions:
    db_path: Path | None = None
    filings_csv: Path | None = None
    output_root: Path = DEFAULT_OUTPUT_ROOT
    run_id: str | None = None
    scan_id: str | None = None
    symbols: tuple[str, ...] = ()
    min_total_filings: int = 1


@dataclass(frozen=True, slots=True)
class FilingCatalystTierAuditResult:
    export_dir: Path
    summary_json_path: Path
    summary_md_path: Path
    classified_csv_path: Path
    summary: dict[str, Any]


class FilingCatalystTierAuditor:
    def audit(self, options: FilingCatalystTierAuditOptions) -> FilingCatalystTierAuditResult:
        rows, input_payload = _load_input_rows(options)
        if not rows:
            raise FilingCatalystTierAuditError("No filing rows found for catalyst tier audit.")

        symbol_filter = {symbol.upper() for symbol in options.symbols}
        if symbol_filter:
            rows = [row for row in rows if str(row.get("symbol", "")).upper() in symbol_filter]
        if not rows:
            raise FilingCatalystTierAuditError("No filing rows remain after symbol filtering.")

        classified = [_classify_row(row) for row in rows]
        run_id = options.run_id or datetime.now(timezone.utc).strftime(
            "filing_catalyst_tiers_%Y%m%d_%H%M%S"
        )
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        status, reasons = _coverage_gate(classified, min_total_filings=options.min_total_filings)
        summary = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "scope": "filing_catalyst_tier_inventory_diagnostic_only",
                "decision_grade": False,
                "cost_grade": "none",
                "cost_grade_reason": (
                    "Filing tiers classify catalyst/trap context only; they do not include "
                    "bid/ask, NBBO, L2, fill, borrow, halt, float, or KIS tradability evidence."
                ),
                "pass_meaning": (
                    "PASS only means filing rows were classified into catalyst/trap buckets. "
                    "It does not approve setup backtests or live trading."
                ),
            },
            "input": input_payload,
            "thresholds": {
                "min_total_filings": options.min_total_filings,
                "symbols": sorted(symbol_filter),
            },
            "filing_count": len(classified),
            "symbol_count": len({row["symbol"] for row in classified}),
            "tier_counts": _count_rows(classified, "catalyst_tier"),
            "trap_flag_counts": _count_rows(classified, "trap_flag"),
            "form_counts": _count_rows(classified, "form"),
            "top_symbols": _top_symbols(classified, top_n=25),
            "filing_catalyst_tier_gate": {
                "status": status,
                "reasons": reasons,
            },
        }

        classified_csv_path = export_dir / "classified_filings.csv"
        summary_json_path = export_dir / "filing_catalyst_tier_summary.json"
        summary_md_path = export_dir / "filing_catalyst_tier_summary.md"
        _write_csv(classified_csv_path, classified)
        summary_json_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_md_path.write_text(_markdown_summary(summary), encoding="utf-8")
        return FilingCatalystTierAuditResult(
            export_dir=export_dir,
            summary_json_path=summary_json_path,
            summary_md_path=summary_md_path,
            classified_csv_path=classified_csv_path,
            summary=summary,
        )


def classify_filing_catalyst(row: dict[str, object]) -> dict[str, object]:
    return _classify_row(row)


def _load_input_rows(
    options: FilingCatalystTierAuditOptions,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if options.filings_csv is not None:
        rows = _load_csv_rows(options.filings_csv)
        return rows, {
            "source": "csv",
            "filings_csv": str(options.filings_csv),
            "row_count": len(rows),
        }
    if options.db_path is None:
        raise FilingCatalystTierAuditError("Provide either --filings-csv or --db-path.")
    rows, resolved_scan_id = _load_db_rows(options.db_path, scan_id=options.scan_id)
    return rows, {
        "source": "database",
        "db_path": str(options.db_path),
        "scan_id": resolved_scan_id,
        "row_count": len(rows),
    }


def _load_csv_rows(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_db_rows(db_path: Path, *, scan_id: str | None) -> tuple[list[dict[str, object]], str | None]:
    with get_connection(db_path) as connection:
        if "filings" not in _table_names(connection):
            raise FilingCatalystTierAuditError(f"Database has no filings table: {db_path}")
        resolved_scan_id = scan_id or _latest_filing_scan_id(connection)
        if resolved_scan_id is None:
            return [], None
        rows = connection.execute(
            """
            SELECT *
            FROM filings
            WHERE scan_id = ?
            ORDER BY acceptance_datetime, symbol, accession_number
            """,
            (resolved_scan_id,),
        ).fetchall()
    return [dict(row) for row in rows], resolved_scan_id


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def _latest_filing_scan_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT scan_id
        FROM filings
        GROUP BY scan_id
        ORDER BY MAX(created_at) DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row[0]) if row else None


def _classify_row(row: dict[str, object]) -> dict[str, object]:
    symbol = str(row.get("symbol") or "").upper()
    form = str(row.get("form") or "").upper()
    text = _classification_text(row)
    item_numbers = _parse_list(row.get("item_numbers"))
    matched_keywords = _parse_list(row.get("matched_keywords"))
    themes = _parse_list(row.get("themes"))

    trap_hits = _keyword_hits(text, TRAP_KEYWORDS)
    tier1_hits = _keyword_hits(text, TIER1_KEYWORDS)
    tier2_hits = _keyword_hits(text, TIER2_KEYWORDS)
    tier3_hits = _keyword_hits(text, TIER3_KEYWORDS)
    if form in TRAP_FORM_HINTS and form != "RW":
        trap_hits = sorted(set([*trap_hits, f"form:{form}"]))
    if form == "RW":
        tier2_hits = sorted(set([*tier2_hits, "form:RW"]))

    if trap_hits:
        tier = "trap"
        direction = "bearish_or_no_long"
        reasons = trap_hits
    elif tier1_hits:
        tier = "tier_1"
        direction = "bullish_candidate"
        reasons = tier1_hits
    elif tier2_hits or "1.01" in item_numbers or "2.02" in item_numbers:
        tier = "tier_2"
        direction = "bullish_or_context_dependent"
        reasons = sorted(set([*tier2_hits, *[f"item:{item}" for item in item_numbers if item in {"1.01", "2.02"}]]))
    elif tier3_hits or form == "8-K":
        tier = "tier_3"
        direction = "weak_or_context_dependent"
        reasons = tier3_hits or [f"form:{form}"]
    else:
        tier = "unknown"
        direction = "unclassified"
        reasons = []

    return {
        "symbol": symbol,
        "form": form,
        "filing_date": str(row.get("filing_date") or row.get("eligible_for_market_date") or ""),
        "acceptance_datetime": str(row.get("acceptance_datetime") or ""),
        "accession_number": str(row.get("accession_number") or ""),
        "primary_doc_description": str(row.get("primary_doc_description") or ""),
        "summary": str(row.get("summary") or ""),
        "item_numbers": "|".join(item_numbers),
        "themes": "|".join(themes),
        "matched_keywords": "|".join(matched_keywords),
        "catalyst_tier": tier,
        "catalyst_direction": direction,
        "trap_flag": str(bool(trap_hits)).lower(),
        "classification_reasons": "|".join(reasons),
        "decision_grade": "false",
        "cost_grade": "none",
    }


def _classification_text(row: dict[str, object]) -> str:
    parts = [
        row.get("form"),
        row.get("primary_doc_description"),
        row.get("summary"),
        " ".join(_parse_list(row.get("item_numbers"))),
        " ".join(_parse_list(row.get("matched_keywords"))),
        " ".join(_parse_list(row.get("themes"))),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _parse_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if "|" in text:
        return [part.strip() for part in text.split("|") if part.strip()]
    return [text]


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def _coverage_gate(
    rows: list[dict[str, object]],
    *,
    min_total_filings: int,
) -> tuple[str, list[str]]:
    if len(rows) < min_total_filings:
        return "BLOCKED", [f"filing_count_lt_{min_total_filings}: {len(rows)}"]
    return "PASS", ["filing_catalyst_tier_inventory_created"]


def _count_rows(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counts = Counter(str(row.get(key) or "UNKNOWN") for row in rows)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_symbols(rows: list[dict[str, object]], *, top_n: int) -> list[dict[str, object]]:
    total = len(rows)
    counts = Counter(str(row.get("symbol") or "UNKNOWN") for row in rows)
    return [
        {
            "symbol": symbol,
            "filing_count": count,
            "pct_of_filings": round((count / total) * 100.0, 6) if total else 0.0,
        }
        for symbol, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    ]


def _markdown_summary(summary: dict[str, Any]) -> str:
    gate = summary.get("filing_catalyst_tier_gate", {})
    lines = [
        "# Filing Catalyst Tier Audit",
        "",
        f"- Filing count: {summary.get('filing_count', 0)}",
        f"- Symbol count: {summary.get('symbol_count', 0)}",
        f"- Gate: `{gate.get('status')}`",
        f"- Decision grade: `{summary.get('metadata', {}).get('decision_grade')}`",
        f"- Cost grade: `{summary.get('metadata', {}).get('cost_grade')}`",
        "",
        "## Tier Counts",
        "",
    ]
    for tier, count in summary.get("tier_counts", {}).items():
        lines.append(f"- {tier}: {count}")
    lines.extend(["", "## Gate Reasons", ""])
    for reason in gate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- This is catalyst context inventory only.",
            "- Trap rows should block naive long setup promotion until separately validated.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    suffix = 2
    while True:
        candidate = resolved.with_name(f"{resolved.name}_{suffix}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
        suffix += 1
