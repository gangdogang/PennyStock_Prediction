from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from penny_stock_radar.db import get_connection
from penny_stock_radar.services.nasdaq_symbol_directory import (
    NasdaqSymbolDirectoryArchiver,
    parse_nasdaqlisted,
    parse_otherlisted,
    records_to_universe_candidates,
)


NASDAQ_LISTED_FIXTURE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
aapl|Apple Inc. - Common Stock|Q|N|N|100|N|N
TETF|Test ETF Trust|G|Y|N|100|Y|N
File Creation Time: 0506202618:02
"""

OTHER_LISTED_FIXTURE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
brk.b|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B
UNIT.U|Example Acquisition Corp Units|A|UNIT.U|N|100|N|UNIT.U
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
File Creation Time: 0506202618:02
"""


def test_parse_symbol_directory_files_normalizes_records_and_ignores_footer() -> None:
    nasdaq_records = parse_nasdaqlisted(NASDAQ_LISTED_FIXTURE)
    other_records = parse_otherlisted(OTHER_LISTED_FIXTURE)

    assert [record.symbol for record in nasdaq_records] == ["AAPL", "TETF"]
    assert nasdaq_records[0].to_dict() == {
        "source_file": "nasdaqlisted.txt",
        "symbol": "AAPL",
        "security_name": "Apple Inc. - Common Stock",
        "exchange": None,
        "market_category": "Q",
        "test_issue": False,
        "etf": False,
    }
    assert [record.symbol for record in other_records] == ["BRK.B", "UNIT.U", "SPY"]
    assert other_records[0].exchange == "N"
    assert other_records[2].etf is True
    assert not any(
        record.symbol.startswith("FILE CREATION TIME")
        for record in [*nasdaq_records, *other_records]
    )


def test_records_to_universe_candidates_filters_directory_rows_conservatively() -> None:
    records = [*parse_nasdaqlisted(NASDAQ_LISTED_FIXTURE), *parse_otherlisted(OTHER_LISTED_FIXTURE)]

    candidates = records_to_universe_candidates(records)
    rows = {candidate.symbol: candidate for candidate in candidates}

    assert rows["AAPL"].passed_filters is True
    assert rows["AAPL"].exchange == "NASDAQ"
    assert rows["BRK.B"].passed_filters is True
    assert rows["TETF"].passed_filters is False
    assert rows["TETF"].filter_reasons == ["etf", "test_issue"]
    assert rows["SPY"].quote_type == "ETF"
    assert rows["SPY"].filter_reasons == ["etf"]
    assert rows["UNIT.U"].filter_reasons == ["spac_security", "unit_security"]


def test_archiver_fetches_two_files_saves_artifacts_and_report(tmp_path: Path) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/nasdaqlisted.txt"):
            return httpx.Response(200, text=NASDAQ_LISTED_FIXTURE)
        if request.url.path.endswith("/otherlisted.txt"):
            return httpx.Response(200, text=OTHER_LISTED_FIXTURE)
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    archiver = NasdaqSymbolDirectoryArchiver(tmp_path / "archive", client=client)

    result = archiver.fetch_and_archive(now=datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc))

    assert requested_paths == [
        "/dynamic/symdir/nasdaqlisted.txt",
        "/dynamic/symdir/otherlisted.txt",
    ]
    assert result.artifact_paths["nasdaqlisted.txt"].read_text(encoding="utf-8") == (
        NASDAQ_LISTED_FIXTURE
    )
    assert result.artifact_paths["otherlisted.txt"].read_text(encoding="utf-8") == (
        OTHER_LISTED_FIXTURE
    )
    records_payload = json.loads(result.records_path.read_text(encoding="utf-8"))
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert len(records_payload) == 5
    assert report["summary"]["forward_pit_only"] is True
    assert report["summary"]["not_backfilled_past_pit"] is True
    assert report["summary"]["db"]["skipped_reason"] == "db_path_not_provided"


def test_archiver_writes_forward_point_in_time_scan_and_universe_rows(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/nasdaqlisted.txt"):
            return httpx.Response(200, text=NASDAQ_LISTED_FIXTURE)
        if request.url.path.endswith("/otherlisted.txt"):
            return httpx.Response(200, text=OTHER_LISTED_FIXTURE)
        return httpx.Response(404, text="not found")

    db_path = tmp_path / "radar.sqlite3"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    archiver = NasdaqSymbolDirectoryArchiver(tmp_path / "archive", client=client)

    result = archiver.archive_current(
        db_path=db_path,
        allow_current_date=True,
        now=datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
    )

    assert result.market_date == "2026-05-06"
    assert result.summary["db"]["source"] == "nasdaq_symbol_directory"
    assert result.summary["db"]["snapshot_role"] == "point_in_time"
    assert result.summary["db"]["inserted_candidates"] == 5
    assert result.summary["db"]["passed_filters"] == 2
    assert result.summary["forward_pit_only"] is True
    assert result.summary["not_backfilled_past_pit"] is True

    with get_connection(db_path) as connection:
        scan = connection.execute(
            """
            SELECT source, symbol_count, market_date, snapshot_role, point_in_time_tag
            FROM scan_runs
            WHERE scan_id = ?
            """,
            (result.scan_id,),
        ).fetchone()
        universe_rows = connection.execute(
            """
            SELECT symbol, company_name, exchange, quote_type, passed_filters, filter_reasons
            FROM universe
            WHERE scan_id = ?
            ORDER BY symbol
            """,
            (result.scan_id,),
        ).fetchall()

    assert dict(scan) == {
        "source": "nasdaq_symbol_directory",
        "symbol_count": 5,
        "market_date": "2026-05-06",
        "snapshot_role": "point_in_time",
        "point_in_time_tag": "forward_pit_only:not_backfilled_past_pit",
    }
    passed_by_symbol = {row["symbol"]: bool(row["passed_filters"]) for row in universe_rows}
    reasons_by_symbol = {
        row["symbol"]: json.loads(str(row["filter_reasons"])) for row in universe_rows
    }
    assert passed_by_symbol == {
        "AAPL": True,
        "BRK.B": True,
        "SPY": False,
        "TETF": False,
        "UNIT.U": False,
    }
    assert reasons_by_symbol["TETF"] == ["etf", "test_issue"]
    assert reasons_by_symbol["UNIT.U"] == ["spac_security", "unit_security"]


def test_archiver_requires_explicit_current_date_permission_for_db_write(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/nasdaqlisted.txt"):
            return httpx.Response(200, text=NASDAQ_LISTED_FIXTURE)
        if request.url.path.endswith("/otherlisted.txt"):
            return httpx.Response(200, text=OTHER_LISTED_FIXTURE)
        return httpx.Response(404, text="not found")

    db_path = tmp_path / "radar.sqlite3"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    archiver = NasdaqSymbolDirectoryArchiver(tmp_path / "archive", client=client)

    result = archiver.archive_current(db_path=db_path)

    assert result.scan_id is None
    assert result.summary["db"] == {
        "enabled": False,
        "skipped_reason": "allow_current_date_required",
    }
    assert not db_path.exists()
