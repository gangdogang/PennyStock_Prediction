from __future__ import annotations

from collections.abc import Sequence

from .base import ACTION_SKIP, Setup, SetupDecision, SetupEvalContext


class SetupRegistry:
    def __init__(self, setups: Sequence[Setup]) -> None:
        self._setups = tuple(setups)
        self._by_bucket: dict[str, tuple[Setup, ...]] = {}
        for setup in self._setups:
            for bucket in setup.bucket_compatibility:
                existing = self._by_bucket.get(bucket, ())
                if setup not in existing:
                    self._by_bucket[bucket] = (*existing, setup)

    def setups_for_bucket(self, bucket: str) -> tuple[Setup, ...]:
        return self._by_bucket.get(bucket, ())

    def primary_for_bucket(self, bucket: str) -> Setup | None:
        setups = self.setups_for_bucket(bucket)
        return setups[0] if setups else None

    def evaluate(self, ctx: SetupEvalContext) -> list[SetupDecision]:
        results: list[SetupDecision] = []
        for setup in self.setups_for_bucket(ctx.bucket):
            if not setup.detect(ctx):
                continue
            decision = setup.evaluate(ctx)
            if decision.action != ACTION_SKIP:
                results.append(decision)
        return results


def build_default_registry() -> SetupRegistry:
    from .fade_short import FadeShortSetup
    from .legacy_momentum import LegacyMomentumSetup

    return SetupRegistry((LegacyMomentumSetup(), FadeShortSetup()))
