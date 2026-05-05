from __future__ import annotations

import json
from pathlib import Path

from penny_stock_radar.db import get_connection, init_database
from penny_stock_radar.services.sec_edgar_pit import (
    SecEdgarPitBackfillOptions,
    SecEdgarPitBackfillService,
)


class FakeSecEdgarProvider:
    def __init__(self) -> None:
        self.requested_ciks: list[str] = []

    def fetch_company_tickers(self):
        return {
            "0": {
                "cik_str": 123456,
                "ticker": "ABCD",
                "title": "ABCD Corp",
            },
            "1": {
                "cik_str": 222222,
                "ticker": "WXYZ",
                "title": "WXYZ Corp",
            },
        }

    def fetch_submissions(self, cik: str):
        self.requested_ciks.append(cik)
        if cik == "0000123456":
            return {
                "filings": {
                    "recent": {
                        "form": ["8-K", "S-1", "10-Q", "8-K"],
                        "filingDate": [
                            "2026-04-16",
                            "2026-04-16",
                            "2026-04-16",
                            "2026-04-16",
                        ],
                        "acceptanceDateTime": [
                            "2026-04-16T11:59:59Z",
                            "2026-04-16T12:00:00Z",
                            "2026-04-16T11:50:00Z",
                            "2026-04-16T12:00:01Z",
                        ],
                        "accessionNumber": [
                            "0000123456-26-000001",
                            "0000123456-26-000002",
                            "0000123456-26-000003",
                            "0000123456-26-000004",
                        ],
                        "primaryDocument": [
                            "pre8k.htm",
                            "cutoffs1.htm",
                            "quarterly.htm",
                            "post8k.htm",
                        ],
                        "primaryDocDescription": [
                            "Current report",
                            "Registration statement",
                            "Quarterly report",
                            "After cutoff report",
                        ],
                        "reportDate": [
                            "2026-04-16",
                            "2026-04-16",
                            "2026-04-16",
                            "2026-04-16",
                        ],
                    }
                }
            }
        return {
            "filings": {
                "recent": {
                    "form": ["8-K", "S-1"],
                    "filingDate": ["2026-04-16", "2026-04-16"],
                    "acceptanceDateTime": [
                        "2026-04-16T11:45:00Z",
                        "2026-04-16T11:46:00Z",
                    ],
                    "accessionNumber": [
                        "0000222222-26-000001",
                        "0000222222-26-000002",
                    ],
                    "primaryDocument": ["wxyz8k.htm", "wxyzs1.htm"],
                    "primaryDocDescription": [
                        "Current report",
                        "Registration statement",
                    ],
                }
            }
        }

    def build_filing_url(
        self,
        cik: str,
        accession_number: str,
        primary_document: str | None,
    ):
        return f"https://example.test/{cik}/{accession_number}/{primary_document}"


def test_sec_edgar_pit_backfill_writes_only_cutoff_eligible_filings_to_supplied_scan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    provider = FakeSecEdgarProvider()
    result = SecEdgarPitBackfillService(provider=provider).run(
        SecEdgarPitBackfillOptions(
            db_path=db_path,
            output_root=tmp_path / "research_runs",
            run_id="sec_fixture",
            start_date="2026-04-16",
            end_date="2026-04-16",
            symbols=("ABCD",),
            forms=("8-K", "S-1"),
            scan_id="pit-sec-2026-04-16",
        )
    )

    assert result.parsed_count == 3
    assert result.eligible_count == 2
    assert result.written_count == 2
    assert result.diagnostic_count == 1
    assert result.scan_ids_by_market_date == {
        "2026-04-16": "pit-sec-2026-04-16",
    }

    with get_connection(db_path) as connection:
        scan = connection.execute(
            """
            SELECT scan_id, market_date, snapshot_role, point_in_time_tag
            FROM scan_runs
            WHERE scan_id = ?
            """,
            ("pit-sec-2026-04-16",),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT accession_number, form, summary
            FROM filings
            WHERE scan_id = ?
            ORDER BY accession_number
            """,
            ("pit-sec-2026-04-16",),
        ).fetchall()

    assert scan["market_date"] == "2026-04-16"
    assert scan["snapshot_role"] == "point_in_time"
    assert scan["point_in_time_tag"] == "sec_edgar_pit_backfill"
    assert [row["accession_number"] for row in rows] == [
        "0000123456-26-000001",
        "0000123456-26-000002",
    ]
    assert "0000123456-26-000004" not in {row["accession_number"] for row in rows}
    assert all("eligible_for_market_date=2026-04-16" in row["summary"] for row in rows)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    diagnostic = next(
        row
        for row in report["records"]
        if row["accession_number"] == "0000123456-26-000004"
    )
    assert diagnostic["cutoff_decision"] == "after_cutoff_next_date"
    assert diagnostic["diagnostic_for_market_date"] == "2026-04-16"
    assert diagnostic["eligible_for_market_date"] == "2026-04-17"
    assert diagnostic["eligible_in_requested_window"] is False
    assert diagnostic["written_to_db"] is False
    assert result.csv_path.exists()
    assert "Diagnostic after-cutoff filings: 1" in result.summary_path.read_text(
        encoding="utf-8"
    )


def test_sec_edgar_pit_backfill_moves_after_cutoff_filing_to_next_date_scan(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)

    result = SecEdgarPitBackfillService(provider=FakeSecEdgarProvider()).run(
        SecEdgarPitBackfillOptions(
            db_path=db_path,
            output_root=tmp_path / "research_runs",
            run_id="sec_range",
            start_date="2026-04-16",
            end_date="2026-04-17",
            symbols=("ABCD",),
            forms=("8-K",),
        )
    )

    assert set(result.scan_ids_by_market_date) == {"2026-04-16", "2026-04-17"}
    assert result.written_count == 2

    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT f.accession_number, s.market_date, s.snapshot_role
            FROM filings f
            JOIN scan_runs s ON s.scan_id = f.scan_id
            ORDER BY f.accession_number
            """
        ).fetchall()

    assert [(row["accession_number"], row["market_date"]) for row in rows] == [
        ("0000123456-26-000001", "2026-04-16"),
        ("0000123456-26-000004", "2026-04-17"),
    ]
    assert all(row["snapshot_role"] == "point_in_time" for row in rows)


def test_sec_edgar_pit_backfill_filters_by_cik_and_form(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.sqlite3"
    init_database(db_path)
    provider = FakeSecEdgarProvider()

    result = SecEdgarPitBackfillService(provider=provider).run(
        SecEdgarPitBackfillOptions(
            db_path=db_path,
            output_root=tmp_path / "research_runs",
            run_id="sec_cik_filter",
            start_date="2026-04-16",
            end_date="2026-04-16",
            ciks=("222222",),
            forms=("8-K",),
        )
    )

    assert provider.requested_ciks == ["0000222222"]
    assert result.parsed_count == 1
    assert result.written_count == 1

    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT symbol, cik, form, accession_number
            FROM filings
            """
        ).fetchone()

    assert dict(row) == {
        "symbol": "WXYZ",
        "cik": "0000222222",
        "form": "8-K",
        "accession_number": "0000222222-26-000001",
    }
