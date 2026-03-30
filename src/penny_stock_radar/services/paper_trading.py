from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import uuid
from zoneinfo import ZoneInfo

from ..config import AppSettings
from ..db import (
    create_paper_trading_run,
    fetch_active_paper_trading_run,
    fetch_latest_paper_trading_run,
    fetch_latest_paper_strategy_runs,
    fetch_paper_orders,
    fetch_paper_positions,
    fetch_paper_trading_run_by_id,
    fetch_paper_run_snapshots,
    insert_paper_orders,
    insert_paper_run_snapshot,
    upsert_paper_positions,
    upsert_paper_trading_run,
)
from ..models import (
    MarketActivity,
    PaperOrder,
    PaperPosition,
    PaperRunSnapshot,
    PaperTradingRun,
)
from .market_activity import MarketActivityScanner
from .momentum_advisor import (
    MomentumAdvice,
    build_momentum_advisor,
)

EASTERN = ZoneInfo("America/New_York")
PRIMARY_PAPER_STRATEGY = "auto_paper_v1"
BASELINE_PCT_STRATEGY = "baseline_pct_leader_v1"
BASELINE_VOLUME_STRATEGY = "baseline_volume_leader_v1"
PAPER_STRATEGY_LABELS = {
    PRIMARY_PAPER_STRATEGY: "Adaptive",
    BASELINE_PCT_STRATEGY: "Pct Leader",
    BASELINE_VOLUME_STRATEGY: "Volume Leader",
}


@dataclass(slots=True)
class PaperTradingStepResult:
    run_id: str
    strategy_name: str
    market_phase: str
    actions: list[str]
    entered_count: int
    added_count: int
    exited_count: int
    open_position_count: int
    closed_trade_count: int
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    profit_factor: float
    reward_risk_ratio: float
    export_dir: Path
    comparison_path: Path | None = None


class PaperTradingEngine:
    def __init__(
        self,
        settings: AppSettings,
        strategy_name: str = PRIMARY_PAPER_STRATEGY,
        export_dir: Path | None = None,
        advisor: object | None = None,
        now_fn: Callable[[], datetime] | None = None,
        strategy_mode: str = "adaptive",
    ) -> None:
        self.settings = settings
        self.strategy_name = strategy_name
        self.strategy_mode = strategy_mode
        self.export_dir = export_dir or settings.paper_trade_dir
        if strategy_mode == "adaptive":
            self.advisor = advisor if advisor is not None else build_momentum_advisor(settings)
        else:
            self.advisor = None
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.scanner = MarketActivityScanner(settings)

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        market_phase = self.scanner.resolve_market_phase(phase)
        rows = list(activity or [])
        if not rows and market_phase in {"premarket", "regular"}:
            scan_result = self.scanner.scan(phase=market_phase, top_limit=10)
            rows = scan_result.activity
        return self.process_market_activity(
            market_phase=market_phase,
            activity=rows,
            export_csv=export_csv,
        )

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        now = self.now_fn()
        market_date = self._market_date(now)
        run = self._get_or_create_run()
        positions = fetch_paper_positions(self.settings.database_path, run.run_id)
        orders: list[PaperOrder] = []
        actions: list[str] = []
        context_notes: list[str] = []
        entered_count = 0
        added_count = 0
        exited_count = 0

        activity_by_symbol = {
            row.symbol.upper(): row
            for row in activity
            if row.symbol and row.last_price is not None and row.last_price > 0
        }

        self._mark_positions(positions, activity_by_symbol, now)

        advice_by_symbol: dict[str, MomentumAdvice] = {}
        if market_phase in {"premarket", "regular"}:
            advice_bundle = self._review_candidates(
                activity=activity,
                positions=positions,
                market_phase=market_phase,
            )
            if advice_bundle is not None:
                advice_by_symbol = advice_bundle.items
                if advice_bundle.notes:
                    context_notes.extend(advice_bundle.notes)
                    actions.append("gemini_consensus")

        if market_phase in {"premarket", "regular"}:
            exit_orders = self._apply_exit_rules(
                run=run,
                positions=positions,
                activity_by_symbol=activity_by_symbol,
                market_phase=market_phase,
                now=now,
            )
            if exit_orders:
                exited_count = sum(1 for order in exit_orders if order.intent == "EXIT")
                actions.extend([f"exit:{order.symbol}" for order in exit_orders])
                orders.extend(exit_orders)

            entry_orders = self._apply_entry_rules(
                run=run,
                positions=positions,
                activity=activity,
                advice_by_symbol=advice_by_symbol,
                market_phase=market_phase,
                market_date=market_date,
                now=now,
            )
            if entry_orders:
                entered_count = sum(1 for order in entry_orders if order.intent == "ENTRY")
                added_count = sum(1 for order in entry_orders if order.intent == "ADD")
                actions.extend(
                    [
                        f"{order.intent.lower()}:{order.symbol}"
                        for order in entry_orders
                    ]
                )
                orders.extend(entry_orders)
        else:
            close_orders = self._close_for_session_end(
                run=run,
                positions=positions,
                market_phase=market_phase,
                now=now,
            )
            if close_orders:
                exited_count = sum(1 for order in close_orders if order.intent == "EXIT")
                actions.extend([f"session_exit:{order.symbol}" for order in close_orders])
                orders.extend(close_orders)

        self._refresh_position_marks(positions)
        self._sync_run_metrics(
            run=run,
            positions=positions,
            market_phase=market_phase,
            market_date=market_date,
            now=now,
            notes=self._step_notes(
                actions=actions,
                activity=activity,
                context_notes=context_notes,
                advice_by_symbol=advice_by_symbol,
            ),
        )

        upsert_paper_trading_run(self.settings.database_path, run)
        upsert_paper_positions(self.settings.database_path, positions)
        if orders:
            insert_paper_orders(self.settings.database_path, orders)

        snapshot = PaperRunSnapshot(
            run_id=run.run_id,
            market_phase=market_phase,
            cash_balance=run.cash_balance,
            equity=run.equity,
            realized_pnl=run.realized_pnl,
            unrealized_pnl=run.unrealized_pnl,
            total_return_pct=run.total_return_pct,
            max_drawdown_pct=run.max_drawdown_pct,
            open_position_count=sum(1 for row in positions if row.status == "OPEN"),
            closed_trade_count=run.closed_trade_count,
            win_rate=run.win_rate,
            profit_factor=run.profit_factor,
            reward_risk_ratio=run.reward_risk_ratio,
            notes=self._step_notes(
                actions=actions,
                activity=activity,
                context_notes=context_notes,
                advice_by_symbol=advice_by_symbol,
            ),
            created_at=now,
        )
        insert_paper_run_snapshot(self.settings.database_path, snapshot)

        if export_csv:
            self.export_run_csv(run.run_id)

        return PaperTradingStepResult(
            run_id=run.run_id,
            strategy_name=run.strategy_name,
            market_phase=market_phase,
            actions=actions or ["hold"],
            entered_count=entered_count,
            added_count=added_count,
            exited_count=exited_count,
            open_position_count=sum(1 for row in positions if row.status == "OPEN"),
            closed_trade_count=run.closed_trade_count,
            cash_balance=run.cash_balance,
            equity=run.equity,
            realized_pnl=run.realized_pnl,
            unrealized_pnl=run.unrealized_pnl,
            profit_factor=run.profit_factor,
            reward_risk_ratio=run.reward_risk_ratio,
            export_dir=self.export_dir.resolve(),
        )

    def export_run_csv(self, run_id: str | None = None) -> Path:
        run = (
            fetch_latest_paper_trading_run(self.settings.database_path, self.strategy_name)
            if run_id is None
            else None
        )
        target_run_id = run_id or (run.run_id if run is not None else None)
        if target_run_id is None:
            return self.export_dir

        target_run = run or fetch_paper_trading_run_by_id(
            self.settings.database_path,
            target_run_id,
        )
        positions = fetch_paper_positions(self.settings.database_path, target_run_id)
        orders = fetch_paper_orders(self.settings.database_path, target_run_id)
        snapshots = fetch_paper_run_snapshots(self.settings.database_path, target_run_id)

        self.export_dir.mkdir(parents=True, exist_ok=True)
        if target_run is not None:
            self._write_csv(
                self.export_dir / "paper_run_summary.csv",
                [target_run.model_dump(mode="json")],
            )
        self._write_csv(
            self.export_dir / "paper_positions.csv",
            [row.model_dump(mode="json") for row in positions],
        )
        self._write_csv(
            self.export_dir / "paper_orders.csv",
            [row.model_dump(mode="json") for row in orders],
        )
        self._write_csv(
            self.export_dir / "paper_equity_curve.csv",
            [row.model_dump(mode="json") for row in snapshots],
        )
        return self.export_dir.resolve()

    def _get_or_create_run(self) -> PaperTradingRun:
        run = fetch_active_paper_trading_run(
            self.settings.database_path,
            self.strategy_name,
        )
        if run is not None:
            return run
        return create_paper_trading_run(
            self.settings.database_path,
            strategy_name=self.strategy_name,
            initial_capital=self.settings.paper_initial_capital,
            notes=["auto paper trading bootstrapped"],
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
        now: datetime,
    ) -> list[PaperOrder]:
        orders: list[PaperOrder] = []
        open_positions = [row for row in positions if row.status == "OPEN"]
        open_symbols = {row.symbol for row in open_positions}
        traded_today = {
            row.symbol
            for row in positions
            if self._market_date(row.opened_at) == market_date
        }

        for position in open_positions:
            row = self._activity_for_symbol(activity, position.symbol)
            if row is None:
                continue
            advice = advice_by_symbol.get(position.symbol)
            if not self._can_add(position, row, advice):
                continue
            quantity = self._position_size(
                cash_balance=run.cash_balance,
                equity=run.equity,
                price=row.last_price,
                fraction=self.settings.paper_add_size_fraction,
            )
            if quantity <= 0:
                continue
            notional = row.last_price * quantity
            run.cash_balance -= notional
            position.quantity += quantity
            position.cost_basis += notional
            position.average_entry_price = position.cost_basis / max(position.quantity, 1)
            position.add_count += 1
            position.last_price = row.last_price
            position.highest_price = max(position.highest_price or row.last_price, row.last_price)
            position.updated_at = now
            position.entry_reasons = position.entry_reasons + [f"add:{row.analysis_label.lower()}"] + self._advice_reasons(advice)
            position.stop_price = self._stop_price(position)
            position.unrealized_pnl = (row.last_price - position.average_entry_price) * position.quantity
            position.market_value = row.last_price * position.quantity
            position.total_pnl = position.realized_pnl + position.unrealized_pnl
            orders.append(
                PaperOrder(
                    order_id=str(uuid.uuid4()),
                    run_id=run.run_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    market_phase=market_phase,
                    action="BUY",
                    intent="ADD",
                    quantity=quantity,
                    price=row.last_price,
                    notional=notional,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    reasons=row.reasons[:4] + self._advice_reasons(advice),
                    created_at=now,
                )
            )

        if len([row for row in positions if row.status == "OPEN"]) >= self.settings.paper_max_open_positions:
            return orders

        candidates = [
            row
            for row in sorted(
                activity,
                key=lambda candidate: self._candidate_sort_key(
                    candidate,
                    advice_by_symbol=advice_by_symbol,
                ),
            )
            if self._eligible_for_entry(
                row=row,
                advice=advice_by_symbol.get(row.symbol),
                market_phase=market_phase,
                open_symbols=open_symbols,
                traded_today=traded_today,
            )
        ]
        slots = max(self.settings.paper_max_open_positions - len(open_symbols), 0)
        for row in candidates[:slots]:
            quantity = self._position_size(
                cash_balance=run.cash_balance,
                equity=run.equity,
                price=row.last_price,
                fraction=self.settings.paper_entry_size_fraction,
            )
            if quantity <= 0:
                continue
            notional = row.last_price * quantity
            position = PaperPosition(
                position_id=str(uuid.uuid4()),
                run_id=run.run_id,
                symbol=row.symbol,
                status="OPEN",
                entry_phase=market_phase,
                entry_label=row.analysis_label,
                quantity=quantity,
                average_entry_price=row.last_price,
                last_price=row.last_price,
                cost_basis=notional,
                market_value=notional,
                highest_price=row.last_price,
                stop_price=row.last_price * (1 - self.settings.paper_stop_loss_pct / 100.0),
                entry_reasons=row.reasons[:5] + self._advice_reasons(advice_by_symbol.get(row.symbol)),
                opened_at=now,
                updated_at=now,
            )
            positions.append(position)
            open_symbols.add(row.symbol)
            run.cash_balance -= notional
            orders.append(
                PaperOrder(
                    order_id=str(uuid.uuid4()),
                    run_id=run.run_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    market_phase=market_phase,
                    action="BUY",
                    intent="ENTRY",
                    quantity=quantity,
                    price=row.last_price,
                    notional=notional,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    reasons=row.reasons[:5] + self._advice_reasons(advice_by_symbol.get(row.symbol)),
                    created_at=now,
                )
            )
        return orders

    def _apply_exit_rules(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        market_phase: str,
        now: datetime,
    ) -> list[PaperOrder]:
        orders: list[PaperOrder] = []
        for position in positions:
            if position.status != "OPEN":
                continue
            row = activity_by_symbol.get(position.symbol)
            if row is None or position.last_price is None:
                continue
            stop_price = self._stop_price(position)
            position.stop_price = stop_price
            gain_pct = ((position.last_price - position.average_entry_price) / position.average_entry_price) * 100.0
            reason: str | None = None
            if position.last_price <= stop_price:
                reason = "trailing_stop" if gain_pct > 0 else "stop_loss"
            elif row.trap_score >= 78.0:
                reason = "trap_warning_exit" if gain_pct >= 0 else "momentum_failure"
            elif self._best_rank(row) > 5 and row.leader_persistence_score < 32.0 and gain_pct >= 1.0:
                reason = "leadership_lost"
            elif market_phase == "regular" and row.analysis_label == "NO_CHASE" and gain_pct >= 2.0:
                reason = "momentum_cooldown"
            if reason is None:
                continue
            orders.append(
                self._close_position(
                    run=run,
                    position=position,
                    market_phase=market_phase,
                    reason=reason,
                    now=now,
                    analysis_label=row.analysis_label,
                    analysis_score=row.analysis_score,
                    reasons=row.reasons[:5],
                )
            )
        return orders

    def _close_for_session_end(
        self,
        *,
        run: PaperTradingRun,
        positions: list[PaperPosition],
        market_phase: str,
        now: datetime,
    ) -> list[PaperOrder]:
        if run.last_phase not in {"premarket", "regular"}:
            return []
        orders: list[PaperOrder] = []
        for position in positions:
            if position.status != "OPEN":
                continue
            orders.append(
                self._close_position(
                    run=run,
                    position=position,
                    market_phase=market_phase,
                    reason="session_closed",
                    now=now,
                    analysis_label="SESSION_END",
                    analysis_score=None,
                    reasons=["session_closed"],
                )
            )
        return orders

    def _close_position(
        self,
        *,
        run: PaperTradingRun,
        position: PaperPosition,
        market_phase: str,
        reason: str,
        now: datetime,
        analysis_label: str | None,
        analysis_score: float | None,
        reasons: list[str],
    ) -> PaperOrder:
        price = position.last_price or position.average_entry_price
        notional = price * position.quantity
        realized_pnl = (price - position.average_entry_price) * position.quantity
        realized_pnl_pct = (
            ((price - position.average_entry_price) / position.average_entry_price) * 100.0
            if position.average_entry_price > 0
            else 0.0
        )
        run.cash_balance += notional
        position.status = "CLOSED"
        position.exit_reason = reason
        position.exit_reasons = position.exit_reasons + [reason] + reasons[:2]
        position.realized_pnl = realized_pnl
        position.unrealized_pnl = 0.0
        position.total_pnl = realized_pnl
        position.market_value = 0.0
        position.closed_at = now
        position.updated_at = now
        return PaperOrder(
            order_id=str(uuid.uuid4()),
            run_id=run.run_id,
            position_id=position.position_id,
            symbol=position.symbol,
            market_phase=market_phase,
            action="SELL",
            intent="EXIT",
            quantity=position.quantity,
            price=price,
            notional=notional,
            analysis_label=analysis_label,
            analysis_score=analysis_score,
            reasons=[reason] + reasons[:4],
            created_at=now,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
        )

    def _mark_positions(
        self,
        positions: list[PaperPosition],
        activity_by_symbol: dict[str, MarketActivity],
        now: datetime,
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

        run.realized_pnl = sum(row.realized_pnl for row in closed_positions)
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
        run.notes = notes[:8]
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
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        if self.strategy_mode != "adaptive":
            return self._eligible_for_baseline_entry(
                row=row,
                open_symbols=open_symbols,
                traded_today=traded_today,
            )
        if advice is not None and advice.stance == "avoid":
            return False
        if row.behavioral_score > 0 and row.behavioral_score < 40.0:
            return False
        if row.trap_score > 0 and row.trap_score >= 72.0:
            return False
        reclaim_candidate = (
            row.analysis_label == "WAIT_PULLBACK"
            and row.behavioral_score >= 62.0
            and ("leader_reclaim" in row.reasons or "reclaim_entry_ready" in row.reasons)
        )
        if row.analysis_label == "NEWS_CHECK_FIRST":
            if not self._eligible_news_entry(row=row, advice=advice, market_phase=market_phase):
                return False
        elif not reclaim_candidate and row.analysis_score < self.settings.paper_entry_score_min:
            return False
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            return False
        if row.dollar_volume is not None and row.dollar_volume < self.settings.premarket_min_dollar_volume:
            return False
        allowed_labels = {
            "premarket": {"OPENING_RANGE_CANDIDATE"},
            "regular": {"CONDITIONAL_ENTRY"},
        }
        if self.settings.supports_news_driven_entries():
            allowed_labels["premarket"].add("NEWS_CHECK_FIRST")
            allowed_labels["regular"].add("NEWS_CHECK_FIRST")
        if row.analysis_label not in allowed_labels.get(market_phase, set()) and not reclaim_candidate:
            return False
        best_rank = self._best_rank(row)
        return (
            best_rank <= 5
            or row.predicted
            or row.leader_persistence_score >= 58.0
            or row.pullback_absorption_score >= 62.0
            or (advice is not None and advice.stance == "buy" and advice.conviction >= 0.55)
        )

    def _eligible_news_entry(
        self,
        *,
        row: MarketActivity,
        advice: MomentumAdvice | None,
        market_phase: str,
    ) -> bool:
        if not self.settings.supports_news_driven_entries():
            return False
        if row.analysis_score < self.settings.paper_news_entry_score_min:
            return False
        if market_phase not in {"premarket", "regular"}:
            return False
        if row.pct_change is not None and row.pct_change < 20:
            return False
        leader = self._best_rank(row) <= 5
        if not leader:
            return False
        if row.dollar_volume is not None and row.dollar_volume < self.settings.premarket_min_dollar_volume * 3:
            return False
        if (
            max(row.leader_persistence_score, row.pullback_absorption_score) > 0
            and row.leader_persistence_score < 52.0
            and row.pullback_absorption_score < 60.0
        ):
            return False
        if row.trap_score > 0 and row.trap_score >= 58.0:
            return False
        return advice is not None and advice.stance == "buy" and advice.conviction >= 0.55

    def _can_add(
        self,
        position: PaperPosition,
        row: MarketActivity,
        advice: MomentumAdvice | None,
    ) -> bool:
        if position.add_count >= self.settings.paper_max_adds_per_position:
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        if self.strategy_mode != "adaptive":
            return self._can_add_baseline(position, row)
        if advice is not None and advice.stance == "avoid":
            return False
        if row.analysis_label not in {"OPENING_RANGE_CANDIDATE", "CONDITIONAL_ENTRY"}:
            return False
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            return False
        if row.trap_score > 0 and row.trap_score >= 52.0:
            return False
        if (
            max(row.leader_persistence_score, row.pullback_absorption_score) > 0
            and row.leader_persistence_score < 60.0
            and row.pullback_absorption_score < 65.0
        ):
            return False
        gain_pct = ((row.last_price - position.average_entry_price) / position.average_entry_price) * 100.0
        return gain_pct >= self.settings.paper_add_trigger_pct and (
            self._best_rank(row) <= 3
            or row.behavioral_score >= 68.0
            or (advice is not None and advice.stance == "buy" and advice.conviction >= 0.65)
        )

    def _eligible_for_baseline_entry(
        self,
        *,
        row: MarketActivity,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            return False
        if row.dollar_volume is not None and row.dollar_volume < self.settings.premarket_min_dollar_volume:
            return False
        if row.trap_score > 0 and row.trap_score >= 76.0:
            return False
        if (row.pct_change or 0.0) < 8.0:
            return False
        selector_rank = self._selector_rank(row)
        return selector_rank <= 5

    def _can_add_baseline(self, position: PaperPosition, row: MarketActivity) -> bool:
        if position.add_count >= self.settings.paper_max_adds_per_position:
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            return False
        if row.trap_score > 0 and row.trap_score >= 55.0:
            return False
        gain_pct = ((row.last_price - position.average_entry_price) / position.average_entry_price) * 100.0
        return gain_pct >= self.settings.paper_add_trigger_pct and self._selector_rank(row) <= 2

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
        target_notional = min(cash_balance, equity * fraction)
        if target_notional < price:
            return 0
        return max(int(target_notional // price), 0)

    def _stop_price(self, position: PaperPosition) -> float:
        base_stop = position.average_entry_price * (1 - self.settings.paper_stop_loss_pct / 100.0)
        if (
            position.highest_price is not None
            and position.highest_price
            >= position.average_entry_price
            * (1 + self.settings.paper_profit_activation_pct / 100.0)
        ):
            trail_stop = position.highest_price * (
                1 - self.settings.paper_trailing_stop_pct / 100.0
            )
            return max(base_stop, trail_stop)
        return base_stop

    def _activity_for_symbol(
        self,
        activity: list[MarketActivity],
        symbol: str,
    ) -> MarketActivity | None:
        for row in activity:
            if row.symbol == symbol:
                return row
        return None

    def _candidate_sort_key(
        self,
        row: MarketActivity,
        *,
        advice_by_symbol: dict[str, MomentumAdvice] | None = None,
    ) -> tuple[int, int, int, float, float, float, float, str]:
        if self.strategy_mode == "baseline_pct":
            return (
                row.pct_rank if row.pct_rank > 0 else 9999,
                row.volume_rank if row.volume_rank > 0 else 9999,
                -float(row.pct_change or 0.0),
                -(row.dollar_volume or 0.0),
                row.symbol,
            )
        if self.strategy_mode == "baseline_volume":
            return (
                row.volume_rank if row.volume_rank > 0 else 9999,
                row.pct_rank if row.pct_rank > 0 else 9999,
                -(row.dollar_volume or 0.0),
                -float(row.pct_change or 0.0),
                row.symbol,
            )
        advice = advice_by_symbol.get(row.symbol) if advice_by_symbol else None
        stance_rank = {"buy": 0, "watch": 1, "avoid": 2}.get(
            advice.stance if advice is not None else "watch",
            1,
        )
        return (
            stance_rank,
            self._best_rank(row),
            row.pct_rank if row.pct_rank > 0 else 9999,
            row.volume_rank if row.volume_rank > 0 else 9999,
            -float(row.behavioral_score),
            -float(row.leader_persistence_score),
            float(row.trap_score),
            -float(row.pct_change or 0.0),
            -(row.dollar_volume or 0.0),
            -float(row.analysis_score),
            -(advice.conviction if advice is not None else 0.0),
            row.symbol,
        )

    def _focus_notes(self, activity: list[MarketActivity]) -> list[str]:
        rows = sorted(activity, key=self._candidate_sort_key)[:3]
        return [
            f"focus:{row.symbol}:{row.analysis_label}:{row.analysis_score:.2f}:lp={row.leader_persistence_score:.0f}:trap={row.trap_score:.0f}"
            for row in rows
        ]

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

    def _best_rank(self, row: MarketActivity) -> int:
        candidates = [value for value in (row.pct_rank, row.volume_rank) if value and value > 0]
        return min(candidates) if candidates else 9999

    def _selector_rank(self, row: MarketActivity) -> int:
        if self.strategy_mode == "baseline_volume":
            return row.volume_rank if row.volume_rank > 0 else 9999
        return row.pct_rank if row.pct_rank > 0 else 9999

    def _market_date(self, value: datetime) -> str:
        return value.astimezone(EASTERN).date().isoformat()

    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: self._csv_value(row.get(key))
                        for key in fieldnames
                    }
                )

    def _csv_value(self, value: object) -> object:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return value


class PaperTradingCoordinator:
    def __init__(
        self,
        settings: AppSettings,
        *,
        export_dir: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        advisor: object | None = None,
    ) -> None:
        self.settings = settings
        self.export_dir = export_dir or settings.paper_trade_dir
        self.strategy_name = PRIMARY_PAPER_STRATEGY
        self.primary_engine = PaperTradingEngine(
            settings,
            strategy_name=PRIMARY_PAPER_STRATEGY,
            export_dir=self.export_dir,
            advisor=advisor,
            now_fn=now_fn,
            strategy_mode="adaptive",
        )
        self.baseline_engines = [
            PaperTradingEngine(
                settings,
                strategy_name=BASELINE_PCT_STRATEGY,
                export_dir=self.export_dir,
                advisor=None,
                now_fn=now_fn,
                strategy_mode="baseline_pct",
            ),
            PaperTradingEngine(
                settings,
                strategy_name=BASELINE_VOLUME_STRATEGY,
                export_dir=self.export_dir,
                advisor=None,
                now_fn=now_fn,
                strategy_mode="baseline_volume",
            ),
        ]

    def run_once(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        market_phase = self.primary_engine.scanner.resolve_market_phase(phase)
        rows = list(activity or [])
        if not rows and market_phase in {"premarket", "regular"}:
            scan_result = self.primary_engine.scanner.scan(phase=market_phase, top_limit=10)
            rows = scan_result.activity
        return self.process_market_activity(
            market_phase=market_phase,
            activity=rows,
            export_csv=export_csv,
        )

    def process_market_activity(
        self,
        *,
        market_phase: str,
        activity: list[MarketActivity],
        export_csv: bool = True,
    ) -> PaperTradingStepResult:
        primary_result = self.primary_engine.process_market_activity(
            market_phase=market_phase,
            activity=activity,
            export_csv=export_csv,
        )
        for engine in self.baseline_engines:
            engine.process_market_activity(
                market_phase=market_phase,
                activity=activity,
                export_csv=False,
            )
        comparison_path = self.export_strategy_comparison()
        primary_result.comparison_path = comparison_path
        return primary_result

    def export_strategy_comparison(self) -> Path:
        runs = fetch_latest_paper_strategy_runs(self.settings.database_path, limit=10)
        rows: list[dict[str, object]] = []
        for run in runs:
            rows.append(
                {
                    "strategy_name": run.strategy_name,
                    "strategy_label": PAPER_STRATEGY_LABELS.get(run.strategy_name, run.strategy_name),
                    "status": run.status,
                    "equity": run.equity,
                    "realized_pnl": run.realized_pnl,
                    "unrealized_pnl": run.unrealized_pnl,
                    "total_return_pct": run.total_return_pct,
                    "closed_trade_count": run.closed_trade_count,
                    "win_rate": run.win_rate,
                    "profit_factor": run.profit_factor,
                    "reward_risk_ratio": run.reward_risk_ratio,
                    "max_drawdown_pct": run.max_drawdown_pct,
                    "updated_at": run.updated_at.isoformat(),
                }
            )
        self.primary_engine._write_csv(self.export_dir / "paper_strategy_comparison.csv", rows)
        return (self.export_dir / "paper_strategy_comparison.csv").resolve()
