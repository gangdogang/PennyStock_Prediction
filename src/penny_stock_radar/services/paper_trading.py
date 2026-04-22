from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import uuid

from ..config import AppSettings
from ..db import (
    create_paper_trading_run,
    fetch_active_paper_trading_run,
    fetch_latest_premkt_predictions,
)
from ..models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from .engine_shared import StepContext, StepHookResult, StrategyHook, StrategyRules, run_step
from .engine_rules import (
    EntryRuleDeps,
    ExitRuleDeps,
    ProfitRuleDeps,
    adaptive_entry_tag,
    apply_entry_rules,
    apply_exit_rules,
    apply_profit_management,
    can_add,
    can_add_baseline,
    candidate_sort_key,
    close_for_session_end,
    close_position,
    eligible_for_baseline_entry,
    eligible_for_entry,
    eligible_for_live_exception_entry,
    eligible_for_predicted_priority_entry,
    entry_reasons,
    entry_score_threshold,
    entry_fraction,
    predictor_score,
    predictor_weight,
    planned_risk_pct,
    predictor_weight_for_score,
    stop_price,
    trim_position,
)
from .fill_model import FillModel
from .market_activity import MarketActivityScanner
from .momentum_advisor import MomentumAdvice, build_momentum_advisor
from .paper_bucket_policy import bucket_uses_predictor_weight
from .paper_runtime import (
    MARKET_STATUS_STATE_PREFIX,
    MOMENTUM_ONLY_BUCKET,
    PREDICTOR_WEIGHTED_BUCKET,
    PRIMARY_PAPER_STRATEGY,
    PAPER_BUCKET_LABELS,
    PAPER_STRATEGY_LABELS,
    PaperTradingStepResult,
    WATCHLIST_BLIND_MOMENTUM_BUCKET,
    active_position_symbols, default_paper_bucket, export_run_csv,
    paper_market_date, run_engine_once,
)
from .trading_support import (
    classify_day_regime, current_day_pnl,
    daily_loss_lock_marker, has_daily_loss_lock, halt_suspected,
    live_quote_is_valid, market_status_blocks_trading, normalize_market_status,
    state_markers,
)


class PaperTradingEngine:
    def __init__(
        self,
        settings: AppSettings,
        strategy_name: str = PRIMARY_PAPER_STRATEGY,
        bucket: str | None = None,
        export_dir: Path | None = None,
        advisor: object | None = None,
        now_fn: Callable[[], datetime] | None = None,
        strategy_mode: str = "adaptive",
    ) -> None:
        self.settings = settings
        self.strategy_name = strategy_name
        self.strategy_mode = strategy_mode
        self.bucket = bucket or self._default_bucket(strategy_name, strategy_mode)
        self.export_dir = export_dir or settings.paper_trade_dir
        if strategy_mode == "adaptive":
            self.advisor = advisor if advisor is not None else build_momentum_advisor(settings)
        else:
            self.advisor = None
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.scanner = MarketActivityScanner(settings)
        self.fill_model = FillModel(settings)
        self._predictor_score_by_symbol: dict[str, float] = {}

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        return run_engine_once(
            self,
            phase=phase,
            activity=activity,
            export_csv=export_csv,
        )

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        return run_step(
            self,
            market_phase=market_phase,
            activity=activity,
            strategy_rules=self._strategy_rules(),
            export_csv=export_csv,
        )

    def _strategy_rules(self) -> StrategyRules:
        return StrategyRules(
            prepare=StrategyHook(callback=self._prepare_step),
            apply_exit_rules=StrategyHook(callback=self._run_exit_rules),
            apply_profit_management=StrategyHook(callback=self._run_profit_management),
            apply_entry_rules=StrategyHook(callback=self._run_entry_rules),
            apply_session_close=StrategyHook(callback=self._run_session_close),
        )

    def _prepare_step(self, context: StepContext) -> StepHookResult:
        self._refresh_predictor_scores()
        return StepHookResult()

    def _run_exit_rules(self, context: StepContext) -> StepHookResult:
        orders = self._apply_exit_rules(
            run=context.run,
            positions=context.positions,
            activity_by_symbol=context.activity_by_symbol,
            market_phase=context.market_phase,
            day_regime=context.day_regime,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            exited_count=sum(1 for order in orders if order.intent == "EXIT"),
            actions=tuple(f"exit:{order.symbol}" for order in orders),
        )

    def _run_profit_management(self, context: StepContext) -> StepHookResult:
        orders = self._apply_profit_management(
            run=context.run,
            positions=context.positions,
            activity_by_symbol=context.activity_by_symbol,
            market_phase=context.market_phase,
            day_regime=context.day_regime,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            actions=tuple(f"trim:{order.symbol}" for order in orders),
        )

    def _run_entry_rules(self, context: StepContext) -> StepHookResult:
        orders = self._apply_entry_rules(
            run=context.run,
            positions=context.positions,
            activity=context.activity,
            advice_by_symbol=context.advice_by_symbol,
            market_phase=context.market_phase,
            market_date=context.market_date,
            day_regime=context.day_regime,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            entered_count=sum(1 for order in orders if order.intent == "ENTRY"),
            added_count=sum(1 for order in orders if order.intent == "ADD"),
            actions=tuple(f"{order.intent.lower()}:{order.symbol}" for order in orders),
        )

    def _run_session_close(self, context: StepContext) -> StepHookResult:
        orders = self._close_for_session_end(
            run=context.run,
            positions=context.positions,
            market_phase=context.market_phase,
            now=context.now,
        )
        return StepHookResult(
            orders=tuple(orders),
            exited_count=sum(1 for order in orders if order.intent == "EXIT"),
            actions=tuple(f"session_exit:{order.symbol}" for order in orders),
        )

    def _classify_day_regime(self, activity: list[MarketActivity]) -> str:
        return (
            classify_day_regime(self.settings, activity)
            if activity
            else "closed"
        )

    def export_run_csv(self, run_id: str | None = None) -> Path:
        return export_run_csv(self, run_id=run_id)

    def _get_or_create_run(self) -> PaperTradingRun:
        run = fetch_active_paper_trading_run(
            self.settings.database_path,
            self.strategy_name,
            bucket=self.bucket,
        )
        if run is not None:
            return run
        return create_paper_trading_run(
            self.settings.database_path,
            strategy_name=self.strategy_name,
            initial_capital=self.settings.paper_initial_capital,
            notes=["auto paper trading bootstrapped"],
            bucket=self.bucket,
        )

    def _active_position_symbols(self) -> tuple[str, ...]:
        run = fetch_active_paper_trading_run(
            self.settings.database_path,
            self.strategy_name,
            bucket=self.bucket,
        )
        return active_position_symbols(
            self.settings.database_path,
            run.run_id if run is not None else None,
        )

    def active_position_symbols(self) -> tuple[str, ...]:
        return self._active_position_symbols()

    def _equity_basis(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
    ) -> float:
        open_positions = [row for row in positions if row.status == "OPEN"]
        equity = run.cash_balance + sum(row.market_value for row in open_positions)
        return equity if equity > 0 else run.initial_capital

    def _daily_loss_locked(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        market_date: str,
        equity_basis: float,
        now: datetime,
    ) -> bool:
        if has_daily_loss_lock(run.notes, market_date):
            return True
        return current_day_pnl(now=now, positions=positions) <= -(
            equity_basis * self.settings.trade_plan_daily_loss_lock_pct / 100.0
        )

    def _latch_daily_loss_lock(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        market_date: str,
    ) -> None:
        marker = daily_loss_lock_marker(market_date)
        if marker in run.notes:
            return
        equity_basis = self._equity_basis(run=run, positions=positions)
        if current_day_pnl(now=self.now_fn(), positions=positions) <= -(
            equity_basis * self.settings.trade_plan_daily_loss_lock_pct / 100.0
        ):
            run.notes = [*run.notes, marker]

    def _position_market_status(self, position: PaperPosition) -> str:
        for reason in reversed(position.entry_reasons):
            if reason.startswith(MARKET_STATUS_STATE_PREFIX):
                return reason[len(MARKET_STATUS_STATE_PREFIX) :]
        return ""

    def _store_position_market_status(
        self,
        position: PaperPosition,
        market_status: str | None,
    ) -> None:
        normalized = normalize_market_status(market_status)
        position.entry_reasons = [
            reason
            for reason in position.entry_reasons
            if not reason.startswith(MARKET_STATUS_STATE_PREFIX)
        ]
        if normalized:
            position.entry_reasons.append(f"{MARKET_STATUS_STATE_PREFIX}{normalized}")

    def _entry_rule_deps(self) -> EntryRuleDeps:
        return EntryRuleDeps(
            settings=self.settings,
            fill_model=self.fill_model,
            strategy_mode=self.strategy_mode,
            bucket=self.bucket,
            predictor_score_by_symbol=self._predictor_score_by_symbol,
            now_fn=self.now_fn,
            market_date=self._market_date,
            max_open_positions=self._max_open_positions,
            position_size=self._position_size,
            daily_loss_locked=self._daily_loss_locked,
            has_tradeable_live_state=lambda row, market_phase: self._has_tradeable_live_state(
                row,
                market_phase=market_phase,
            ),
            store_position_market_status=self._store_position_market_status,
            planned_risk_pct=self._planned_risk_pct,
            stop_price=self._stop_price,
            decision_basis_price=self._decision_basis_price,
            advice_reasons=self._advice_reasons,
            order_id_factory=lambda: str(uuid.uuid4()),
        )

    def _exit_rule_deps(self) -> ExitRuleDeps:
        return ExitRuleDeps(
            settings=self.settings,
            fill_model=self.fill_model,
            strategy_mode=self.strategy_mode,
            position_market_status=self._position_market_status,
            decision_basis_price=self._decision_basis_price,
            order_id_factory=lambda: str(uuid.uuid4()),
        )

    def _profit_rule_deps(self) -> ProfitRuleDeps:
        return ProfitRuleDeps(
            settings=self.settings,
            fill_model=self.fill_model,
            strategy_mode=self.strategy_mode,
            decision_basis_price=self._decision_basis_price,
            stop_price=self._stop_price,
            order_id_factory=lambda: str(uuid.uuid4()),
        )

    def _apply_entry_rules(
        self,
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
        return apply_entry_rules(
            self._entry_rule_deps(),
            run=run,
            positions=positions,
            activity=activity,
            advice_by_symbol=advice_by_symbol,
            market_phase=market_phase,
            market_date=market_date,
            day_regime=day_regime,
            now=now,
        )

    def _apply_profit_management(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return apply_profit_management(
            self._profit_rule_deps(),
            run=run,
            positions=positions,
            activity_by_symbol=activity_by_symbol,
            market_phase=market_phase,
            day_regime=day_regime,
            now=now,
        )

    def _apply_exit_rules(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        day_regime: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return apply_exit_rules(
            self._exit_rule_deps(),
            run=run,
            positions=positions,
            activity_by_symbol=activity_by_symbol,
            market_phase=market_phase,
            day_regime=day_regime,
            now=now,
        )

    def _close_for_session_end(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        market_phase: str,
        now: datetime,
    ) -> list[PaperOrder]:
        return close_for_session_end(
            self._exit_rule_deps(),
            run=run,
            positions=positions,
            market_phase=market_phase,
            now=now,
        )

    def _close_position(
        self,
        *,
        run: PaperTradingRun,
        position: PaperPosition,
        row: MarketActivity | None,
        market_phase: str,
        reason: str,
        day_regime: str,
        now: datetime,
        analysis_label: str | None,
        analysis_score: float | None,
        reasons: list[str],
    ) -> PaperOrder:
        return close_position(
            self._exit_rule_deps(),
            run=run,
            position=position,
            row=row,
            market_phase=market_phase,
            reason=reason,
            day_regime=day_regime,
            now=now,
            analysis_label=analysis_label,
            analysis_score=analysis_score,
            reasons=reasons,
        )

    def _trim_position(
        self,
        *,
        run: PaperTradingRun,
        position: PaperPosition,
        row: MarketActivity,
        market_phase: str,
        quantity: int,
        reason: str,
        day_regime: str,
        now: datetime,
        analysis_label: str | None,
        analysis_score: float | None,
        reasons: list[str],
    ) -> PaperOrder:
        return trim_position(
            self._profit_rule_deps(),
            run=run,
            position=position,
            row=row,
            market_phase=market_phase,
            quantity=quantity,
            reason=reason,
            day_regime=day_regime,
            now=now,
            analysis_label=analysis_label,
            analysis_score=analysis_score,
            reasons=reasons,
        )

    def _mark_positions(
        self,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        now: datetime,
        *,
        day_regime: str,
    ) -> None:
        for position in positions:
            if position.status != "OPEN":
                continue
            row = activity_by_symbol.get(position.symbol)
            if row is None or row.last_price is None or row.last_price <= 0:
                continue
            position.last_price = row.last_price
            position.highest_price = max(position.highest_price or row.last_price, row.last_price)
            position.market_value = row.last_price * position.quantity
            position.unrealized_pnl = (row.last_price - position.average_entry_price) * position.quantity
            position.total_pnl = position.realized_pnl + position.unrealized_pnl
            position.stop_price = self._stop_price(position)
            position.planned_stop_price = position.stop_price
            position.planned_risk_pct = self._planned_risk_pct(
                position.average_entry_price,
                position.planned_stop_price,
            )
            if not position.day_regime and row.market_phase in {"premarket", "regular"}:
                position.day_regime = day_regime
            self._store_position_market_status(position, row.market_status)
            position.updated_at = now

    def _refresh_position_marks(self, positions: list[PaperPosition]) -> None:
        for position in positions:
            if position.status != "OPEN":
                position.market_value = 0.0
                position.unrealized_pnl = 0.0
                continue
            if position.last_price is None:
                continue
            position.market_value = position.last_price * position.quantity
            position.unrealized_pnl = (position.last_price - position.average_entry_price) * position.quantity
            position.total_pnl = position.realized_pnl + position.unrealized_pnl
            position.stop_price = self._stop_price(position)
            position.planned_stop_price = position.stop_price
            position.planned_risk_pct = self._planned_risk_pct(
                position.average_entry_price,
                position.planned_stop_price,
            )

    def _sync_run_metrics(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        market_phase: str,
        market_date: str,
        now: datetime,
        notes: list[str],
    ) -> None:
        open_positions = [row for row in positions if row.status == "OPEN"]
        closed_positions = [row for row in positions if row.status == "CLOSED"]
        gross_profit = sum(max(row.realized_pnl, 0.0) for row in closed_positions)
        gross_loss = sum(abs(min(row.realized_pnl, 0.0)) for row in closed_positions)
        wins = [row.realized_pnl for row in closed_positions if row.realized_pnl > 0]
        losses = [abs(row.realized_pnl) for row in closed_positions if row.realized_pnl < 0]

        run.realized_pnl = sum(row.realized_pnl for row in positions)
        run.unrealized_pnl = sum(row.unrealized_pnl for row in open_positions)
        run.equity = run.cash_balance + sum(row.market_value for row in open_positions)
        run.equity_peak = max(run.equity_peak, run.equity)
        drawdown_pct = (
            ((run.equity_peak - run.equity) / run.equity_peak) * 100.0
            if run.equity_peak > 0
            else 0.0
        )
        run.max_drawdown_pct = max(run.max_drawdown_pct, drawdown_pct)
        run.total_return_pct = (
            ((run.equity - run.initial_capital) / run.initial_capital) * 100.0
            if run.initial_capital > 0
            else 0.0
        )
        run.closed_trade_count = len(closed_positions)
        run.winning_trade_count = len(wins)
        run.losing_trade_count = len(losses)
        run.win_rate = (
            (run.winning_trade_count / run.closed_trade_count) * 100.0
            if run.closed_trade_count > 0
            else 0.0
        )
        run.gross_profit = gross_profit
        run.gross_loss = gross_loss
        run.total_transaction_cost = sum(row.fees_paid_total for row in positions)
        run.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float(gross_profit > 0)
        run.average_win = sum(wins) / len(wins) if wins else 0.0
        run.average_loss = sum(losses) / len(losses) if losses else 0.0
        run.reward_risk_ratio = (
            run.average_win / run.average_loss
            if run.average_loss > 0
            else float(run.average_win > 0)
        )
        run.last_phase = market_phase
        run.last_market_date = market_date
        preserved_markers = state_markers(run.notes)
        visible_notes = [note for note in notes if note not in preserved_markers]
        run.notes = preserved_markers + visible_notes[: max(0, 8 - len(preserved_markers))]
        run.updated_at = now

    def _eligible_for_entry(
        self,
        *,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        market_phase: str,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        return eligible_for_entry(
            self._entry_rule_deps(),
            row=row,
            advice=advice,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )

    def _adaptive_entry_tag(
        self,
        *,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        market_phase: str,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> str | None:
        return adaptive_entry_tag(
            self._entry_rule_deps(),
            row=row,
            advice=advice,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )

    def _eligible_for_predicted_priority_entry(
        self,
        *,
        row: MarketActivity,
    ) -> bool:
        return eligible_for_predicted_priority_entry(
            self._entry_rule_deps(),
            row=row,
        )

    def _eligible_for_live_exception_entry(
        self,
        *,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        market_phase: str,
    ) -> bool:
        return eligible_for_live_exception_entry(
            self._entry_rule_deps(),
            row=row,
            advice=advice,
            market_phase=market_phase,
        )

    def _can_add(
        self,
        position: PaperPosition,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        *,
        market_phase: str,
        now: datetime,
    ) -> bool:
        return can_add(
            self._entry_rule_deps(),
            position,
            row,
            advice,
            market_phase=market_phase,
            now=now,
        )

    def _eligible_for_baseline_entry(
        self,
        *,
        row: MarketActivity,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        return eligible_for_baseline_entry(
            self._entry_rule_deps(),
            row=row,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )

    def _can_add_baseline(self, position: PaperPosition, row: MarketActivity) -> bool:
        return can_add_baseline(
            self._entry_rule_deps(),
            position,
            row,
        )

    def _position_size(
        self,
        *,
        cash_balance: float,
        equity: float,
        price: float | None,
        fraction: float,
    ) -> int:
        if price is None or price <= 0:
            return 0
        fee_buffer = max(self.settings.paper_min_trade_fee, 0.0)
        affordable_cash = max(min(cash_balance, equity * fraction) - fee_buffer, 0.0)
        target_notional = affordable_cash / (
            1.0 + self.settings.paper_notional_fee_rate_pct / 100.0
        )
        if target_notional < price:
            return 0
        return max(int(target_notional // price), 0)

    def _has_tradeable_live_state(
        self,
        row: MarketActivity,
        *,
        market_phase: str,
    ) -> bool:
        if not row.has_live_quote:
            return False
        if not live_quote_is_valid(row.bid_price, row.ask_price):
            return False
        if row.data_age_seconds is None:
            return False
        if row.data_age_seconds > float(self.settings.trade_plan_stale_data_seconds):
            return False
        if market_status_blocks_trading(row.market_status):
            return False
        return not halt_suspected(
            row,
            market_phase=market_phase,
            halt_seconds=self.settings.trade_plan_halt_suspected_seconds,
        )

    def _planned_risk_pct(
        self,
        entry_price: float | None,
        stop_price: float | None,
    ) -> float | None:
        return planned_risk_pct(entry_price, stop_price)

    def _stop_price(self, position: PaperPosition) -> float:
        return stop_price(self.settings, self.strategy_mode, position)

    def _activity_for_symbol(
        self,
        activity: list[MarketActivity],
        symbol: str,
    ) -> MarketActivity | None:
        for row in activity:
            if row.symbol == symbol:
                return row
        return None

    def _decision_basis_price(self, position: PaperPosition) -> float:
        reference = position.fill_reference_price
        if reference is not None and reference > 0:
            return reference
        return max(position.average_entry_price, 0.0)

    def _candidate_sort_key(
        self,
        row: MarketActivity,
        *,
        advice_by_symbol: dict[str, MomentumAdvice] | None = None,
    ) -> tuple[object, ...]:
        return candidate_sort_key(
            self._entry_rule_deps(),
            row,
            advice_by_symbol=advice_by_symbol,
        )

    def _review_candidates(
        self,
        *,
        activity: list[MarketActivity],
        positions: list[PaperPosition],
        market_phase: str,
    ) -> object | None:
        if self.advisor is None:
            return None
        candidates = [
            row
            for row in sorted(activity, key=self._candidate_sort_key)
            if row.last_price is not None
            and row.last_price > 0
            and (row.pct_rank <= 5 or row.volume_rank <= 5)
        ]
        if not candidates:
            return None
        try:
            return self.advisor.review(
                market_phase=market_phase,
                candidates=candidates[: self.settings.paper_ai_consensus_limit],
                open_positions=[row for row in positions if row.status == "OPEN"],
            )
        except Exception:
            return None

    def _step_notes(
        self,
        *,
        actions: list[str],
        activity: list[MarketActivity],
        context_notes: list[str],
        advice_by_symbol: dict[str, MomentumAdvice],
    ) -> list[str]:
        notes: list[str] = []
        notes.extend(actions[:4])
        notes.extend(context_notes[:4])
        for row in sorted(
            activity,
            key=lambda candidate: self._candidate_sort_key(
                candidate,
                advice_by_symbol=advice_by_symbol,
            ),
        )[:3]:
            advice = advice_by_symbol.get(row.symbol)
            advice_note = advice.stance if advice is not None else "none"
            notes.append(
                f"focus:{row.symbol}:{row.analysis_label}:{row.analysis_score:.2f}:lp={row.leader_persistence_score:.0f}:pa={row.pullback_absorption_score:.0f}:trap={row.trap_score:.0f}:ai={advice_note}"
            )
        deduped: list[str] = []
        for note in notes:
            if note and note not in deduped:
                deduped.append(note)
        return deduped[:8]

    def _advice_reasons(self, advice: MomentumAdvice | None) -> list[str]:
        if advice is None:
            return []
        reasons = [f"gemini_{advice.stance}", f"gemini_conviction_{advice.conviction:.2f}"]
        if advice.note:
            reasons.append(f"gemini_note:{advice.note[:72]}")
        return reasons[:3]

    def _entry_reasons(
        self,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        *,
        entry_tag: str | None,
    ) -> list[str]:
        return entry_reasons(
            self._entry_rule_deps(),
            row,
            advice,
            entry_tag=entry_tag,
        )

    def _max_open_positions(self) -> int:
        if self.strategy_mode == "adaptive":
            return max(int(self.settings.paper_adaptive_max_open_positions), 1)
        return max(int(self.settings.paper_max_open_positions), 1)

    def _entry_fraction(
        self,
        *,
        row: MarketActivity,
        entry_tag: str | None,
    ) -> float:
        return entry_fraction(
            self._entry_rule_deps(),
            row=row,
            entry_tag=entry_tag,
        )

    def _refresh_predictor_scores(self) -> None:
        if self.strategy_mode != "adaptive" or not bucket_uses_predictor_weight(self.bucket):
            self._predictor_score_by_symbol = {}
            return
        rows = fetch_latest_premkt_predictions(
            self.settings.database_path,
            prefer_reportable=False,
        )
        self._predictor_score_by_symbol = {
            str(row["symbol"]).upper(): float(row["score"])
            for row in rows
        }

    def _predictor_score(self, row: MarketActivity) -> float | None:
        return predictor_score(self._entry_rule_deps(), row)

    def predictor_weight_for_score(self, score: float | None) -> float:
        return predictor_weight_for_score(score)

    def _predictor_weight(self, row: MarketActivity) -> float:
        return predictor_weight(self._entry_rule_deps(), row)

    def _entry_score_threshold(self, *, base_threshold: float, row: MarketActivity) -> float:
        return entry_score_threshold(self._entry_rule_deps(), base_threshold=base_threshold, row=row)

    def _best_rank(self, row: MarketActivity) -> int:
        return min(row.pct_rank if row.pct_rank > 0 else 9999, row.volume_rank if row.volume_rank > 0 else 9999)

    def _selector_rank(self, row: MarketActivity) -> int:
        if self.strategy_mode == "baseline_volume":
            return row.volume_rank if row.volume_rank > 0 else 9999
        return row.pct_rank if row.pct_rank > 0 else 9999

    def _market_date(self, value: datetime) -> str:
        return paper_market_date(value)

    def _default_bucket(self, strategy_name: str, strategy_mode: str) -> str:
        return default_paper_bucket(strategy_name, strategy_mode)
