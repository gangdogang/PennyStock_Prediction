from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import create_snapshot_run, get_connection, insert_filings
from ..models import FilingMatch
from ..providers.sec import SecDataProvider
from .paper_runtime import write_csv

EASTERN = ZoneInfo("America/New_York")
DEFAULT_CUTOFF_TIME = time(8, 0)
DEFAULT_FORMS = ("8-K", "S-1", "S-3", "424B5", "RW")


class SecEdgarProvider(Protocol):
    def fetch_company_tickers(self) -> dict[str, Any]:
        ...

    def fetch_submissions(self, cik: str) -> dict[str, Any]:
        ...

    def build_filing_url(
        self,
        cik: str,
        accession_number: str,
        primary_document: str | None,
    ) -> str:
        ...


@dataclass(frozen=True, slots=True)
class SecEdgarPitBackfillOptions:
    db_path: Path
    start_date: date | str
    end_date: date | str | None = None
    output_root: Path = Path("data/backtest_lab/research_runs")
    run_id: str | None = None
    symbols: tuple[str, ...] = ()
    ciks: tuple[str, ...] = ()
    forms: tuple[str, ...] = DEFAULT_FORMS
    scan_id: str | None = None
    write_database: bool = True
    source: str = "sec_edgar_pit_backfill"
    point_in_time_tag: str = "sec_edgar_pit_backfill"
    cutoff_time: time = DEFAULT_CUTOFF_TIME


@dataclass(frozen=True, slots=True)
class SecEdgarPitBackfillResult:
    export_dir: Path
    manifest_path: Path
    report_path: Path
    csv_path: Path
    summary_path: Path
    scan_ids_by_market_date: dict[str, str]
    parsed_count: int
    eligible_count: int
    written_count: int
    diagnostic_count: int


@dataclass(frozen=True, slots=True)
class _CompanyRef:
    symbol: str
    cik: str
    title: str


@dataclass(frozen=True, slots=True)
class _SubmissionRow:
    symbol: str
    cik: str
    title: str
    form: str
    filing_date: str
    acceptance_datetime: str | None
    accession_number: str
    primary_document: str | None
    primary_doc_description: str | None
    report_date: str | None


class SecEdgarPitBackfillService:
    """Backfill SEC recent filings with point-in-time market-date cutoff semantics."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        provider: SecEdgarProvider | None = None,
    ) -> None:
        self.settings = settings or AppSettings()
        self.provider = provider or SecDataProvider(
            company_tickers_url=self.settings.sec_company_tickers_url,
            submissions_url_template=self.settings.sec_submissions_url_template,
            filing_url_template=self.settings.sec_archive_filing_url_template,
            user_agent=self.settings.sec_user_agent,
        )

    def run(self, options: SecEdgarPitBackfillOptions) -> SecEdgarPitBackfillResult:
        start_date = _coerce_date(options.start_date)
        end_date = _coerce_date(options.end_date or options.start_date)
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if options.scan_id and start_date != end_date:
            raise ValueError("A supplied scan_id can only be used for a single market date")

        run_id = options.run_id or datetime.now(timezone.utc).strftime(
            "sec_edgar_pit_%Y%m%d_%H%M%S"
        )
        export_dir = _unique_export_dir(options.output_root / run_id)
        export_dir.mkdir(parents=True, exist_ok=True)

        companies = self._selected_companies(options)
        records = self._collect_records(
            companies,
            start_date=start_date,
            end_date=end_date,
            forms={_normalize_form(form) for form in options.forms},
            cutoff_time=options.cutoff_time,
        )
        scan_ids: dict[str, str] = {}
        written_count = 0
        if options.write_database:
            scan_ids, written_count = self._write_database(
                options,
                records,
                start_date=start_date,
            )

        summary = self._summary(records, written_count=written_count)
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "db_path": str(options.db_path),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "symbols": sorted({_normalize_symbol(symbol) for symbol in options.symbols}),
            "ciks": sorted({_normalize_cik(cik) for cik in options.ciks}),
            "forms": sorted({_normalize_form(form) for form in options.forms}),
            "source": options.source,
            "point_in_time_tag": options.point_in_time_tag,
            "cutoff_time_et": options.cutoff_time.strftime("%H:%M:%S"),
            "scan_ids_by_market_date": scan_ids,
        }
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "interpretation": (
                "eligible_for_market_date is derived from acceptanceDateTime and the "
                "08:00 ET cutoff. Filings accepted after the cutoff are diagnostics for "
                "their acceptance date and become eligible only for the next date."
            ),
            "manifest": manifest,
            "summary": summary,
            "records": records,
        }

        manifest_path = export_dir / "run_manifest.json"
        report_path = export_dir / "sec_edgar_pit_report.json"
        csv_path = export_dir / "sec_edgar_pit_filings.csv"
        summary_path = export_dir / "sec_edgar_pit_summary.md"
        _write_json(manifest_path, manifest)
        _write_json(report_path, report)
        write_csv(csv_path, records)
        summary_path.write_text(self._markdown_summary(report), encoding="utf-8")

        return SecEdgarPitBackfillResult(
            export_dir=export_dir,
            manifest_path=manifest_path,
            report_path=report_path,
            csv_path=csv_path,
            summary_path=summary_path,
            scan_ids_by_market_date=scan_ids,
            parsed_count=int(summary["parsed_count"]),
            eligible_count=int(summary["eligible_count"]),
            written_count=written_count,
            diagnostic_count=int(summary["diagnostic_count"]),
        )

    def _selected_companies(self, options: SecEdgarPitBackfillOptions) -> list[_CompanyRef]:
        ticker_payload = self.provider.fetch_company_tickers()
        companies_by_symbol = _normalize_company_tickers(ticker_payload)
        symbol_filter = {_normalize_symbol(symbol) for symbol in options.symbols}
        cik_filter = {_normalize_cik(cik) for cik in options.ciks}

        companies = list(companies_by_symbol.values())
        if symbol_filter:
            companies = [company for company in companies if company.symbol in symbol_filter]
        if cik_filter:
            companies = [company for company in companies if company.cik in cik_filter]
        if not symbol_filter and cik_filter:
            known_ciks = {company.cik for company in companies}
            for cik in sorted(cik_filter - known_ciks):
                companies.append(_CompanyRef(symbol=f"CIK{cik}", cik=cik, title=""))
        companies.sort(key=lambda company: (company.symbol, company.cik))
        return companies

    def _collect_records(
        self,
        companies: list[_CompanyRef],
        *,
        start_date: date,
        end_date: date,
        forms: set[str],
        cutoff_time: time,
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for company in companies:
            try:
                submissions = self.provider.fetch_submissions(company.cik)
            except Exception as exc:
                records.append(
                    self._diagnostic_fetch_error(
                        company,
                        start_date=start_date,
                        end_date=end_date,
                        error=exc,
                    )
                )
                continue
            recent = submissions.get("filings", {}).get("recent", {})
            for row in _iter_submission_rows(company, recent):
                if forms and row.form not in forms:
                    continue
                record = self._record_from_submission_row(
                    row,
                    start_date=start_date,
                    end_date=end_date,
                    cutoff_time=cutoff_time,
                )
                if bool(record["include_in_report"]):
                    records.append(record)
        records.sort(
            key=lambda item: (
                str(item.get("eligible_for_market_date") or "9999-99-99"),
                str(item.get("acceptance_datetime") or ""),
                str(item.get("symbol") or ""),
                str(item.get("accession_number") or ""),
            )
        )
        return records

    def _record_from_submission_row(
        self,
        row: _SubmissionRow,
        *,
        start_date: date,
        end_date: date,
        cutoff_time: time,
    ) -> dict[str, object]:
        accepted_at = _parse_acceptance_datetime(row.acceptance_datetime)
        filing_date = _parse_date_or_none(row.filing_date)
        if accepted_at is None:
            include = filing_date is not None and start_date <= filing_date <= end_date
            return {
                **self._base_record(row),
                "acceptance_datetime_utc": None,
                "acceptance_datetime_et": None,
                "acceptance_market_date": filing_date.isoformat() if filing_date else None,
                "cutoff_at_et": None,
                "eligible_for_market_date": None,
                "diagnostic_for_market_date": filing_date.isoformat() if filing_date else None,
                "cutoff_decision": "missing_or_unparseable_acceptance_datetime",
                "eligible_in_requested_window": False,
                "written_to_db": False,
                "include_in_report": include,
            }

        accepted_at_utc = accepted_at.astimezone(UTC)
        accepted_at_et = accepted_at.astimezone(EASTERN)
        acceptance_market_date = accepted_at_et.date()
        cutoff_at_et = datetime.combine(acceptance_market_date, cutoff_time, tzinfo=EASTERN)
        after_cutoff = accepted_at_et > cutoff_at_et
        eligible_market_date = (
            acceptance_market_date + timedelta(days=1)
            if after_cutoff
            else acceptance_market_date
        )
        eligible_in_window = start_date <= eligible_market_date <= end_date
        diagnostic_in_window = after_cutoff and start_date <= acceptance_market_date <= end_date
        include = eligible_in_window or diagnostic_in_window
        return {
            **self._base_record(row),
            "acceptance_datetime_utc": accepted_at_utc.isoformat(),
            "acceptance_datetime_et": accepted_at_et.isoformat(),
            "acceptance_market_date": acceptance_market_date.isoformat(),
            "cutoff_at_et": cutoff_at_et.isoformat(),
            "eligible_for_market_date": eligible_market_date.isoformat(),
            "diagnostic_for_market_date": (
                acceptance_market_date.isoformat() if after_cutoff else None
            ),
            "cutoff_decision": (
                "after_cutoff_next_date" if after_cutoff else "eligible_same_date"
            ),
            "eligible_in_requested_window": eligible_in_window,
            "written_to_db": False,
            "include_in_report": include,
        }

    def _base_record(self, row: _SubmissionRow) -> dict[str, object]:
        filing_url = self.provider.build_filing_url(
            row.cik,
            row.accession_number,
            row.primary_document,
        )
        return {
            "symbol": row.symbol,
            "cik": row.cik,
            "company_name": row.title,
            "form": row.form,
            "filing_date": row.filing_date,
            "report_date": row.report_date,
            "acceptance_datetime": row.acceptance_datetime,
            "accession_number": row.accession_number,
            "primary_document": row.primary_document,
            "primary_doc_description": row.primary_doc_description,
            "filing_url": filing_url,
        }

    def _diagnostic_fetch_error(
        self,
        company: _CompanyRef,
        *,
        start_date: date,
        end_date: date,
        error: Exception,
    ) -> dict[str, object]:
        return {
            "symbol": company.symbol,
            "cik": company.cik,
            "company_name": company.title,
            "form": None,
            "filing_date": None,
            "report_date": None,
            "acceptance_datetime": None,
            "acceptance_datetime_utc": None,
            "acceptance_datetime_et": None,
            "acceptance_market_date": None,
            "cutoff_at_et": None,
            "eligible_for_market_date": None,
            "diagnostic_for_market_date": None,
            "accession_number": None,
            "primary_document": None,
            "primary_doc_description": None,
            "filing_url": None,
            "cutoff_decision": "fetch_submissions_error",
            "eligible_in_requested_window": False,
            "written_to_db": False,
            "include_in_report": True,
            "error": f"{type(error).__name__}: {error}",
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
        }

    def _write_database(
        self,
        options: SecEdgarPitBackfillOptions,
        records: list[dict[str, object]],
        *,
        start_date: date,
    ) -> tuple[dict[str, str], int]:
        eligible_records = [
            record
            for record in records
            if bool(record.get("eligible_in_requested_window"))
            and record.get("eligible_for_market_date") is not None
        ]
        grouped: dict[str, list[dict[str, object]]] = {}
        for record in eligible_records:
            grouped.setdefault(str(record["eligible_for_market_date"]), []).append(record)

        scan_ids: dict[str, str] = {}
        if options.scan_id:
            market_date = start_date.isoformat()
            self._ensure_supplied_scan(
                options.db_path,
                scan_id=options.scan_id,
                market_date=market_date,
                source=options.source,
                point_in_time_tag=options.point_in_time_tag,
                symbol_count=len(
                    {str(row["symbol"]) for row in grouped.get(market_date, [])}
                ),
            )
            scan_ids[market_date] = options.scan_id
        else:
            for market_date, market_records in grouped.items():
                snapshot = create_snapshot_run(
                    options.db_path,
                    source=options.source,
                    symbol_count=len({str(row["symbol"]) for row in market_records}),
                    market_date=market_date,
                    snapshot_role="point_in_time",
                    point_in_time_tag=options.point_in_time_tag,
                )
                scan_ids[market_date] = snapshot.snapshot_id

        written_count = 0
        for market_date, scan_id in scan_ids.items():
            market_records = grouped.get(market_date, [])
            self._replace_filings(options.db_path, scan_id, market_records)
            written_count += len(market_records)
            for record in market_records:
                record["written_to_db"] = True
                record["scan_id"] = scan_id
        if not grouped and options.scan_id:
            self._replace_filings(options.db_path, options.scan_id, [])
        return scan_ids, written_count

    def _ensure_supplied_scan(
        self,
        db_path: Path,
        *,
        scan_id: str,
        market_date: str,
        source: str,
        point_in_time_tag: str,
        symbol_count: int,
    ) -> None:
        with get_connection(db_path) as connection:
            row = connection.execute(
                """
                SELECT scan_id, market_date, snapshot_role
                FROM scan_runs
                WHERE scan_id = ?
                """,
                (scan_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO scan_runs (
                        scan_id,
                        source,
                        symbol_count,
                        market_date,
                        snapshot_role,
                        point_in_time_tag,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, 'point_in_time', ?, ?)
                    """,
                    (
                        scan_id,
                        source,
                        symbol_count,
                        market_date,
                        point_in_time_tag,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return
            if str(row["snapshot_role"]) != "point_in_time":
                raise ValueError(f"scan_id={scan_id} is not a point_in_time scan")
            existing_market_date = row["market_date"]
            if existing_market_date is not None and str(existing_market_date) != market_date:
                raise ValueError(
                    f"scan_id={scan_id} market_date={existing_market_date} "
                    f"does not match requested market_date={market_date}"
                )

    def _replace_filings(
        self,
        db_path: Path,
        scan_id: str,
        records: list[dict[str, object]],
    ) -> None:
        with get_connection(db_path) as connection:
            connection.execute("DELETE FROM filings WHERE scan_id = ?", (scan_id,))
        filings = [self._filing_match_from_record(record) for record in records]
        if filings:
            insert_filings(db_path, scan_id, filings)

    def _filing_match_from_record(self, record: dict[str, object]) -> FilingMatch:
        summary = self._filing_summary(record)
        return FilingMatch(
            symbol=str(record["symbol"]),
            cik=str(record["cik"]),
            form=str(record["form"]),
            filing_date=str(record["filing_date"]),
            acceptance_datetime=str(record["acceptance_datetime"])
            if record.get("acceptance_datetime")
            else None,
            accession_number=str(record["accession_number"]),
            primary_document=str(record["primary_document"])
            if record.get("primary_document")
            else None,
            primary_doc_description=str(record["primary_doc_description"])
            if record.get("primary_doc_description")
            else None,
            filing_url=str(record["filing_url"]),
            matched_keywords=[],
            summary=summary,
        )

    def _filing_summary(self, record: dict[str, object]) -> str:
        parts = [str(record["form"])]
        description = record.get("primary_doc_description")
        if description:
            parts.append(str(description).strip())
        parts.append(f"eligible_for_market_date={record.get('eligible_for_market_date')}")
        parts.append(f"cutoff_decision={record.get('cutoff_decision')}")
        return " | ".join(parts)

    def _summary(
        self,
        records: list[dict[str, object]],
        *,
        written_count: int,
    ) -> dict[str, object]:
        parsed_records = [record for record in records if record.get("accession_number")]
        eligible = [
            record
            for record in parsed_records
            if record.get("eligible_in_requested_window")
        ]
        diagnostic = [
            record
            for record in parsed_records
            if record.get("cutoff_decision") == "after_cutoff_next_date"
            and record.get("diagnostic_for_market_date") is not None
        ]
        by_form: dict[str, int] = {}
        by_market_date: dict[str, int] = {}
        for record in eligible:
            form = str(record["form"])
            market_date = str(record["eligible_for_market_date"])
            by_form[form] = by_form.get(form, 0) + 1
            by_market_date[market_date] = by_market_date.get(market_date, 0) + 1
        return {
            "parsed_count": len(parsed_records),
            "eligible_count": len(eligible),
            "diagnostic_count": len(diagnostic),
            "written_count": written_count,
            "by_form": dict(sorted(by_form.items())),
            "by_eligible_market_date": dict(sorted(by_market_date.items())),
        }

    def _markdown_summary(self, report: dict[str, object]) -> str:
        summary = report["summary"]
        assert isinstance(summary, dict)
        manifest = report["manifest"]
        assert isinstance(manifest, dict)
        lines = [
            "# SEC EDGAR PIT Filing Backfill",
            "",
            f"- Date range: `{manifest['start_date']}` to `{manifest['end_date']}`",
            f"- Parsed filings: {summary['parsed_count']}",
            f"- Eligible filings: {summary['eligible_count']}",
            f"- Diagnostic after-cutoff filings: {summary['diagnostic_count']}",
            f"- Written filings: {summary['written_count']}",
            f"- PIT scan ids: {json.dumps(manifest['scan_ids_by_market_date'], sort_keys=True)}",
            "",
            "## Cutoff Rule",
            "- `acceptanceDateTime <= D 08:00 ET` is eligible for market date D.",
            "- Filings accepted after D 08:00 ET are diagnostics for D and eligible for the next date.",
        ]
        return "\n".join(lines) + "\n"


def _normalize_company_tickers(payload: dict[str, Any]) -> dict[str, _CompanyRef]:
    companies: dict[str, _CompanyRef] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        symbol = _normalize_symbol(str(value.get("ticker") or key))
        cik_value = value.get("cik") or value.get("cik_str")
        if not symbol or cik_value is None:
            continue
        cik = _normalize_cik(cik_value)
        title = str(value.get("title") or value.get("company_name") or "").strip()
        companies[symbol] = _CompanyRef(symbol=symbol, cik=cik, title=title)
    return companies


def _iter_submission_rows(company: _CompanyRef, recent: dict[str, Any]) -> list[_SubmissionRow]:
    forms = _list_value(recent.get("form"))
    total = len(forms)
    rows: list[_SubmissionRow] = []
    for index in range(total):
        accession = _at(recent, "accessionNumber", index)
        if not accession:
            continue
        form = _normalize_form(str(forms[index]))
        rows.append(
            _SubmissionRow(
                symbol=company.symbol,
                cik=company.cik,
                title=company.title,
                form=form,
                filing_date=str(_at(recent, "filingDate", index) or ""),
                acceptance_datetime=_optional_str(_at(recent, "acceptanceDateTime", index)),
                accession_number=str(accession),
                primary_document=_optional_str(_at(recent, "primaryDocument", index)),
                primary_doc_description=_optional_str(
                    _at(recent, "primaryDocDescription", index)
                ),
                report_date=_optional_str(_at(recent, "reportDate", index)),
            )
        )
    return rows


def _at(recent: dict[str, Any], key: str, index: int) -> object | None:
    values = _list_value(recent.get(key))
    if index >= len(values):
        return None
    return values[index]


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_acceptance_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 14 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
            return parsed.replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_date_or_none(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _coerce_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _normalize_symbol(value: object) -> str:
    return str(value).strip().upper()


def _normalize_cik(value: object) -> str:
    text = str(value).strip()
    if text.isdigit():
        return text.zfill(10)
    return text.upper().removeprefix("CIK").zfill(10)


def _normalize_form(value: object) -> str:
    return str(value).strip().upper()


def _unique_export_dir(requested_dir: Path) -> Path:
    resolved = requested_dir.resolve()
    if not resolved.exists() or not any(resolved.iterdir()):
        return resolved
    for index in range(2, 1000):
        candidate = resolved.with_name(f"{resolved.name}_rerun_{index}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise OSError(f"Could not allocate a unique export directory for {requested_dir}")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
