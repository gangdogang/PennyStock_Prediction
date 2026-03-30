from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def _config_value(config: Any, attr: str, default: Any) -> Any:
    return getattr(config, attr, default)


def _to_dict(record: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if is_dataclass(record):
        return asdict(record)
    if hasattr(record, "model_dump"):
        return record.model_dump()
    return dict(record)


def filter_universe(records: list[dict[str, Any]], config: Any) -> list[dict[str, Any]]:
    """Pure filtering helper used by Milestone 1 tests and higher-level builders."""

    filtered: list[dict[str, Any]] = []
    for raw_record in records:
        record = _to_dict(raw_record)
        price = record.get("price")
        market_cap = record.get("market_cap")
        float_shares = record.get("float_shares")
        price_min = _config_value(config, "price_min", 0.3)
        price_max = _config_value(config, "price_max", 5.0)
        market_cap_max = _config_value(config, "market_cap_max", 250_000_000)
        float_shares_max = _config_value(config, "float_shares_max", 10_000_000)

        if price is None or price < price_min or (price_max is not None and price > price_max):
            continue
        if market_cap is not None and market_cap_max is not None and market_cap > market_cap_max:
            continue
        if (
            float_shares is not None
            and float_shares_max is not None
            and float_shares > float_shares_max
        ):
            continue
        if _config_value(config, "exclude_etf", True) and record.get("is_etf"):
            continue
        if _config_value(config, "exclude_preferred", True) and record.get("is_preferred"):
            continue
        if _config_value(config, "exclude_warrants", True) and record.get("is_warrant"):
            continue
        if _config_value(config, "exclude_rights", True) and record.get("is_right"):
            continue
        if _config_value(config, "exclude_spac", True) and record.get("is_spac"):
            continue
        filtered.append(record)
    return filtered
