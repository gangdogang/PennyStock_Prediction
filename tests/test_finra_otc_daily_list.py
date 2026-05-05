from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from penny_stock_radar.services.finra_otc_daily_list import (
    EXPECTED_INPUT_CSV_FORMAT,
    FinraOTCDailyListImporter,
    parse_finra_otc_daily_list_csv,
    parse_finra_otc_daily_list_json,
    records_to_corporate_actions_hook,
    stage_finra_otc_daily_list_rows,
)


DIVIDEND_ROW = {
    "changeRoundLotQuantityFlag": "N",
    "paymentMethodCode": None,
    "bankruptcyFlag": "N",
    "changeSecurityDescriptionFlag": "N",
    "newMaturityExpirationDate": None,
    "dividendTypeCode": "CD",
    "newSecurityDescription": "Solvay Bank Corporation (Solvay, NY) Common Stock",
    "oldSymbolCode": "SOBS",
    "newClassText": None,
    "exDate": "2018-12-28 00:00:00.0",
    "reverseSplitRate": None,
    "forwardSplitRate": None,
    "dailyListDatetime": "2018-12-18 16:30:49.0",
    "dailyListEventCode": "DA",
    "newSymbolCode": "SOBS",
    "cashAmountText": "0.34",
    "OTCDailyListID": 139933,
    "changeSymbolFlag": "N",
    "commentText": None,
    "securityDeleteFlag": "N",
    "stockPercentage": None,
    "recordDate": "2018-12-31 00:00:00.0",
    "changeFinancialStatusFlag": "N",
    "securityAddFlag": "N",
    "dailyListReasonDescription": "Cash Dividend Regular",
    "declarationDate": "2018-12-18 00:00:00.0",
    "calendarDay": "2018-12-18",
    "subjectCorporateActionCode": None,
    "oldSecurityDescription": "Solvay Bank Corporation (Solvay, NY) Common Stock",
    "oldMarketCategoryCode": "u",
    "newMarketCategoryCode": "u",
    "dividendTypeDescription": "Cash Dividend",
    "paymentDate": "2019-01-25 00:00:00.0",
}


def test_parse_json_rows_normalizes_daily_list_actions() -> None:
    rows = [
        DIVIDEND_ROW,
        {
            "OTCDailyListID": 200001,
            "calendarDay": "2026-05-01",
            "dailyListDatetime": "2026-05-01 12:30:00.0",
            "dailyListEventCode": "SC",
            "dailyListReasonDescription": "Symbol and Name Change",
            "oldSymbolCode": "oldx",
            "newSymbolCode": "newx",
            "oldSecurityDescription": "Old X Corp Common Stock",
            "newSecurityDescription": "New X Holdings Common Stock",
            "changeSymbolFlag": "Y",
            "changeSecurityDescriptionFlag": "Y",
            "securityDeleteFlag": "N",
            "securityAddFlag": "N",
        },
        {
            "OTCDailyListID": 200002,
            "calendarDay": "2026-05-02",
            "dailyListDatetime": "2026-05-02 09:00:00.0",
            "dailyListEventCode": "DL",
            "dailyListReasonDescription": "Deletion",
            "oldSymbolCode": "DELD",
            "oldSecurityDescription": "Deleted Corp Common Stock",
            "securityDeleteFlag": "Y",
        },
        {
            "OTCDailyListID": 200003,
            "calendarDay": "2026-05-03",
            "dailyListReasonDescription": "Reverse Split",
            "oldSymbolCode": "RSPL",
            "newSymbolCode": "RSPL",
            "oldSecurityDescription": "Reverse Split Corp",
            "newSecurityDescription": "Reverse Split Corp",
            "reverseSplitRate": "1:10",
            "exDate": "2026-05-04 00:00:00.0",
        },
        {
            "OTCDailyListID": 200004,
            "calendarDay": "2026-05-04",
            "dailyListReasonDescription": "Round lot update",
            "oldSymbolCode": "RLOT",
            "newSymbolCode": "RLOT",
            "changeRoundLotQuantityFlag": "Y",
            "oldRoundLotQuantity": 100,
            "newRoundLotQuantity": 10,
        },
    ]

    records = parse_finra_otc_daily_list_json(rows)
    by_category = {record.action_category: record for record in records}

    assert [record.action_category for record in records] == [
        "dividend",
        "symbol_change",
        "name_change",
        "deleted",
        "split",
        "corporate_action",
    ]
    assert by_category["dividend"].source_record_id == "139933"
    assert by_category["dividend"].effective_date == "2018-12-28"
    assert by_category["dividend"].cash_amount == "0.34"
    assert by_category["dividend"].dividend_type_description == "Cash Dividend"
    assert by_category["symbol_change"].old_symbol == "OLDX"
    assert by_category["symbol_change"].new_symbol == "NEWX"
    assert by_category["name_change"].old_name == "Old X Corp Common Stock"
    assert by_category["name_change"].new_name == "New X Holdings Common Stock"
    assert by_category["deleted"].symbol == "DELD"
    assert by_category["split"].action_subtype == "reverse_split"
    assert by_category["split"].split_ratio == "1:10"
    assert by_category["corporate_action"].action_subtype == "round_lot_change"

    hook_rows = records_to_corporate_actions_hook(records)
    assert hook_rows[1]["action_category"] == "symbol_change"
    assert hook_rows[1]["source_record_id"] == "200001"
    assert hook_rows[1]["raw_row"]["oldSymbolCode"] == "oldx"


def test_parse_csv_accepts_official_fields_and_legacy_aliases() -> None:
    official_rows = [
        {
            "OTCDailyListID": "300001",
            "calendarDay": "2026-05-02",
            "dailyListDatetime": "2026-05-02 08:00:00.0",
            "dailyListEventCode": "CA",
            "dailyListReasonDescription": "Forward Split",
            "oldSymbolCode": "FSPL",
            "newSymbolCode": "FSPL",
            "oldSecurityDescription": "Forward Split Inc",
            "newSecurityDescription": "Forward Split Inc",
            "changeSymbolFlag": "N",
            "changeSecurityDescriptionFlag": "N",
            "securityDeleteFlag": "N",
            "securityAddFlag": "N",
            "forwardSplitRate": "2:1",
            "exDate": "2026-05-05 00:00:00.0",
        }
    ]
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=list(official_rows[0].keys()))
    writer.writeheader()
    writer.writerows(official_rows)

    official_records = parse_finra_otc_daily_list_csv(handle.getvalue())
    legacy_records = parse_finra_otc_daily_list_csv(
        "DailyListDate,Type,NewSymbol,OldSymbol,NewName,OldName,EffDate,Comments\n"
        "05/02/2026,Deleted,,LEGD,,Legacy Delete Corp,05/05/2026,Deleted issue\n"
    )

    assert len(official_records) == 1
    assert official_records[0].action_category == "split"
    assert official_records[0].action_subtype == "forward_split"
    assert official_records[0].effective_date == "2026-05-05"
    assert len(legacy_records) == 1
    assert legacy_records[0].action_category == "deleted"
    assert legacy_records[0].symbol == "LEGD"
    assert legacy_records[0].effective_date == "2026-05-05"


def test_stage_rows_writes_json_csv_and_markdown_artifacts(tmp_path: Path) -> None:
    result = stage_finra_otc_daily_list_rows(
        [DIVIDEND_ROW],
        tmp_path / "staging",
        now=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
    )

    payload = json.loads(result.records_json_path.read_text(encoding="utf-8"))
    csv_text = result.records_csv_path.read_text(encoding="utf-8")
    markdown = result.markdown_path.read_text(encoding="utf-8")
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))

    assert result.status == "READY"
    assert payload[0]["action_category"] == "dividend"
    assert "raw_row" in csv_text
    assert "Cash Dividend Regular" in csv_text
    assert "- dividend: 1" in markdown
    assert summary["db_integration"]["status"] == "hook_only"


def test_fetch_non_200_writes_blocked_report_with_expected_csv_format(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, text="Forbidden")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    importer = FinraOTCDailyListImporter(tmp_path / "blocked", client=client)

    result = importer.fetch_and_stage(
        limit=25,
        now=datetime(2026, 5, 6, 12, 30, tzinfo=timezone.utc),
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    records_payload = json.loads(result.records_json_path.read_text(encoding="utf-8"))
    csv_text = result.records_csv_path.read_text(encoding="utf-8")
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert result.blocked is True
    assert str(requests[0].url) == (
        "https://api.finra.org/data/group/otcMarket/name/OTCDAILYLIST?limit=25"
    )
    assert requests[0].headers["accept"] == "application/json"
    assert summary["status"] == "BLOCKED"
    assert summary["blocked"]["reason"] == "http_status_403"
    assert summary["blocked"]["expected_input_csv_format"] == EXPECTED_INPUT_CSV_FORMAT
    assert "OTCDailyListID" in summary["blocked"]["expected_input_csv_format"][
        "preferred_header"
    ]
    assert records_payload == []
    assert csv_text.startswith("source,staging_record_id,source_record_id")
    assert "Status: BLOCKED" in markdown
    assert "OTCDailyListID" in markdown
