from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from penny_stock_radar.config import AppSettings
from penny_stock_radar.services.setups import (
    ACTION_ENTER,
    ACTION_SKIP,
    SetupDecision,
    SetupRegistry,
    build_default_registry,
)
from penny_stock_radar.services.setups.legacy_momentum import LegacyMomentumSetup


@dataclass(frozen=True)
class FakeSetup:
    setup_id: str
    bucket_compatibility: tuple[str, ...]
    detected: bool
    action: str

    def detect(self, ctx) -> bool:
        return self.detected

    def evaluate(self, ctx) -> SetupDecision:
        return SetupDecision(
            setup_id=self.setup_id,
            action=self.action,
            size_fraction=0.25,
            stop_price=0.95,
            target_price=None,
            reason="fake",
        )


def _ctx(bucket: str = "predictor_weighted") -> SimpleNamespace:
    return SimpleNamespace(bucket=bucket)


def test_empty_registry_evaluate_returns_empty_list() -> None:
    registry = SetupRegistry(())

    assert registry.evaluate(_ctx()) == []


def test_registry_ignores_bucket_mismatch() -> None:
    registry = SetupRegistry((FakeSetup("fake", ("other",), True, ACTION_ENTER),))

    assert registry.evaluate(_ctx("predictor_weighted")) == []


def test_registry_ignores_detect_false_setup() -> None:
    registry = SetupRegistry((FakeSetup("fake", ("predictor_weighted",), False, ACTION_ENTER),))

    assert registry.evaluate(_ctx()) == []


def test_registry_ignores_skip_decision() -> None:
    registry = SetupRegistry((FakeSetup("fake", ("predictor_weighted",), True, ACTION_SKIP),))

    assert registry.evaluate(_ctx()) == []


def test_registry_returns_enter_decision() -> None:
    registry = SetupRegistry((FakeSetup("fake", ("predictor_weighted",), True, ACTION_ENTER),))

    decisions = registry.evaluate(_ctx())

    assert len(decisions) == 1
    assert decisions[0].setup_id == "fake"
    assert decisions[0].action == ACTION_ENTER


def test_default_registry_exposes_legacy_momentum_setup() -> None:
    registry = build_default_registry()

    setup = registry.primary_for_bucket("predictor_weighted")

    assert isinstance(setup, LegacyMomentumSetup)


def test_psr_use_setup_registry_env_flag_can_disable_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PSR_USE_SETUP_REGISTRY", raising=False)
    settings = AppSettings(db_path=tmp_path / "radar.sqlite3")

    assert settings.setup_registry_enabled() is True

    monkeypatch.setenv("PSR_USE_SETUP_REGISTRY", "0")

    assert settings.setup_registry_enabled() is False
