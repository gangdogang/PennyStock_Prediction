from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


# KIS overseas-stock output1.dymd/dhms are interpreted as US/Eastern based on 2026-04-18 archive rows showing <2h quote_at-created_at deltas; kis_historical canary warns on >120m drift.
def parse_kis_market_timestamp(
    date_value: Any,
    time_value: Any,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    date_digits = "".join(ch for ch in str(date_value or "").strip() if ch.isdigit())
    time_digits = "".join(ch for ch in str(time_value or "").strip() if ch.isdigit())
    if len(time_digits) == 4:
        time_digits = f"{time_digits}00"
    if len(date_digits) == 8 and len(time_digits) == 6:
        try:
            return datetime(
                int(date_digits[0:4]),
                int(date_digits[4:6]),
                int(date_digits[6:8]),
                int(time_digits[0:2]),
                int(time_digits[2:4]),
                int(time_digits[4:6]),
                tzinfo=EASTERN,
            ).astimezone(timezone.utc)
        except ValueError:
            return None
    return backsolve_kis_clock_to_utc(time_value, now_utc=now_utc)


def backsolve_kis_clock_to_utc(
    time_value: Any,
    *,
    now_utc: datetime | None = None,
) -> datetime | None:
    if time_value is None or time_value == "":
        return None
    digits = "".join(ch for ch in str(time_value).strip() if ch.isdigit())
    if len(digits) not in {4, 6}:
        return None
    if len(digits) == 4:
        digits = f"{digits}00"
    try:
        hours = int(digits[0:2])
        minutes = int(digits[2:4])
        seconds = int(digits[4:6])
    except ValueError:
        return None

    reference_utc = now_utc or datetime.now(timezone.utc)
    reference_kst = reference_utc.astimezone(KST)
    candidates: list[datetime] = []
    for day_offset in (-1, 0, 1):
        local_date = reference_kst.date() + timedelta(days=day_offset)
        try:
            candidates.append(
                datetime(
                    local_date.year,
                    local_date.month,
                    local_date.day,
                    hours,
                    minutes,
                    seconds,
                    tzinfo=KST,
                ).astimezone(timezone.utc)
            )
        except ValueError:
            return None

    return min(
        candidates,
        key=lambda candidate: abs((candidate - reference_utc).total_seconds()),
    )
