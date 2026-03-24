from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import load_first_module


CONFIG_CANDIDATES = (
    "penny_stock_radar.config",
    "config",
)


def test_settings_defaults_and_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENNY_STOCK_DB_PATH", "/tmp/penny_stock_test.sqlite3")
    monkeypatch.setenv("PENNY_STOCK_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("PENNY_STOCK_USE_MOCK_MARKET_DATA", "true")
    monkeypatch.setenv("PENNY_STOCK_UNIVERSE_PRICE_MIN", "0.25")
    monkeypatch.setenv("PENNY_STOCK_UNIVERSE_PRICE_MAX", "5.0")
    monkeypatch.setenv("PENNY_STOCK_LIVE_MARKET_PROVIDER", "polygon")
    monkeypatch.setenv("PENNY_STOCK_LIVE_MARKET_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("PENNY_STOCK_ALPACA_MARKET_DATA_FEED", "sip")

    module = load_first_module(CONFIG_CANDIDATES)

    settings_cls = getattr(module, "Settings", None)
    load_settings = getattr(module, "load_settings", None)
    assert settings_cls is not None or load_settings is not None, (
        "Milestone 1 should expose either a Settings model or a load_settings() helper."
    )

    settings = load_settings() if callable(load_settings) else settings_cls()

    assert Path(settings.db_path) == Path("/tmp/penny_stock_test.sqlite3")
    assert settings.log_level == "DEBUG"
    assert settings.use_mock_market_data is True
    assert settings.universe_price_min == pytest.approx(0.25)
    assert settings.universe_price_max == pytest.approx(5.0)
    assert settings.live_market_provider == "polygon"
    assert settings.live_market_timeout_seconds == pytest.approx(12.0)
    assert settings.alpaca_market_data_feed == "sip"


def test_settings_have_practical_defaults() -> None:
    module = load_first_module(CONFIG_CANDIDATES)

    settings_cls = getattr(module, "Settings", None)
    load_settings = getattr(module, "load_settings", None)
    assert settings_cls is not None or load_settings is not None, (
        "Milestone 1 should expose either a Settings model or a load_settings() helper."
    )

    settings = load_settings() if callable(load_settings) else settings_cls()

    assert Path(settings.db_path).name.endswith(".sqlite3") or Path(settings.db_path).suffix in {
        ".sqlite3",
        ".db",
    }
    assert settings.log_level in {"INFO", "WARNING", "DEBUG", "ERROR"}
    assert settings.universe_price_min < settings.universe_price_max
    assert settings.live_market_provider in {"auto", "polygon", "alpaca", "disabled"}
    assert settings.live_market_timeout_seconds > 0
