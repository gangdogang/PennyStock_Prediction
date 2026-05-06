from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ...models import MarketActivity
from ..engine_rules.entry import EntryRuleDeps
from ..momentum_advisor import MomentumAdvice
from ..setup_state import SetupJudgement

if TYPE_CHECKING:
    from ...models import PaperPosition


ACTION_ENTER = "ENTER"
ACTION_ADD = "ADD"
ACTION_TRIM = "TRIM"
ACTION_EXIT = "EXIT"
ACTION_HOLD = "HOLD"
ACTION_SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class SetupDecision:
    setup_id: str
    action: str
    size_fraction: float
    stop_price: float | None
    target_price: float | None
    reason: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SetupEvalContext:
    market_activity: MarketActivity
    bar_at: datetime
    bucket: str
    deps: EntryRuleDeps
    open_position: PaperPosition | None
    setup_judgement: SetupJudgement | None
    advice: MomentumAdvice | None = None
    market_phase: str | None = None
    open_symbols: frozenset[str] = frozenset()
    traded_today: frozenset[str] = frozenset()


@runtime_checkable
class Setup(Protocol):
    setup_id: str
    bucket_compatibility: tuple[str, ...]

    def detect(self, ctx: SetupEvalContext) -> bool: ...

    def evaluate(self, ctx: SetupEvalContext) -> SetupDecision: ...
