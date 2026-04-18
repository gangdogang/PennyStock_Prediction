from __future__ import annotations

from penny_stock_radar.config import AppSettings
from penny_stock_radar.observability.metrics import build_live_metric_emitter


def test_live_metric_emitter_logs_mock_mode_disable_reason(tmp_path, caplog) -> None:
    settings = AppSettings(
        live_observability_path=tmp_path / "live_metrics.jsonl",
        use_mock_market_data=True,
        live_market_provider="kis",
        live_observability_enabled=True,
    )

    with caplog.at_level("INFO"):
        emit = build_live_metric_emitter(settings)

    emit("scan_summary", {"symbol": "TSLA"})

    assert "live metric emitter disabled: mock mode" in caplog.text
    assert not settings.live_observability_path.exists()


def test_live_metric_emitter_logs_provider_disabled_reason(tmp_path, caplog) -> None:
    settings = AppSettings(
        live_observability_path=tmp_path / "live_metrics.jsonl",
        use_mock_market_data=False,
        live_market_provider="disabled",
        live_observability_enabled=True,
    )

    with caplog.at_level("INFO"):
        emit = build_live_metric_emitter(settings)

    emit("scan_summary", {"symbol": "TSLA"})

    assert "live metric emitter disabled: provider disabled" in caplog.text
    assert not settings.live_observability_path.exists()


def test_live_metric_emitter_logs_flag_disable_reason(tmp_path, caplog) -> None:
    settings = AppSettings(
        live_observability_path=tmp_path / "live_metrics.jsonl",
        use_mock_market_data=False,
        live_market_provider="kis",
        live_observability_enabled=False,
    )

    with caplog.at_level("INFO"):
        emit = build_live_metric_emitter(settings)

    emit("scan_summary", {"symbol": "TSLA"})

    assert "live metric emitter disabled: live_observability_enabled=false" in caplog.text
    assert not settings.live_observability_path.exists()
