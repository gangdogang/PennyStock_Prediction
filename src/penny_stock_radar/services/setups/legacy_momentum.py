from __future__ import annotations

from datetime import datetime

from ...models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from ..engine_rules import entry as entry_rules
from ..engine_rules import exit as exit_rules
from ..engine_rules import profit as profit_rules
from ..engine_rules.entry import EntryRuleDeps
from ..engine_rules.exit import ExitRuleDeps
from ..engine_rules.profit import ProfitRuleDeps
from ..momentum_advisor import MomentumAdvice
from ..paper_runtime import (
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
    WATCHLIST_MOMENTUM_BUCKET,
)
from .base import (
    ACTION_ADD,
    ACTION_ENTER,
    ACTION_SKIP,
    SetupDecision,
    SetupEvalContext,
)

LEGACY_BUCKETS = (
    PREDICTOR_WEIGHTED_BUCKET,
    MOMENTUM_ONLY_BUCKET,
    WATCHLIST_MOMENTUM_BUCKET,
    "watchlist_momentum",
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
)


class LegacyMomentumSetup:
    setup_id = "legacy_momentum"
    bucket_compatibility = LEGACY_BUCKETS

    def detect(self, ctx: SetupEvalContext) -> bool:
        if ctx.open_position is not None:
            return entry_rules.can_add(
                ctx.deps,
                ctx.open_position,
                ctx.market_activity,
                ctx.advice,
                market_phase=self._market_phase(ctx),
                now=ctx.bar_at,
            )
        return entry_rules.eligible_for_entry(
            ctx.deps,
            row=ctx.market_activity,
            advice=ctx.advice,
            market_phase=self._market_phase(ctx),
            open_symbols=set(ctx.open_symbols),
            traded_today=set(ctx.traded_today),
        )

    def evaluate(self, ctx: SetupEvalContext) -> SetupDecision:
        if not self.detect(ctx):
            return SetupDecision(
                setup_id=self.setup_id,
                action=ACTION_SKIP,
                size_fraction=0.0,
                stop_price=None,
                target_price=None,
                reason="legacy_momentum_not_detected",
            )
        if ctx.open_position is not None:
            return self._evaluate_add(ctx)
        return self._evaluate_entry(ctx)

    def apply_entry_rules(
        self,
        deps: EntryRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity: list[MarketActivity],
        advice_by_symbol: dict[str, MomentumAdvice],
        market_phase: str,
        market_date: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return entry_rules.apply_entry_rules(
            deps,
            run=run,
            positions=positions,
            activity=activity,
            advice_by_symbol=advice_by_symbol,
            market_phase=market_phase,
            market_date=market_date,
            day_regime=day_regime,
            now=now,
        )

    def apply_exit_rules(
        self,
        deps: ExitRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return exit_rules.apply_exit_rules(
            deps,
            run=run,
            positions=positions,
            activity_by_symbol=activity_by_symbol,
            market_phase=market_phase,
            day_regime=day_regime,
            now=now,
        )

    def apply_profit_management(
        self,
        deps: ProfitRuleDeps,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return profit_rules.apply_profit_management(
            deps,
            run=run,
            positions=positions,
            activity_by_symbol=activity_by_symbol,
            market_phase=market_phase,
            day_regime=day_regime,
            now=now,
        )

    def _evaluate_entry(self, ctx: SetupEvalContext) -> SetupDecision:
        entry_tag = (
            entry_rules.adaptive_entry_tag(
                ctx.deps,
                row=ctx.market_activity,
                advice=ctx.advice,
                market_phase=self._market_phase(ctx),
                open_symbols=set(ctx.open_symbols),
                traded_today=set(ctx.traded_today),
            )
            if ctx.deps.strategy_mode == "adaptive"
            else None
        )
        fill_price = self._entry_reference_price(ctx.market_activity)
        stop = (
            fill_price * (1 - ctx.deps.settings.paper_stop_loss_pct / 100.0)
            if fill_price is not None
            else None
        )
        return SetupDecision(
            setup_id=self.setup_id,
            action=ACTION_ENTER,
            size_fraction=entry_rules.entry_fraction(
                ctx.deps,
                row=ctx.market_activity,
                entry_tag=entry_tag,
            ),
            stop_price=stop,
            target_price=None,
            reason="legacy_momentum_entry",
            metadata={"entry_tag": entry_tag or ""},
        )

    def _evaluate_add(self, ctx: SetupEvalContext) -> SetupDecision:
        stop = (
            ctx.deps.stop_price(ctx.open_position)
            if ctx.open_position is not None
            else None
        )
        return SetupDecision(
            setup_id=self.setup_id,
            action=ACTION_ADD,
            size_fraction=ctx.deps.settings.paper_add_size_fraction,
            stop_price=stop,
            target_price=None,
            reason="legacy_momentum_add",
            metadata={"position_id": ctx.open_position.position_id if ctx.open_position else ""},
        )

    def _market_phase(self, ctx: SetupEvalContext) -> str:
        return ctx.market_phase or ctx.market_activity.market_phase

    def _entry_reference_price(self, row: MarketActivity) -> float | None:
        if row.ask_price is not None and row.ask_price > 0:
            return row.ask_price
        return row.last_price if row.last_price is not None and row.last_price > 0 else None
