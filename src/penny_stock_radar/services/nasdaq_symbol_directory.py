from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..db import create_snapshot_run, init_database, insert_universe_candidates
from ..models import UniverseCandidate
from ..security_filters import listing_filter_reasons

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
SOURCE_NAME = "nasdaq_symbol_directory"
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class NasdaqSymbolDirectoryRecord:
    source_file: str
    symbol: str
    security_name: str
    exchange: str | None = None
    market_category: str | None = None
    test_issue: bool | None = None
    etf: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "symbol": self.symbol,
            "security_name": self.security_name,
            "exchange": self.exchange,
            "market_category": self.market_category,
            "test_issue": self.test_issue,
            "etf": self.etf,
        }


@dataclass(frozen=True, slots=True)
class NasdaqSymbolDirectoryArchiveResult:
    output_dir: Path
    artifact_paths: dict[str, Path]
    records_path: Path
    report_path: Path
    records: list[NasdaqSymbolDirectoryRecord]
    summary: dict[str, Any]
    report: dict[str, Any]
    scan_id: str | None = None
    market_date: str | None = None


class NasdaqSymbolDirectoryArchiver:
    """Archive Nasdaq Trader symbol directory snapshots as forward-only PIT inputs."""

    def __init__(
        self,
        output_dir: Path | str,
        *,
        client: httpx.Client | None = None,
        nasdaqlisted_url: str = NASDAQ_LISTED_URL,
        otherlisted_url: str = OTHER_LISTED_URL,
        timeout: float = 30.0,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.client = client
        self.urls = {
            "nasdaqlisted.txt": nasdaqlisted_url,
            "otherlisted.txt": otherlisted_url,
        }
        self.timeout = timeout

    def archive_current(
        self,
        *,
        db_path: Path | str | None = None,
        allow_current_date: bool = False,
        market_date: date | str | None = None,
        now: datetime | None = None,
    ) -> NasdaqSymbolDirectoryArchiveResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fetched_at = _coerce_now(now)
        payloads = self._fetch_payloads()

        artifact_paths: dict[str, Path] = {}
        records: list[NasdaqSymbolDirectoryRecord] = []
        for source_file, payload_text in payloads.items():
            artifact_path = self.output_dir / source_file
            artifact_path.write_text(payload_text, encoding="utf-8")
            artifact_paths[source_file] = artifact_path
            records.extend(parse_symbol_directory(payload_text, source_file=source_file))

        records_path = self.output_dir / "nasdaq_symbol_directory_records.json"
        _write_json(records_path, [record.to_dict() for record in records])

        scan_id: str | None = None
        market_date_str: str | None = None
        db_summary = self._db_summary_skipped(db_path, allow_current_date)
        if db_path is not None and allow_current_date:
            market_date_str = _coerce_market_date(market_date, now=fetched_at)
            candidates = records_to_universe_candidates(records)
            resolved_db_path = Path(db_path)
            init_database(resolved_db_path)
            snapshot = create_snapshot_run(
                resolved_db_path,
                source=SOURCE_NAME,
                symbol_count=len(candidates),
                market_date=market_date_str,
                snapshot_role="point_in_time",
                point_in_time_tag="forward_pit_only:not_backfilled_past_pit",
            )
            insert_universe_candidates(resolved_db_path, snapshot.snapshot_id, candidates)
            scan_id = snapshot.snapshot_id
            db_summary = {
                "enabled": True,
                "source": SOURCE_NAME,
                "scan_id": scan_id,
                "snapshot_role": "point_in_time",
                "market_date": market_date_str,
                "inserted_candidates": len(candidates),
                "passed_filters": sum(1 for candidate in candidates if candidate.passed_filters),
                "rejected_candidates": sum(
                    1 for candidate in candidates if not candidate.passed_filters
                ),
            }

        summary = self._summary(
            records=records,
            artifact_paths=artifact_paths,
            fetched_at=fetched_at,
            db_summary=db_summary,
        )
        report: dict[str, Any] = {
            "summary": summary,
            "artifact_paths": {
                source_file: str(path) for source_file, path in artifact_paths.items()
            },
            "records_path": str(records_path),
        }
        report_path = self.output_dir / "nasdaq_symbol_directory_report.json"
        _write_json(report_path, report)

        return NasdaqSymbolDirectoryArchiveResult(
            output_dir=self.output_dir,
            artifact_paths=artifact_paths,
            records_path=records_path,
            report_path=report_path,
            records=records,
            summary=summary,
            report=report,
            scan_id=scan_id,
            market_date=market_date_str,
        )

    def fetch_and_archive(
        self,
        *,
        db_path: Path | str | None = None,
        allow_current_date: bool = False,
        market_date: date | str | None = None,
        now: datetime | None = None,
    ) -> NasdaqSymbolDirectoryArchiveResult:
        return self.archive_current(
            db_path=db_path,
            allow_current_date=allow_current_date,
            market_date=market_date,
            now=now,
        )

    def _fetch_payloads(self) -> dict[str, str]:
        if self.client is not None:
            return self._fetch_payloads_with_client(self.client)
        with httpx.Client() as client:
            return self._fetch_payloads_with_client(client)

    def _fetch_payloads_with_client(self, client: httpx.Client) -> dict[str, str]:
        payloads: dict[str, str] = {}
        for source_file, url in self.urls.items():
            response = client.get(
                url,
                follow_redirects=True,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payloads[source_file] = response.text
        return payloads

    def _summary(
        self,
        *,
        records: list[NasdaqSymbolDirectoryRecord],
        artifact_paths: dict[str, Path],
        fetched_at: datetime,
        db_summary: dict[str, Any],
    ) -> dict[str, Any]:
        source_counts: dict[str, int] = {}
        for record in records:
            source_counts[record.source_file] = source_counts.get(record.source_file, 0) + 1
        return {
            "source": SOURCE_NAME,
            "created_at": fetched_at.isoformat(),
            "forward_pit_only": True,
            "not_backfilled_past_pit": True,
            "record_count": len(records),
            "source_files": {
                source_file: {
                    "url": self.urls[source_file],
                    "artifact_path": str(artifact_paths[source_file]),
                    "record_count": source_counts.get(source_file, 0),
                }
                for source_file in self.urls
            },
            "db": db_summary,
            "warnings": [
                "This archive is valid only from its fetch time forward.",
                "It does not backfill historical point-in-time membership.",
                "DB rows are directory-membership candidates, not price/float-enriched penny-stock filters.",
            ],
        }

    def _db_summary_skipped(
        self,
        db_path: Path | str | None,
        allow_current_date: bool,
    ) -> dict[str, Any]:
        if db_path is None:
            reason = "db_path_not_provided"
        elif not allow_current_date:
            reason = "allow_current_date_required"
        else:
            reason = ""
        return {
            "enabled": False,
            "skipped_reason": reason,
        }


def parse_symbol_directory(
    payload_text: str,
    *,
    source_file: str,
) -> list[NasdaqSymbolDirectoryRecord]:
    source_name = Path(source_file).name
    reader = csv.DictReader(io.StringIO(payload_text), delimiter="|")
    records: list[NasdaqSymbolDirectoryRecord] = []
    for row in reader:
        if _is_footer_row(row):
            continue
        symbol = _normalize_symbol(
            _first_present(row, "Symbol", "ACT Symbol", "NASDAQ Symbol")
        )
        if not symbol:
            continue
        security_name = _normalize_text(row.get("Security Name")) or symbol
        records.append(
            NasdaqSymbolDirectoryRecord(
                source_file=source_name,
                symbol=symbol,
                security_name=security_name,
                exchange=_normalize_text(row.get("Exchange")),
                market_category=_normalize_text(row.get("Market Category")),
                test_issue=_parse_yes_no(row.get("Test Issue")),
                etf=_parse_yes_no(row.get("ETF")),
            )
        )
    return records


def parse_nasdaqlisted(payload_text: str) -> list[NasdaqSymbolDirectoryRecord]:
    return parse_symbol_directory(payload_text, source_file="nasdaqlisted.txt")


def parse_otherlisted(payload_text: str) -> list[NasdaqSymbolDirectoryRecord]:
    return parse_symbol_directory(payload_text, source_file="otherlisted.txt")


def records_to_universe_candidates(
    records: list[NasdaqSymbolDirectoryRecord],
) -> list[UniverseCandidate]:
    candidates: list[UniverseCandidate] = []
    seen_symbols: set[str] = set()
    for record in records:
        if record.symbol in seen_symbols:
            continue
        seen_symbols.add(record.symbol)
        reasons = _candidate_filter_reasons(record)
        candidates.append(
            UniverseCandidate(
                symbol=record.symbol,
                company_name=record.security_name,
                exchange=_candidate_exchange(record),
                quote_type="ETF" if record.etf else None,
                passed_filters=not reasons,
                filter_reasons=reasons,
            )
        )
    return candidates


def _candidate_filter_reasons(record: NasdaqSymbolDirectoryRecord) -> list[str]:
    reasons: list[str] = []
    if record.test_issue:
        reasons.append("test_issue")
    if record.etf:
        reasons.append("etf")
    reasons.extend(
        listing_filter_reasons(
            symbol=record.symbol,
            company_name=record.security_name,
            exclude_units=True,
            exclude_preferred=True,
            exclude_warrants=True,
            exclude_rights=True,
            exclude_spacs=True,
        )
    )
    return sorted(set(reasons))


def _candidate_exchange(record: NasdaqSymbolDirectoryRecord) -> str:
    if record.exchange:
        return record.exchange
    if record.source_file.lower() == "nasdaqlisted.txt":
        return "NASDAQ"
    return "UNKNOWN"


def _is_footer_row(row: dict[str | None, Any]) -> bool:
    for value in row.values():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if str(item or "").strip().startswith("File Creation Time"):
                return True
    return False


def _first_present(row: dict[str | None, Any], *keys: str) -> str | None:
    for key in keys:
        value = _normalize_text(row.get(key))
        if value:
            return value
    return None


def _normalize_symbol(value: str | None) -> str:
    return str(value or "").strip().upper()


def _normalize_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _parse_yes_no(value: object) -> bool | None:
    normalized = str(value or "").strip().upper()
    if normalized == "Y":
        return True
    if normalized == "N":
        return False
    return None


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _coerce_market_date(value: date | str | None, *, now: datetime) -> str:
    if value is None:
        return now.astimezone(EASTERN).date().isoformat()
    if isinstance(value, datetime):
        return value.astimezone(EASTERN).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
