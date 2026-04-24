from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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
    monkeypatch.setenv("PENNY_STOCK_LIVE_MARKET_PROVIDER", "kis")
    monkeypatch.setenv("PENNY_STOCK_BROKER_ADAPTER", "kis_mock")
    monkeypatch.setenv("PENNY_STOCK_LIVE_MARKET_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("PENNY_STOCK_LIVE_OBSERVABILITY_ENABLED", "false")
    monkeypatch.setenv("PENNY_STOCK_LIVE_OBSERVABILITY_PATH", "/tmp/live_metrics.jsonl")
    monkeypatch.setenv("PENNY_STOCK_BACKTEST_COVERAGE_REPORT_DIR", "/tmp/backtest_coverage")
    monkeypatch.setenv("PENNY_STOCK_BACKTEST_COVERAGE_GATE_PATH", "/tmp/backtest_coverage_gate_status.json")
    monkeypatch.setenv("PENNY_STOCK_BACKTEST_COVERAGE_GATE_PCT", "65.0")
    monkeypatch.setenv("PENNY_STOCK_KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    monkeypatch.setenv("PENNY_STOCK_KIS_MOCK_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    monkeypatch.setenv("PENNY_STOCK_KIS_APP_KEY", "kis-app")
    monkeypatch.setenv("PENNY_STOCK_KIS_APP_SECRET", "kis-secret")
    monkeypatch.setenv("PENNY_STOCK_KIS_MOCK_APP_KEY", "mock-app")
    monkeypatch.setenv("PENNY_STOCK_KIS_MOCK_APP_SECRET", "mock-secret")
    monkeypatch.setenv("PENNY_STOCK_KIS_MOCK_ACCOUNT_NUMBER", "12345678")
    monkeypatch.setenv("PENNY_STOCK_KIS_MOCK_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("PENNY_STOCK_KIS_MARKET_DIV_CODE", "J")
    monkeypatch.setenv("PENNY_STOCK_KIS_CUSTTYPE", "P")
    monkeypatch.setenv("PENNY_STOCK_PREDICTOR_WEIGHT_TOTAL_SCORE", "9.5")
    monkeypatch.setenv("PENNY_STOCK_PREDICTOR_MAX_HOLD_DAYS_TIER2_TOTAL", "5.5")
    monkeypatch.setenv("PENNY_STOCK_PAPER_HALT_RESUME_DECAY_MINUTES", "4.0")
    monkeypatch.setenv("PENNY_STOCK_PAPER_VOLUME_CAP_SMALL_MARKET_CAP_THRESHOLD", "400000000")
    monkeypatch.setenv("PENNY_STOCK_PAPER_VOLUME_CAP_SMALL_MARKET_CAP_SCALE", "0.4")

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
    assert settings.live_market_provider == "kis"
    assert settings.broker_adapter == "kis_mock"
    assert settings.live_market_timeout_seconds == pytest.approx(12.0)
    assert settings.live_observability_enabled is False
    assert Path(settings.live_observability_path) == Path("/tmp/live_metrics.jsonl")
    assert Path(settings.backtest_coverage_report_dir) == Path("/tmp/backtest_coverage")
    assert Path(settings.backtest_coverage_gate_path) == Path("/tmp/backtest_coverage_gate_status.json")
    assert settings.backtest_coverage_gate_pct == pytest.approx(65.0)
    assert settings.kis_base_url == "https://openapi.koreainvestment.com:9443"
    assert settings.kis_mock_base_url == "https://openapivts.koreainvestment.com:29443"
    assert settings.kis_app_key == "kis-app"
    assert settings.kis_app_secret == "kis-secret"
    assert settings.kis_mock_app_key == "mock-app"
    assert settings.kis_mock_app_secret == "mock-secret"
    assert settings.kis_mock_account_number == "12345678"
    assert settings.kis_mock_account_product_code == "01"
    assert settings.kis_market_div_code == "J"
    assert settings.kis_custtype == "P"
    assert settings.predictor_weight_total_score == pytest.approx(9.5)
    assert settings.predictor_max_hold_days_tier2_total == pytest.approx(5.5)
    assert settings.paper_halt_resume_decay_minutes == pytest.approx(4.0)
    assert settings.paper_volume_cap_small_market_cap_threshold == 400_000_000
    assert settings.paper_volume_cap_small_market_cap_scale == pytest.approx(0.4)


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
    assert settings.live_market_provider == "kis"
    assert settings.live_market_provider in {"kis", "disabled"}
    assert settings.broker_adapter in {"null", "kis_mock"}
    assert settings.live_market_timeout_seconds > 0
    assert settings.live_observability_enabled is True
    assert Path(settings.live_observability_path).name == "live_metrics.jsonl"
    assert Path(settings.backtest_coverage_report_dir).name == "backtest_coverage"
    assert Path(settings.backtest_coverage_gate_path).name == "backtest_coverage_gate_status.json"
    assert settings.backtest_coverage_gate_pct == pytest.approx(60.0)
    assert settings.kis_base_url.startswith("https://")
    assert settings.kis_mock_base_url.startswith("https://")
    assert settings.kis_market_div_code in {"J", "US", "NAS", "NYS", "AMS"}
    assert settings.kis_custtype in {"P", "B"}
    assert settings.paper_fill_slippage_pct == pytest.approx(0.15)
    assert settings.paper_stop_gap_slippage_pct == pytest.approx(0.50)
    assert settings.paper_halt_resume_decay_minutes == pytest.approx(3.0)
    assert settings.paper_volume_cap_small_market_cap_threshold == 500_000_000
    assert settings.paper_volume_cap_small_market_cap_scale == pytest.approx(0.5)
    assert settings.trade_plan_per_trade_risk_pct == pytest.approx(0.35)
    assert settings.trade_plan_starter_notional_cap_pct == pytest.approx(8.0)
    assert settings.trade_plan_add_notional_cap_pct == pytest.approx(5.0)
    assert settings.trade_plan_daily_loss_lock_pct == pytest.approx(2.0)
    assert settings.trade_plan_max_concurrent_open_risk_pct == pytest.approx(1.0)
    assert settings.paper_predictor_weight_k1 == pytest.approx(1.0)
    assert settings.paper_predictor_weight_k2 == pytest.approx(1.0)
    assert settings.predictor_weight_total_score == pytest.approx(11.0)
    assert settings.predictor_weight_catalyst == pytest.approx(8.0)
    assert settings.predictor_weight_technical == pytest.approx(4.0)
    assert settings.predictor_weight_sympathy == pytest.approx(3.0)
    assert settings.predictor_weight_market_context == pytest.approx(4.0)
    assert settings.predictor_weight_social == pytest.approx(2.0)
    assert settings.predictor_weight_low_float_bonus == pytest.approx(3.0)
    assert settings.predictor_weight_filing_bonus == pytest.approx(6.0)
    assert settings.predictor_weight_multi_theme_bonus == pytest.approx(4.0)
    assert settings.predictor_max_hold_days_tier3_total == pytest.approx(6.5)
    assert settings.predictor_max_hold_days_tier3_catalyst == pytest.approx(1.0)
    assert settings.predictor_max_hold_days_tier2_total == pytest.approx(4.5)
    assert settings.trade_plan_stale_data_seconds == 15
    assert settings.trade_plan_halt_suspected_seconds == 60


def test_predictor_settings_reject_negative_values() -> None:
    module = load_first_module(CONFIG_CANDIDATES)

    settings_cls = getattr(module, "Settings", None)
    assert settings_cls is not None, "Config module should expose Settings."

    with pytest.raises(ValidationError):
        settings_cls(predictor_weight_total_score=-1.0)

    with pytest.raises(ValidationError):
        settings_cls(predictor_max_hold_days_tier2_total=-0.1)

    with pytest.raises(ValidationError):
        settings_cls(paper_halt_resume_decay_minutes=-1.0)

    with pytest.raises(ValidationError):
        settings_cls(paper_volume_cap_small_market_cap_threshold=0)

    with pytest.raises(ValidationError):
        settings_cls(paper_volume_cap_small_market_cap_scale=-0.1)

    with pytest.raises(ValidationError):
        settings_cls(broker_adapter="unsupported")
