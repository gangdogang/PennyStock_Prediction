from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.conftest import load_first_module, normalize_records


UNIVERSE_CANDIDATES = (
    "penny_stock_radar.universe",
    "universe",
)


@dataclass
class UniverseConfig:
    price_min: float = 0.3
    price_max: float = 5.0
    market_cap_max: float = 250_000_000
    float_shares_max: float = 10_000_000
    exclude_etf: bool = True
    exclude_preferred: bool = True
    exclude_warrants: bool = True
    exclude_rights: bool = True
    exclude_spac: bool = True


def test_universe_filter_applies_milestone_one_rules() -> None:
    module = load_first_module(UNIVERSE_CANDIDATES)

    filter_fn = getattr(module, "filter_universe", None) or getattr(module, "build_universe", None)
    assert callable(filter_fn), (
        "Milestone 1 should expose filter_universe(records, config) or build_universe(records, config)."
    )

    records = [
        {
            "ticker": "KEEP",
            "price": 1.25,
            "market_cap": 120_000_000,
            "float_shares": 4_500_000,
            "is_etf": False,
            "is_preferred": False,
            "is_warrant": False,
            "is_right": False,
            "is_spac": False,
        },
        {
            "ticker": "TOO_EXPENSIVE",
            "price": 7.5,
            "market_cap": 120_000_000,
            "float_shares": 4_500_000,
            "is_etf": False,
            "is_preferred": False,
            "is_warrant": False,
            "is_right": False,
            "is_spac": False,
        },
        {
            "ticker": "TOO_MUCH_FLOAT",
            "price": 1.25,
            "market_cap": 120_000_000,
            "float_shares": 25_000_000,
            "is_etf": False,
            "is_preferred": False,
            "is_warrant": False,
            "is_right": False,
            "is_spac": False,
        },
        {
            "ticker": "ETF",
            "price": 1.25,
            "market_cap": 120_000_000,
            "float_shares": 4_500_000,
            "is_etf": True,
            "is_preferred": False,
            "is_warrant": False,
            "is_right": False,
            "is_spac": False,
        },
    ]

    result = filter_fn(records, UniverseConfig())
    filtered = normalize_records(result)

    assert [row["ticker"] for row in filtered] == ["KEEP"]


def test_universe_filter_preserves_reasonable_metadata() -> None:
    module = load_first_module(UNIVERSE_CANDIDATES)
    filter_fn = getattr(module, "filter_universe", None) or getattr(module, "build_universe", None)

    records = [
        {
            "ticker": "A",
            "price": 0.8,
            "market_cap": 50_000_000,
            "float_shares": 2_000_000,
            "is_etf": False,
            "is_preferred": False,
            "is_warrant": False,
            "is_right": False,
            "is_spac": False,
            "sector": "Biotech",
        }
    ]

    result = normalize_records(filter_fn(records, UniverseConfig()))
    assert result[0]["sector"] == "Biotech"
    assert result[0]["ticker"] == "A"
