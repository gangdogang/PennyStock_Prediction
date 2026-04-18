from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from ...config import AppSettings
from ...models import MarketActivity, PaperOrder, PaperPosition, PaperTradingRun
from ..fill_model import FillModel
from ..momentum_advisor import MomentumAdvice
from ..paper_runtime import MOMENTUM_ONLY_BUCKET
from ..trading_support import (
    best_live_rank,
    current_open_risk,
    is_premarket_entry_window,
    is_winner_add_window,
)


@dataclass(frozen=True, slots=True)
class EntryRuleDeps:
    settings: AppSettings
    fill_model: FillModel
    strategy_mode: str
    bucket: str
    predictor_score_by_symbol: Mapping[str, float]
    now_fn: Callable[[], datetime]
    market_date: Callable[[datetime], str]
    max_open_positions: Callable[[], int]
    position_size: Callable[..., int]
    daily_loss_locked: Callable[..., bool]
    has_tradeable_live_state: Callable[[MarketActivity, str], bool]
    store_position_market_status: Callable[[PaperPosition, str | None], None]
    planned_risk_pct: Callable[[float | None, float | None], float | None]
    stop_price: Callable[[PaperPosition], float]
    decision_basis_price: Callable[[PaperPosition], float]
    advice_reasons: Callable[[MomentumAdvice | None], list[str]]
    order_id_factory: Callable[[], str]


def apply_entry_rules(
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
    orders: list[PaperOrder] = []
    open_positions = [row for row in positions if row.status == "OPEN"]
    open_symbols = {row.symbol for row in open_positions}
    traded_today = {
        row.symbol
        for row in positions
        if deps.market_date(row.opened_at) == market_date
    }
    equity_basis = _equity_basis(run=run, positions=positions)
    is_daily_loss_locked = deps.daily_loss_locked(
        run=run,
        positions=positions,
        market_date=market_date,
        equity_basis=equity_basis,
        now=now,
    )
    risk_cap = equity_basis * deps.settings.trade_plan_max_concurrent_open_risk_pct / 100.0
    tracked_open_risk = current_open_risk(open_positions)
    concurrent_risk_locked = tracked_open_risk >= risk_cap
    if is_daily_loss_locked or concurrent_risk_locked:
        return []

    for position in open_positions:
        row = activity_for_symbol(activity, position.symbol)
        if row is None:
            continue
        advice = advice_by_symbol.get(position.symbol)
        if not can_add(
            deps,
            position,
            row,
            advice,
            market_phase=market_phase,
            now=now,
        ):
            continue
        reference_price = row.ask_price if row.ask_price is not None and row.ask_price > 0 else row.last_price
        requested_quantity = deps.position_size(
            cash_balance=run.cash_balance,
            equity=run.equity,
            price=reference_price,
            fraction=deps.settings.paper_add_size_fraction,
        )
        fill = deps.fill_model.buy(
            row,
            market_phase=market_phase,
            requested_quantity=requested_quantity,
        )
        quantity = fill.quantity
        remaining_quantity = fill.remaining_quantity
        fill_status = fill.fill_status
        fill_price = fill.fill_price
        fill_reference_price = fill.fill_reference_price
        fill_slippage_pct = fill.fill_slippage_pct
        transaction_cost = fill.transaction_cost
        if quantity <= 0 or fill_price is None:
            continue
        existing_risk = current_open_risk([position])
        candidate_position = position.model_copy(deep=True)
        candidate_position.quantity += quantity
        candidate_position.cost_basis += fill_price * quantity + transaction_cost
        candidate_position.average_entry_price = candidate_position.cost_basis / max(
            candidate_position.quantity,
            1,
        )
        candidate_position.last_price = row.last_price
        candidate_position.stop_price = deps.stop_price(candidate_position)
        candidate_position.planned_stop_price = candidate_position.stop_price
        incremental_risk = max(
            current_open_risk([candidate_position]) - existing_risk,
            0.0,
        )
        if tracked_open_risk + incremental_risk > risk_cap:
            continue
        notional = fill_price * quantity
        run.cash_balance -= notional + transaction_cost
        position.quantity += quantity
        position.cost_basis += notional + transaction_cost
        position.average_entry_price = position.cost_basis / max(position.quantity, 1)
        position.add_count += 1
        position.fees_paid_total += transaction_cost
        position.last_price = row.last_price
        position.highest_price = max(
            position.highest_price or row.last_price or fill_price,
            row.last_price or fill_price,
        )
        position.updated_at = now
        position.entry_reasons = (
            position.entry_reasons
            + ["winner_add", f"add:{row.analysis_label.lower()}"]
            + deps.advice_reasons(advice)
        )
        deps.store_position_market_status(position, row.market_status)
        position.stop_price = deps.stop_price(position)
        position.planned_stop_price = position.stop_price
        position.planned_risk_pct = deps.planned_risk_pct(
            position.average_entry_price,
            position.planned_stop_price,
        )
        position.unrealized_pnl = (row.last_price - position.average_entry_price) * position.quantity
        position.market_value = row.last_price * position.quantity
        position.total_pnl = position.realized_pnl + position.unrealized_pnl
        orders.append(
            PaperOrder(
                order_id=deps.order_id_factory(),
                run_id=run.run_id,
                position_id=position.position_id,
                symbol=position.symbol,
                market_phase=market_phase,
                action="BUY",
                intent="ADD",
                quantity=quantity,
                requested_quantity=requested_quantity,
                remaining_quantity=remaining_quantity,
                fill_status=fill_status,
                price=fill_price,
                notional=notional,
                transaction_cost=transaction_cost,
                strategy_bucket="winner_add",
                analysis_label=row.analysis_label,
                analysis_score=row.analysis_score,
                planned_stop_price=position.planned_stop_price,
                planned_risk_pct=position.planned_risk_pct,
                fill_reference_price=fill_reference_price,
                fill_slippage_pct=fill_slippage_pct,
                day_regime=day_regime,
                watchlist_rank_at_entry=position.watchlist_rank_at_entry,
                reasons=(
                    row.reasons[:4]
                    + (["partial_fill_cap"] if remaining_quantity > 0 else [])
                    + deps.advice_reasons(advice)
                ),
                created_at=now,
            )
        )
        tracked_open_risk += incremental_risk
        run.total_transaction_cost += transaction_cost

    if len([row for row in positions if row.status == "OPEN"]) >= deps.max_open_positions():
        return orders

    slots = max(deps.max_open_positions() - len(open_symbols), 0)
    if deps.strategy_mode == "adaptive":
        tagged_candidates = adaptive_entry_candidates(
            deps,
            activity=activity,
            advice_by_symbol=advice_by_symbol,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )
    else:
        tagged_candidates = [
            (row, None)
            for row in sorted(
                activity,
                key=lambda candidate: candidate_sort_key(
                    deps,
                    candidate,
                    advice_by_symbol=advice_by_symbol,
                ),
            )
            if eligible_for_entry(
                deps,
                row=row,
                advice=advice_by_symbol.get(row.symbol),
                market_phase=market_phase,
                open_symbols=open_symbols,
                traded_today=traded_today,
            )
        ]

    for row, entry_tag in tagged_candidates[:slots]:
        advice = advice_by_symbol.get(row.symbol)
        reasons = entry_reasons(deps, row, advice, entry_tag=entry_tag)
        fraction = entry_fraction(deps, row=row, entry_tag=entry_tag)
        reference_price = row.ask_price if row.ask_price is not None and row.ask_price > 0 else row.last_price
        requested_quantity = deps.position_size(
            cash_balance=run.cash_balance,
            equity=run.equity,
            price=reference_price,
            fraction=fraction,
        )
        fill = deps.fill_model.buy(
            row,
            market_phase=market_phase,
            requested_quantity=requested_quantity,
        )
        quantity = fill.quantity
        remaining_quantity = fill.remaining_quantity
        fill_status = fill.fill_status
        fill_price = fill.fill_price
        fill_reference_price = fill.fill_reference_price
        fill_slippage_pct = fill.fill_slippage_pct
        transaction_cost = fill.transaction_cost
        if quantity <= 0 or fill_price is None:
            continue
        notional = fill_price * quantity
        strategy_bucket = entry_tag or ""
        planned_stop_price = fill_price * (1 - deps.settings.paper_stop_loss_pct / 100.0)
        order_risk = quantity * max(fill_price - planned_stop_price, 0.0)
        if tracked_open_risk + order_risk > risk_cap:
            continue
        position = PaperPosition(
            position_id=deps.order_id_factory(),
            run_id=run.run_id,
            symbol=row.symbol,
            status="OPEN",
            entry_phase=market_phase,
            entry_label=row.analysis_label,
            quantity=quantity,
            average_entry_price=(notional + transaction_cost) / max(quantity, 1),
            last_price=row.last_price,
            cost_basis=notional + transaction_cost,
            market_value=notional,
            stop_price=planned_stop_price,
            planned_stop_price=planned_stop_price,
            planned_risk_pct=deps.planned_risk_pct(fill_price, planned_stop_price),
            highest_price=row.last_price or fill_price,
            strategy_bucket=strategy_bucket,
            fill_reference_price=fill_reference_price,
            fill_slippage_pct=fill_slippage_pct,
            fees_paid_total=transaction_cost,
            day_regime=day_regime,
            watchlist_rank_at_entry=row.watchlist_rank,
            entry_reasons=reasons + (["partial_fill_cap"] if remaining_quantity > 0 else []),
            opened_at=now,
            updated_at=now,
        )
        deps.store_position_market_status(position, row.market_status)
        positions.append(position)
        open_symbols.add(row.symbol)
        run.cash_balance -= notional + transaction_cost
        orders.append(
            PaperOrder(
                order_id=deps.order_id_factory(),
                run_id=run.run_id,
                position_id=position.position_id,
                symbol=position.symbol,
                market_phase=market_phase,
                action="BUY",
                intent="ENTRY",
                quantity=quantity,
                requested_quantity=requested_quantity,
                remaining_quantity=remaining_quantity,
                fill_status=fill_status,
                price=fill_price,
                notional=notional,
                transaction_cost=transaction_cost,
                strategy_bucket=strategy_bucket,
                analysis_label=row.analysis_label,
                analysis_score=row.analysis_score,
                planned_stop_price=position.planned_stop_price,
                planned_risk_pct=position.planned_risk_pct,
                fill_reference_price=fill_reference_price,
                fill_slippage_pct=fill_slippage_pct,
                day_regime=day_regime,
                watchlist_rank_at_entry=row.watchlist_rank,
                reasons=reasons + (["partial_fill_cap"] if remaining_quantity > 0 else []),
                created_at=now,
            )
        )
        tracked_open_risk += order_risk
        run.total_transaction_cost += transaction_cost
    return orders


def adaptive_entry_candidates(
    deps: EntryRuleDeps,
    *,
    activity: list[MarketActivity],
    advice_by_symbol: dict[str, MomentumAdvice],
    market_phase: str,
    open_symbols: set[str],
    traded_today: set[str],
) -> list[tuple[MarketActivity, str | None]]:
    candidates: list[tuple[MarketActivity, str | None]] = []
    for row in activity:
        tag = adaptive_entry_tag(
            deps,
            row=row,
            advice=advice_by_symbol.get(row.symbol),
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )
        if tag is None:
            continue
        candidates.append((row, tag))
    return sorted(
        candidates,
        key=lambda item: adaptive_entry_sort_key(
            deps,
            item[0],
            entry_tag=item[1],
            advice_by_symbol=advice_by_symbol,
        ),
    )


def adaptive_entry_sort_key(
    deps: EntryRuleDeps,
    row: MarketActivity,
    *,
    entry_tag: str | None,
    advice_by_symbol: dict[str, MomentumAdvice],
) -> tuple[object, ...]:
    if entry_tag == "predicted_starter":
        return (
            0,
            row.watchlist_rank if row.watchlist_rank is not None else 9999,
            best_live_rank(row),
            -float(row.analysis_score),
            -(row.dollar_volume or 0.0),
            row.symbol,
        )
    return (
        1,
        *candidate_sort_key(deps, row, advice_by_symbol=advice_by_symbol),
    )


def eligible_for_entry(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    market_phase: str,
    open_symbols: set[str],
    traded_today: set[str],
) -> bool:
    if deps.strategy_mode != "adaptive":
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        return eligible_for_baseline_entry(
            deps,
            row=row,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )
    return (
        adaptive_entry_tag(
            deps,
            row=row,
            advice=advice,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        )
        is not None
    )


def adaptive_entry_tag(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    market_phase: str,
    open_symbols: set[str],
    traded_today: set[str],
) -> str | None:
    if market_phase != "premarket" or not is_premarket_entry_window(deps.now_fn()):
        return None
    if not passes_adaptive_guardrails(
        deps,
        row=row,
        open_symbols=open_symbols,
        traded_today=traded_today,
    ):
        return None
    if eligible_for_predicted_priority_entry(deps, row=row):
        return "predicted_starter"
    if eligible_for_live_exception_entry(
        deps,
        row=row,
        advice=advice,
        market_phase=market_phase,
    ):
        return "live_exception"
    return None


def passes_adaptive_guardrails(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    open_symbols: set[str],
    traded_today: set[str],
) -> bool:
    if row.symbol in open_symbols or row.symbol in traded_today:
        return False
    if row.last_price is None or row.last_price <= 0:
        return False
    if not deps.has_tradeable_live_state(row, row.market_phase):
        return False
    if row.behavioral_score > 0 and row.behavioral_score < 40.0:
        return False
    if row.trap_score > 0 and row.trap_score >= 72.0:
        return False
    if row.spread_pct is not None and row.spread_pct > deps.settings.premarket_max_spread_pct:
        return False
    if row.dollar_volume is not None and row.dollar_volume < deps.settings.premarket_min_dollar_volume:
        return False
    return True


def eligible_for_predicted_priority_entry(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
) -> bool:
    if not row.predicted:
        return False
    if row.analysis_label not in {
        "OPENING_RANGE_CANDIDATE",
        "CONDITIONAL_ENTRY",
        "WAIT_PULLBACK",
        "NEWS_CHECK_FIRST",
    }:
        return False
    score_min = (
        deps.settings.paper_news_entry_score_min
        if row.analysis_label == "NEWS_CHECK_FIRST"
        else deps.settings.paper_entry_score_min
    )
    return row.analysis_score >= entry_score_threshold(
        deps,
        base_threshold=score_min,
        row=row,
    )


def eligible_for_live_exception_entry(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    market_phase: str,
) -> bool:
    if row.predicted or best_rank(row) > 3:
        return False
    if advice is not None and advice.stance == "avoid":
        return False
    reclaim_candidate = (
        row.analysis_label == "WAIT_PULLBACK"
        and row.behavioral_score >= 62.0
        and ("leader_reclaim" in row.reasons or "reclaim_entry_ready" in row.reasons)
    )
    if row.analysis_label == "NEWS_CHECK_FIRST":
        if not eligible_news_entry(deps, row=row, advice=advice, market_phase=market_phase):
            return False
    elif not reclaim_candidate and row.analysis_score < entry_score_threshold(
        deps,
        base_threshold=deps.settings.paper_entry_score_min,
        row=row,
    ):
        return False
    if row.spread_pct is not None and row.spread_pct > deps.settings.premarket_max_spread_pct:
        return False
    if row.dollar_volume is not None and row.dollar_volume < deps.settings.premarket_min_dollar_volume:
        return False
    allowed_labels = {
        "premarket": {"OPENING_RANGE_CANDIDATE"},
        "regular": {"CONDITIONAL_ENTRY"},
    }
    if deps.settings.supports_news_driven_entries():
        allowed_labels["premarket"].add("NEWS_CHECK_FIRST")
        allowed_labels["regular"].add("NEWS_CHECK_FIRST")
    if row.analysis_label not in allowed_labels.get(market_phase, set()) and not reclaim_candidate:
        return False
    return True


def eligible_news_entry(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    market_phase: str,
) -> bool:
    if not deps.settings.supports_news_driven_entries():
        return False
    if row.analysis_score < deps.settings.paper_news_entry_score_min:
        return False
    if market_phase not in {"premarket", "regular"}:
        return False
    if row.pct_change is not None and row.pct_change < 20:
        return False
    leader = best_rank(row) <= 5
    if not leader:
        return False
    if row.dollar_volume is not None and row.dollar_volume < deps.settings.premarket_min_dollar_volume * 3:
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


def can_add(
    deps: EntryRuleDeps,
    position: PaperPosition,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    *,
    market_phase: str,
    now: datetime,
) -> bool:
    if position.add_count >= deps.settings.paper_max_adds_per_position:
        return False
    if position.partial_exit_count > 0:
        return False
    if row.last_price is None or row.last_price <= 0:
        return False
    if not deps.has_tradeable_live_state(row, market_phase):
        return False
    if deps.strategy_mode != "adaptive":
        return can_add_baseline(deps, position, row)
    if market_phase != "regular" or not is_winner_add_window(now):
        return False
    if position.strategy_bucket != "predicted_starter":
        return False
    if advice is not None and advice.stance == "avoid":
        return False
    if row.analysis_label not in {"OPENING_RANGE_CANDIDATE", "CONDITIONAL_ENTRY"}:
        return False
    if row.spread_pct is not None and row.spread_pct > deps.settings.premarket_max_spread_pct:
        return False
    if row.trap_score > 0 and row.trap_score >= 52.0:
        return False
    if (
        max(row.leader_persistence_score, row.pullback_absorption_score) > 0
        and row.leader_persistence_score < 60.0
        and row.pullback_absorption_score < 65.0
    ):
        return False
    decision_basis = deps.decision_basis_price(position)
    gain_pct = (
        ((row.last_price - decision_basis) / decision_basis) * 100.0
        if decision_basis > 0
        else 0.0
    )
    return gain_pct >= deps.settings.paper_add_trigger_pct and (
        best_rank(row) <= 3
        or row.behavioral_score >= 68.0
        or (advice is not None and advice.stance == "buy" and advice.conviction >= 0.65)
    )


def eligible_for_baseline_entry(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    open_symbols: set[str],
    traded_today: set[str],
) -> bool:
    if row.symbol in open_symbols or row.symbol in traded_today:
        return False
    if row.last_price is None or row.last_price <= 0:
        return False
    if not deps.has_tradeable_live_state(row, row.market_phase):
        return False
    if row.spread_pct is not None and row.spread_pct > deps.settings.premarket_max_spread_pct:
        return False
    if row.dollar_volume is not None and row.dollar_volume < deps.settings.premarket_min_dollar_volume:
        return False
    if row.trap_score > 0 and row.trap_score >= 76.0:
        return False
    if (row.pct_change or 0.0) < 8.0:
        return False
    return selector_rank(deps, row) <= 5


def can_add_baseline(
    deps: EntryRuleDeps,
    position: PaperPosition,
    row: MarketActivity,
) -> bool:
    if position.add_count >= deps.settings.paper_max_adds_per_position:
        return False
    if row.last_price is None or row.last_price <= 0:
        return False
    if not deps.has_tradeable_live_state(row, row.market_phase):
        return False
    if row.spread_pct is not None and row.spread_pct > deps.settings.premarket_max_spread_pct:
        return False
    if row.trap_score > 0 and row.trap_score >= 55.0:
        return False
    decision_basis = deps.decision_basis_price(position)
    gain_pct = (
        ((row.last_price - decision_basis) / decision_basis) * 100.0
        if decision_basis > 0
        else 0.0
    )
    return gain_pct >= deps.settings.paper_add_trigger_pct and selector_rank(deps, row) <= 2


def candidate_sort_key(
    deps: EntryRuleDeps,
    row: MarketActivity,
    *,
    advice_by_symbol: dict[str, MomentumAdvice] | None = None,
) -> tuple[object, ...]:
    if deps.strategy_mode == "baseline_pct":
        return (
            row.pct_rank if row.pct_rank > 0 else 9999,
            row.volume_rank if row.volume_rank > 0 else 9999,
            -float(row.pct_change or 0.0),
            -(row.dollar_volume or 0.0),
            row.symbol,
        )
    if deps.strategy_mode == "baseline_volume":
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
        best_rank(row),
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


def entry_reasons(
    deps: EntryRuleDeps,
    row: MarketActivity,
    advice: MomentumAdvice | None,
    *,
    entry_tag: str | None,
) -> list[str]:
    reasons: list[str] = []
    score = predictor_score(deps, row)
    weight = predictor_weight(deps, row)
    if entry_tag:
        reasons.append(entry_tag)
        if entry_tag == "predicted_starter":
            reasons.append("predicted_priority_entry")
        elif entry_tag == "live_exception":
            reasons.append("live_exception_entry")
    if score is not None:
        reasons.append(f"predictor_score:{score:.1f}")
        reasons.append(f"predictor_weight:{weight:.2f}")
    reasons.extend(row.reasons[:5])
    reasons.extend(deps.advice_reasons(advice))
    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in deduped:
            deduped.append(reason)
    return deduped


def entry_fraction(
    deps: EntryRuleDeps,
    *,
    row: MarketActivity,
    entry_tag: str | None,
) -> float:
    if deps.strategy_mode != "adaptive":
        return deps.settings.paper_entry_size_fraction
    if entry_tag == "live_exception":
        base_fraction = deps.settings.paper_adaptive_live_exception_entry_size_fraction
    elif row.watchlist_rank is not None and row.watchlist_rank <= 2:
        base_fraction = deps.settings.paper_adaptive_predicted_entry_size_fraction
    elif row.watchlist_rank is not None and row.watchlist_rank <= 4:
        base_fraction = max(deps.settings.paper_adaptive_predicted_entry_size_fraction - 0.01, 0.01)
    else:
        base_fraction = max(deps.settings.paper_adaptive_predicted_entry_size_fraction - 0.02, 0.01)
    scaled_fraction = base_fraction * (
        1.0 + deps.settings.paper_predictor_weight_k2 * predictor_weight(deps, row)
    )
    return min(scaled_fraction, deps.settings.paper_entry_size_fraction)


def predictor_score(deps: EntryRuleDeps, row: MarketActivity) -> float | None:
    if deps.bucket == MOMENTUM_ONLY_BUCKET:
        return None
    return deps.predictor_score_by_symbol.get(row.symbol.upper())


def predictor_weight_for_score(score: float | None) -> float:
    if score is None or score <= 40.0:
        return 0.0
    if score >= 80.0:
        return 1.0
    return (float(score) - 40.0) / 40.0


def predictor_weight(deps: EntryRuleDeps, row: MarketActivity) -> float:
    if deps.bucket == MOMENTUM_ONLY_BUCKET:
        return 0.0
    if not row.predicted:
        return 0.0
    return predictor_weight_for_score(predictor_score(deps, row))


def entry_score_threshold(
    deps: EntryRuleDeps,
    *,
    base_threshold: float,
    row: MarketActivity,
) -> float:
    adjusted = base_threshold - (
        deps.settings.paper_predictor_weight_k1 * predictor_weight(deps, row)
    )
    return max(adjusted, 0.0)


def best_rank(row: MarketActivity) -> int:
    return best_live_rank(row)


def selector_rank(deps: EntryRuleDeps, row: MarketActivity) -> int:
    if deps.strategy_mode == "baseline_volume":
        return row.volume_rank if row.volume_rank > 0 else 9999
    return row.pct_rank if row.pct_rank > 0 else 9999


def activity_for_symbol(
    activity: list[MarketActivity],
    symbol: str,
) -> MarketActivity | None:
    for row in activity:
        if row.symbol == symbol:
            return row
    return None


def _equity_basis(
    *,
    run: PaperTradingRun,
    positions: list[PaperPosition],
) -> float:
    open_positions = [row for row in positions if row.status == "OPEN"]
    equity = run.cash_balance + sum(row.market_value for row in open_positions)
    return equity if equity > 0 else run.initial_capital
