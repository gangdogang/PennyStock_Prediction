from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

FINRA_OTC_DAILY_LIST_URL = (
    "https://api.finra.org/data/group/otcMarket/name/OTCDAILYLIST"
)
SOURCE_NAME = "finra_otc_daily_list"

OFFICIAL_FIELDS: tuple[str, ...] = (
    "OTCDailyListID",
    "calendarDay",
    "dailyListDatetime",
    "dailyListEventCode",
    "dailyListReasonDescription",
    "oldSymbolCode",
    "newSymbolCode",
    "oldSecurityDescription",
    "newSecurityDescription",
    "changeSymbolFlag",
    "changeSecurityDescriptionFlag",
    "securityDeleteFlag",
    "securityAddFlag",
    "subjectCorporateActionCode",
    "reverseSplitRate",
    "forwardSplitRate",
    "dividendTypeCode",
    "dividendTypeDescription",
    "cashAmountText",
    "stockPercentage",
    "exDate",
    "recordDate",
    "paymentDate",
    "declarationDate",
    "commentText",
    "oldMarketCategoryCode",
    "newMarketCategoryCode",
    "oldOATSReportableFlag",
    "newOATSReportableFlag",
    "oldRoundLotQuantity",
    "newRoundLotQuantity",
    "changeRoundLotQuantityFlag",
    "bankruptcyFlag",
    "changeOATSReportableFlag",
    "changeRegFeeFlag",
    "changeOTCBBQuoteFlag",
    "changeSecurityAttributeFlag",
    "changeFinancialStatusFlag",
    "oldFinancialStatusCode",
    "newFinancialStatusCode",
    "oldRegFeeFlag",
    "newRegFeeFlag",
    "oldClassText",
    "newClassText",
    "oldMaturityExpirationDate",
    "newMaturityExpirationDate",
    "oldADROrdinaryShareRate",
    "newADROrdnyShareRate",
    "dividendADRFlag",
    "dividendNonADRFlag",
    "dividendMasterID",
    "qualifiedDividendDescription",
    "paymentMethodCode",
    "offeringTypeDescription",
    "ADRWitholdingTaxPercentage",
    "ADRNetRate",
    "ADRGrossRate",
    "ADRFeeAmount",
    "ADRIssuanceFeeAmount",
    "ADRTaxReliefAmount",
)

EXPECTED_INPUT_CSV_FORMAT: dict[str, object] = {
    "encoding": "utf-8",
    "delimiter": ",",
    "preferred_header": ",".join(OFFICIAL_FIELDS),
    "minimum_useful_fields": [
        "OTCDailyListID",
        "calendarDay",
        "dailyListDatetime",
        "dailyListEventCode",
        "dailyListReasonDescription",
        "oldSymbolCode",
        "newSymbolCode",
        "oldSecurityDescription",
        "newSecurityDescription",
        "changeSymbolFlag",
        "changeSecurityDescriptionFlag",
        "securityDeleteFlag",
        "securityAddFlag",
        "subjectCorporateActionCode",
        "reverseSplitRate",
        "forwardSplitRate",
        "dividendTypeCode",
        "dividendTypeDescription",
        "cashAmountText",
        "stockPercentage",
        "exDate",
        "recordDate",
        "paymentDate",
        "declarationDate",
        "commentText",
    ],
    "accepted_legacy_aliases": {
        "DailyListDate": "dailyListDatetime",
        "Type": "dailyListEventCode",
        "NewSymbol": "newSymbolCode",
        "OldSymbol": "oldSymbolCode",
        "NewName": "newSecurityDescription",
        "OldName": "oldSecurityDescription",
        "EffDate": "exDate",
        "Comments": "commentText",
        "Notes": "commentText",
        "Mkt_Cat": "newMarketCategoryCode",
        "OATS_Rptbl_Fl": "newOATSReportableFlag",
    },
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "OTCDailyListID": ("OTCDailyListID", "OTC Daily List ID", "DailyListID"),
    "calendarDay": ("calendarDay", "CalendarDay", "DailyListDate"),
    "dailyListDatetime": ("dailyListDatetime", "DailyListDatetime", "DailyListDate"),
    "dailyListEventCode": ("dailyListEventCode", "DailyListEventCode", "Type"),
    "dailyListReasonDescription": (
        "dailyListReasonDescription",
        "DailyListReasonDescription",
        "Reason",
        "Daily List Reason",
    ),
    "oldSymbolCode": ("oldSymbolCode", "OldSymbolCode", "OldSymbol", "Old Symbol"),
    "newSymbolCode": ("newSymbolCode", "NewSymbolCode", "NewSymbol", "New Symbol"),
    "oldSecurityDescription": (
        "oldSecurityDescription",
        "OldSecurityDescription",
        "OldName",
        "Old Name",
    ),
    "newSecurityDescription": (
        "newSecurityDescription",
        "NewSecurityDescription",
        "NewName",
        "New Name",
    ),
    "exDate": ("exDate", "ExDate", "EffDate", "EffectiveDate"),
    "recordDate": ("recordDate", "RecordDate"),
    "paymentDate": ("paymentDate", "PaymentDate"),
    "declarationDate": ("declarationDate", "DeclarationDate"),
    "commentText": ("commentText", "CommentText", "Comments", "Notes"),
    "newMarketCategoryCode": ("newMarketCategoryCode", "Mkt_Cat", "MarketCategory"),
    "newOATSReportableFlag": ("newOATSReportableFlag", "OATS_Rptbl_Fl"),
}


@dataclass(frozen=True, slots=True)
class FinraOTCDailyListStagingRecord:
    staging_record_id: str
    source_record_id: str | None
    action_category: str
    action_subtype: str
    symbol: str | None
    old_symbol: str | None
    new_symbol: str | None
    old_name: str | None
    new_name: str | None
    effective_date: str | None
    calendar_day: str | None
    daily_list_datetime: str | None
    event_code: str | None
    event_reason: str | None
    ex_date: str | None
    record_date: str | None
    payment_date: str | None
    declaration_date: str | None
    split_ratio: str | None
    cash_amount: str | None
    stock_percentage: str | None
    dividend_type_code: str | None
    dividend_type_description: str | None
    old_market_category: str | None
    new_market_category: str | None
    subject_corporate_action_code: str | None
    comment: str | None
    raw_row: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": SOURCE_NAME,
            "staging_record_id": self.staging_record_id,
            "source_record_id": self.source_record_id,
            "action_category": self.action_category,
            "action_subtype": self.action_subtype,
            "symbol": self.symbol,
            "old_symbol": self.old_symbol,
            "new_symbol": self.new_symbol,
            "old_name": self.old_name,
            "new_name": self.new_name,
            "effective_date": self.effective_date,
            "calendar_day": self.calendar_day,
            "daily_list_datetime": self.daily_list_datetime,
            "event_code": self.event_code,
            "event_reason": self.event_reason,
            "ex_date": self.ex_date,
            "record_date": self.record_date,
            "payment_date": self.payment_date,
            "declaration_date": self.declaration_date,
            "split_ratio": self.split_ratio,
            "cash_amount": self.cash_amount,
            "stock_percentage": self.stock_percentage,
            "dividend_type_code": self.dividend_type_code,
            "dividend_type_description": self.dividend_type_description,
            "old_market_category": self.old_market_category,
            "new_market_category": self.new_market_category,
            "subject_corporate_action_code": self.subject_corporate_action_code,
            "comment": self.comment,
            "raw_row": self.raw_row,
        }

    def to_corporate_action_hook(self) -> dict[str, object]:
        """Return a DB-ready shape without assuming a corporate_actions schema."""
        return {
            "source": SOURCE_NAME,
            "source_record_id": self.source_record_id,
            "action_category": self.action_category,
            "action_subtype": self.action_subtype,
            "symbol": self.symbol,
            "old_symbol": self.old_symbol,
            "new_symbol": self.new_symbol,
            "old_name": self.old_name,
            "new_name": self.new_name,
            "effective_date": self.effective_date,
            "event_code": self.event_code,
            "event_reason": self.event_reason,
            "raw_row": self.raw_row,
        }


@dataclass(frozen=True, slots=True)
class FinraOTCDailyListStagingResult:
    output_dir: Path
    status: str
    records: list[FinraOTCDailyListStagingRecord]
    summary: dict[str, object]
    records_json_path: Path
    records_csv_path: Path
    markdown_path: Path
    summary_path: Path

    @property
    def blocked(self) -> bool:
        return self.status == "BLOCKED"


class FinraOTCDailyListImporter:
    def __init__(
        self,
        output_dir: Path | str,
        *,
        client: httpx.Client | None = None,
        url: str = FINRA_OTC_DAILY_LIST_URL,
        timeout: float = 30.0,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.client = client
        self.url = url
        self.timeout = timeout

    def fetch_and_stage(
        self,
        *,
        limit: int = 1000,
        now: datetime | None = None,
    ) -> FinraOTCDailyListStagingResult:
        fetched_at = _coerce_now(now)
        if self.client is not None:
            return self._fetch_with_client(self.client, limit=limit, fetched_at=fetched_at)
        with httpx.Client() as client:
            return self._fetch_with_client(client, limit=limit, fetched_at=fetched_at)

    def stage_rows(
        self,
        rows: Iterable[dict[str, object]],
        *,
        now: datetime | None = None,
    ) -> FinraOTCDailyListStagingResult:
        staged = parse_finra_otc_daily_list_rows(rows)
        return _write_staging_artifacts(
            self.output_dir,
            status="READY",
            records=staged,
            summary_extra={
                "created_at": _coerce_now(now).isoformat(),
                "source_url": self.url,
                "input": "rows",
            },
        )

    def _fetch_with_client(
        self,
        client: httpx.Client,
        *,
        limit: int,
        fetched_at: datetime,
    ) -> FinraOTCDailyListStagingResult:
        try:
            response = client.get(
                self.url,
                params={"limit": str(limit)},
                headers={"Accept": "application/json"},
                follow_redirects=True,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            return _write_blocked_artifacts(
                self.output_dir,
                reason="request_failed",
                message=str(exc),
                status_code=None,
                source_url=self.url,
                limit=limit,
                created_at=fetched_at,
            )

        if response.status_code != 200:
            return _write_blocked_artifacts(
                self.output_dir,
                reason=f"http_status_{response.status_code}",
                message=_blocked_response_message(response),
                status_code=response.status_code,
                source_url=str(response.url),
                limit=limit,
                created_at=fetched_at,
            )

        try:
            records = parse_finra_otc_daily_list_payload(
                response.text,
                content_type=response.headers.get("content-type"),
            )
        except ValueError as exc:
            return _write_blocked_artifacts(
                self.output_dir,
                reason="parse_failed",
                message=str(exc),
                status_code=response.status_code,
                source_url=str(response.url),
                limit=limit,
                created_at=fetched_at,
            )

        return _write_staging_artifacts(
            self.output_dir,
            status="READY",
            records=records,
            summary_extra={
                "created_at": fetched_at.isoformat(),
                "source_url": str(response.url),
                "limit": limit,
                "http_status": response.status_code,
                "input": "finra_api",
            },
        )


def parse_finra_otc_daily_list_payload(
    payload: str | bytes | list[dict[str, object]] | dict[str, object],
    *,
    content_type: str | None = None,
) -> list[FinraOTCDailyListStagingRecord]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    if isinstance(payload, list):
        return parse_finra_otc_daily_list_rows(payload)
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return parse_finra_otc_daily_list_rows(data)
        return parse_finra_otc_daily_list_rows([payload])

    text = str(payload).lstrip("\ufeff").strip()
    if not text:
        return []

    normalized_content_type = str(content_type or "").lower()
    if "json" in normalized_content_type or text[0] in "[{":
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid FINRA OTC Daily List JSON: {exc}") from exc
        return parse_finra_otc_daily_list_payload(decoded)

    return parse_finra_otc_daily_list_csv(text)


def parse_finra_otc_daily_list_json(
    payload: str | bytes | list[dict[str, object]] | dict[str, object],
) -> list[FinraOTCDailyListStagingRecord]:
    return parse_finra_otc_daily_list_payload(payload, content_type="application/json")


def parse_finra_otc_daily_list_csv(
    payload_text: str,
) -> list[FinraOTCDailyListStagingRecord]:
    reader = csv.DictReader(io.StringIO(payload_text.lstrip("\ufeff")))
    return parse_finra_otc_daily_list_rows(
        row for row in reader if any(_present(value) for value in row.values())
    )


def parse_finra_otc_daily_list_rows(
    rows: Iterable[dict[str, object]],
) -> list[FinraOTCDailyListStagingRecord]:
    records: list[FinraOTCDailyListStagingRecord] = []
    for row_index, row in enumerate(rows):
        normalized = _normalize_input_row(row)
        records.extend(_records_from_row(normalized, row_index=row_index))
    return records


def records_to_corporate_actions_hook(
    records: Iterable[FinraOTCDailyListStagingRecord],
) -> list[dict[str, object]]:
    return [record.to_corporate_action_hook() for record in records]


def stage_finra_otc_daily_list_rows(
    rows: Iterable[dict[str, object]],
    output_dir: Path | str,
    *,
    now: datetime | None = None,
) -> FinraOTCDailyListStagingResult:
    return FinraOTCDailyListImporter(output_dir).stage_rows(rows, now=now)


def _records_from_row(
    row: dict[str, object],
    *,
    row_index: int,
) -> list[FinraOTCDailyListStagingRecord]:
    actions = _detected_actions(row)
    source_record_id = _normalize_text(row.get("OTCDailyListID"))
    base_id = source_record_id or f"row_{row_index + 1}"

    records: list[FinraOTCDailyListStagingRecord] = []
    for sequence, (category, subtype) in enumerate(actions, start=1):
        records.append(
            FinraOTCDailyListStagingRecord(
                staging_record_id=f"{base_id}:{category}:{sequence}",
                source_record_id=source_record_id,
                action_category=category,
                action_subtype=subtype,
                symbol=_row_symbol(row, category),
                old_symbol=_normalize_symbol(row.get("oldSymbolCode")),
                new_symbol=_normalize_symbol(row.get("newSymbolCode")),
                old_name=_normalize_text(row.get("oldSecurityDescription")),
                new_name=_normalize_text(row.get("newSecurityDescription")),
                effective_date=_effective_date(row),
                calendar_day=_normalize_date(row.get("calendarDay")),
                daily_list_datetime=_normalize_datetime(row.get("dailyListDatetime")),
                event_code=_normalize_text(row.get("dailyListEventCode")),
                event_reason=_normalize_text(row.get("dailyListReasonDescription")),
                ex_date=_normalize_date(row.get("exDate")),
                record_date=_normalize_date(row.get("recordDate")),
                payment_date=_normalize_date(row.get("paymentDate")),
                declaration_date=_normalize_date(row.get("declarationDate")),
                split_ratio=_split_ratio(row),
                cash_amount=_normalize_text(row.get("cashAmountText")),
                stock_percentage=_normalize_text(row.get("stockPercentage")),
                dividend_type_code=_normalize_text(row.get("dividendTypeCode")),
                dividend_type_description=_normalize_text(row.get("dividendTypeDescription")),
                old_market_category=_normalize_text(row.get("oldMarketCategoryCode")),
                new_market_category=_normalize_text(row.get("newMarketCategoryCode")),
                subject_corporate_action_code=_normalize_text(
                    row.get("subjectCorporateActionCode")
                ),
                comment=_normalize_text(row.get("commentText")),
                raw_row=_json_safe_dict(row),
            )
        )
    return records


def _detected_actions(row: dict[str, object]) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    if _yes(row.get("securityDeleteFlag")) or _legacy_type_contains(row, "delete"):
        actions.append(("deleted", "security_delete"))
    if _yes(row.get("securityAddFlag")) or _legacy_type_contains(row, "add"):
        actions.append(("security_add", "security_add"))
    if _has_symbol_change(row):
        actions.append(("symbol_change", "symbol_change"))
    if _has_name_change(row):
        actions.append(("name_change", "security_description_change"))
    if _has_split(row):
        actions.append(("split", _split_subtype(row)))
    if _has_dividend(row):
        actions.append(("dividend", _dividend_subtype(row)))

    if not actions and _has_generic_corporate_action(row):
        actions.append(("corporate_action", _generic_corporate_action_subtype(row)))
    if not actions:
        actions.append(("other", "unclassified_daily_list_row"))
    return actions


def _has_symbol_change(row: dict[str, object]) -> bool:
    old_symbol = _normalize_symbol(row.get("oldSymbolCode"))
    new_symbol = _normalize_symbol(row.get("newSymbolCode"))
    if _yes(row.get("changeSymbolFlag")):
        return True
    return bool(old_symbol and new_symbol and old_symbol != new_symbol)


def _has_name_change(row: dict[str, object]) -> bool:
    old_name = _normalize_text(row.get("oldSecurityDescription"))
    new_name = _normalize_text(row.get("newSecurityDescription"))
    if _yes(row.get("changeSecurityDescriptionFlag")):
        return True
    return bool(old_name and new_name and _squash(old_name) != _squash(new_name))


def _has_split(row: dict[str, object]) -> bool:
    reason = _combined_reason_text(row)
    return (
        _present(row.get("reverseSplitRate"))
        or _present(row.get("forwardSplitRate"))
        or "split" in reason
    )


def _has_dividend(row: dict[str, object]) -> bool:
    reason = _combined_reason_text(row)
    dividend_fields = (
        "dividendTypeCode",
        "dividendTypeDescription",
        "cashAmountText",
        "stockPercentage",
        "dividendMasterID",
        "ADRNetRate",
        "ADRGrossRate",
    )
    return any(_present(row.get(field)) for field in dividend_fields) or any(
        token in reason for token in ("dividend", "distribution")
    )


def _has_generic_corporate_action(row: dict[str, object]) -> bool:
    if _present(row.get("subjectCorporateActionCode")):
        return True
    generic_flags = (
        "changeRoundLotQuantityFlag",
        "bankruptcyFlag",
        "changeOATSReportableFlag",
        "changeRegFeeFlag",
        "changeOTCBBQuoteFlag",
        "changeSecurityAttributeFlag",
        "changeFinancialStatusFlag",
    )
    return any(_yes(row.get(flag)) for flag in generic_flags)


def _generic_corporate_action_subtype(row: dict[str, object]) -> str:
    checks = (
        ("bankruptcyFlag", "bankruptcy"),
        ("changeRoundLotQuantityFlag", "round_lot_change"),
        ("changeFinancialStatusFlag", "financial_status_change"),
        ("changeSecurityAttributeFlag", "security_attribute_change"),
        ("changeOTCBBQuoteFlag", "otcbb_quote_change"),
        ("changeOATSReportableFlag", "oats_reportable_change"),
        ("changeRegFeeFlag", "reg_fee_change"),
    )
    for field, subtype in checks:
        if _yes(row.get(field)):
            return subtype
    if _present(row.get("subjectCorporateActionCode")):
        return "subject_corporate_action"
    return "corporate_action"


def _split_subtype(row: dict[str, object]) -> str:
    reason = _combined_reason_text(row)
    if _present(row.get("reverseSplitRate")) or "reverse split" in reason:
        return "reverse_split"
    if _present(row.get("forwardSplitRate")) or "forward split" in reason:
        return "forward_split"
    return "split"


def _dividend_subtype(row: dict[str, object]) -> str:
    description = _normalize_text(row.get("dividendTypeDescription"))
    code = _normalize_text(row.get("dividendTypeCode"))
    if description:
        return _slug(description)
    if code:
        return f"dividend_{_slug(code)}"
    if _present(row.get("cashAmountText")):
        return "cash_dividend"
    if _present(row.get("stockPercentage")):
        return "stock_dividend"
    return "dividend"


def _split_ratio(row: dict[str, object]) -> str | None:
    return _normalize_text(row.get("reverseSplitRate")) or _normalize_text(
        row.get("forwardSplitRate")
    )


def _row_symbol(row: dict[str, object], category: str) -> str | None:
    old_symbol = _normalize_symbol(row.get("oldSymbolCode"))
    new_symbol = _normalize_symbol(row.get("newSymbolCode"))
    if category == "deleted":
        return old_symbol or new_symbol
    if category == "symbol_change":
        return new_symbol or old_symbol
    return new_symbol or old_symbol


def _effective_date(row: dict[str, object]) -> str | None:
    return (
        _normalize_date(row.get("exDate"))
        or _normalize_date(row.get("calendarDay"))
        or _normalize_date(row.get("dailyListDatetime"))
    )


def _legacy_type_contains(row: dict[str, object], needle: str) -> bool:
    event_code = str(row.get("dailyListEventCode") or "").lower()
    reason = _combined_reason_text(row)
    return needle in event_code or needle in reason


def _combined_reason_text(row: dict[str, object]) -> str:
    return " ".join(
        str(value or "").lower()
        for value in (
            row.get("dailyListEventCode"),
            row.get("dailyListReasonDescription"),
            row.get("commentText"),
            row.get("subjectCorporateActionCode"),
        )
    )


def _normalize_input_row(row: dict[str, object]) -> dict[str, object]:
    key_map = {_key_fingerprint(key): key for key in row.keys() if key is not None}
    normalized: dict[str, object] = dict(row)
    for official in OFFICIAL_FIELDS:
        candidates = (official, *_FIELD_ALIASES.get(official, ()))
        for candidate in candidates:
            raw_key = key_map.get(_key_fingerprint(candidate))
            if raw_key is not None:
                normalized[official] = row.get(raw_key)
                break
    return normalized


def _key_fingerprint(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _normalize_symbol(value: object) -> str | None:
    text = _normalize_text(value)
    return text.upper() if text else None


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "nan", "n/a"}:
        return None
    return text


def _normalize_date(value: object) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    for fmt in ("%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _normalize_datetime(value: object) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text.replace(" ", "T")
    return text


def _yes(value: object) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def _present(value: object) -> bool:
    return _normalize_text(value) is not None


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def _json_safe_dict(row: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in row.items():
        if key is None:
            continue
        safe[str(key)] = _json_safe(value)
    return safe


def _json_safe(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _coerce_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _write_staging_artifacts(
    output_dir: Path,
    *,
    status: str,
    records: list[FinraOTCDailyListStagingRecord],
    summary_extra: dict[str, object],
) -> FinraOTCDailyListStagingResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    records_json_path = output_dir / "finra_otc_daily_list_staging.json"
    records_csv_path = output_dir / "finra_otc_daily_list_staging.csv"
    markdown_path = output_dir / "finra_otc_daily_list_staging.md"
    summary_path = output_dir / "finra_otc_daily_list_summary.json"

    summary = {
        "source": SOURCE_NAME,
        "status": status,
        "record_count": len(records),
        "action_counts": _action_counts(records),
        "db_integration": {
            "status": "hook_only",
            "hook": "records_to_corporate_actions_hook",
            "message": (
                "No DB writes or schema assumptions are made here; wire this hook "
                "after the corporate_actions schema is chosen."
            ),
        },
        **summary_extra,
    }

    _write_json(records_json_path, [record.to_dict() for record in records])
    _write_csv(records_csv_path, [record.to_dict() for record in records])
    markdown_path.write_text(_markdown_summary(summary, records), encoding="utf-8")
    _write_json(summary_path, summary)

    return FinraOTCDailyListStagingResult(
        output_dir=output_dir,
        status=status,
        records=records,
        summary=summary,
        records_json_path=records_json_path,
        records_csv_path=records_csv_path,
        markdown_path=markdown_path,
        summary_path=summary_path,
    )


def _write_blocked_artifacts(
    output_dir: Path,
    *,
    reason: str,
    message: str,
    status_code: int | None,
    source_url: str,
    limit: int,
    created_at: datetime,
) -> FinraOTCDailyListStagingResult:
    extra = {
        "created_at": created_at.isoformat(),
        "source_url": source_url,
        "limit": limit,
        "http_status": status_code,
        "blocked": {
            "reason": reason,
            "message": message,
            "expected_input_csv_format": EXPECTED_INPUT_CSV_FORMAT,
        },
    }
    return _write_staging_artifacts(
        output_dir,
        status="BLOCKED",
        records=[],
        summary_extra=extra,
    )


def _action_counts(records: list[FinraOTCDailyListStagingRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.action_category] = counts.get(record.action_category, 0) + 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "source",
        "staging_record_id",
        "source_record_id",
        "action_category",
        "action_subtype",
        "symbol",
        "old_symbol",
        "new_symbol",
        "old_name",
        "new_name",
        "effective_date",
        "calendar_day",
        "daily_list_datetime",
        "event_code",
        "event_reason",
        "ex_date",
        "record_date",
        "payment_date",
        "declaration_date",
        "split_ratio",
        "cash_amount",
        "stock_percentage",
        "dividend_type_code",
        "dividend_type_description",
        "old_market_category",
        "new_market_category",
        "subject_corporate_action_code",
        "comment",
        "raw_row",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        json.dumps(row.get(field), sort_keys=True)
                        if isinstance(row.get(field), (dict, list))
                        else row.get(field)
                    )
                    for field in fieldnames
                }
            )


def _markdown_summary(
    summary: dict[str, object],
    records: list[FinraOTCDailyListStagingRecord],
) -> str:
    lines = [
        "# FINRA OTC Daily List Staging",
        "",
        f"- Status: {summary['status']}",
        f"- Records: {summary['record_count']}",
    ]
    if summary["status"] == "BLOCKED":
        blocked = summary.get("blocked", {})
        reason = blocked.get("reason") if isinstance(blocked, dict) else None
        lines.extend(
            [
                f"- Reason: {reason}",
                "",
                "## Expected Input CSV",
                "",
                "Use FINRA OTC Daily List field names. Minimum useful fields:",
                "",
                ", ".join(EXPECTED_INPUT_CSV_FORMAT["minimum_useful_fields"]),
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(["", "## Action Counts", ""])
    counts = summary.get("action_counts", {})
    if isinstance(counts, dict) and counts:
        for category, count in counts.items():
            lines.append(f"- {category}: {count}")
    else:
        lines.append("- none: 0")

    lines.extend(["", "## Sample", ""])
    for record in records[:5]:
        lines.append(
            f"- {record.action_category}: {record.old_symbol or ''} -> "
            f"{record.new_symbol or ''} ({record.effective_date or 'no_date'})"
        )
    return "\n".join(lines) + "\n"


def _blocked_response_message(response: httpx.Response) -> str:
    text = response.text.strip()
    if not text:
        return f"FINRA OTC Daily List request returned HTTP {response.status_code}."
    return text[:500]
