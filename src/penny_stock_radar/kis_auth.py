from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_cached_token(cache_path: Path) -> tuple[str | None, datetime | None]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    token = payload.get("access_token")
    expires_at = _coerce_datetime(payload.get("expires_at"))
    if not token or expires_at is None:
        return None, None
    if expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30):
        return None, None
    return str(token), expires_at


def store_cached_token(cache_path: Path, *, access_token: str, expires_at: datetime | None) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "expires_at": expires_at.astimezone(timezone.utc).isoformat() if expires_at else None,
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
