from __future__ import annotations

import json
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Callable

from ..config import AppSettings

logger = logging.getLogger(__name__)


def build_live_metric_emitter(
    settings: AppSettings,
    *,
    now_fn: Callable[[], datetime] | None = None,
) -> Callable[[str, dict[str, Any]], None]:
    disabled_reason = _disabled_reason(settings)
    enabled = disabled_reason is None
    output_path = settings.live_observability_path
    recorder = now_fn or (lambda: datetime.now(timezone.utc))
    if disabled_reason is not None:
        logger.info("live metric emitter disabled: %s", disabled_reason)

    def emit(metric_type: str, payload: dict[str, Any]) -> None:
        if not enabled:
            return
        record = {
            "metric_type": metric_type,
            "recorded_at": recorder(),
            **payload,
        }
        _append_jsonl(output_path, _json_ready(record))

    return emit


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
    except OSError:
        return


def _disabled_reason(settings: AppSettings) -> str | None:
    if settings.use_mock_market_data:
        return "mock mode"
    if settings.live_market_provider == "disabled":
        return "provider disabled"
    if not settings.live_observability_enabled:
        return "live_observability_enabled=false"
    return None


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value
