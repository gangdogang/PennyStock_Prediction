from __future__ import annotations

import pandas as pd

from penny_stock_radar.services.setup_scanner import SetupScanner


def test_setup_scanner_flags_above_20dma_and_contraction(monkeypatch) -> None:
    index = pd.date_range("2026-01-01", periods=40, freq="D")
    close = pd.Series([1.0 + (idx * 0.03) for idx in range(40)], index=index)
    wide_range = [0.25] * 30 + [0.05] * 10
    high = pd.Series([value + delta for value, delta in zip(close, wide_range)], index=index)
    low = pd.Series([value - delta for value, delta in zip(close, wide_range)], index=index)
    volume = pd.Series([100_000] * 40, index=index)

    frame = pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )

    monkeypatch.setattr(
        "penny_stock_radar.services.setup_scanner.yf.download",
        lambda **_: frame,
    )

    scanner = SetupScanner()
    signals = scanner.scan_symbols(["ABCD"])

    assert "ABCD" in signals
    signal = signals["ABCD"]
    assert signal.above_sma20 is True
    assert signal.volatility_contraction is True
    assert signal.setup_score >= 2.0
