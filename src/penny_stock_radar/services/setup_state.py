from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable

from ..models import MarketActivity
from .replay_bar_cache import PreparedBarSnapshot, PreparedMinuteBar

SETUP_STATES = (
    "DEAD_PUMP",
    "WATCH_LEADER",
    "VWAP_RECLAIM",
    "ORB_BREAKOUT",
    "PULLBACK_HOLD",
    "FAILED_BREAKOUT",
    "STARTER_VALID",
    "ADD_VALID",
    "TRIM_EXTENSION",
    "RUNNER_HOLD",
    "EXIT_FAIL",
)

ACTION_BIASES = (
    "no_trade",
    "watch",
    "starter_ok",
    "add_ok",
    "trim",
    "exit",
)


@dataclass(frozen=True)
class SetupContext:
    run_id: str
    market_date: str
    bucket: str
    symbol: str
    observed_at: datetime
    bar_at: datetime
    market_phase: str
    position_state: str
    position_entry_price: float | None
    position_age_minutes: float | None
    unrealized_pct: float | None
    last_price: float | None
    bar_high_price: float | None
    bar_low_price: float | None
    vwap: float | None
    distance_to_vwap_pct: float | None
    premarket_high: float | None
    hod: float | None
    opening_range_high: float | None
    opening_range_low: float | None
    pullback_depth_pct: float | None
    volume_bar_ratio: float | None
    minutes_since_hod: float | None
    best_rank: int
    rank_persistence: float
    spread_pct: float | None
    dollar_volume: float | None
    analysis_score: float
    analysis_label: str
    predicted: bool
    watchlist_rank: int | None
    watchlist_score: float | None
    above_vwap: bool
    vwap_reclaim: bool
    hod_breakout: bool
    opening_range_breakout: bool
    failed_breakout: bool
    pullback_hold: bool
    volume_expansion: bool
    volume_dryup: bool
    higher_low: bool
    lower_high: bool
    true_leader: bool
    spread_ok: bool
    liquidity_ok: bool
    dilution_risk_flag: bool
    catalyst_risk_flag: bool
    reasons: tuple[str, ...]

    @property
    def has_l1_proxy(self) -> bool:
        return self.spread_pct is not None

    def feature_row(self, judgement: SetupJudgement) -> dict[str, object]:
        row = {
            "run_id": self.run_id,
            "market_date": self.market_date,
            "bucket": self.bucket,
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "bar_at": self.bar_at.isoformat(),
            "market_phase": self.market_phase,
            "position_state": self.position_state,
            "position_entry_price": _round_optional(self.position_entry_price),
            "position_age_minutes": _round_optional(self.position_age_minutes),
            "unrealized_pct": _round_optional(self.unrealized_pct),
            "last_price": _round_optional(self.last_price),
            "bar_high_price": _round_optional(self.bar_high_price),
            "bar_low_price": _round_optional(self.bar_low_price),
            "vwap": _round_optional(self.vwap),
            "distance_to_vwap_pct": _round_optional(self.distance_to_vwap_pct),
            "premarket_high": _round_optional(self.premarket_high),
            "hod": _round_optional(self.hod),
            "opening_range_high": _round_optional(self.opening_range_high),
            "opening_range_low": _round_optional(self.opening_range_low),
            "pullback_depth_pct": _round_optional(self.pullback_depth_pct),
            "volume_bar_ratio": _round_optional(self.volume_bar_ratio),
            "minutes_since_hod": _round_optional(self.minutes_since_hod),
            "best_rank": self.best_rank,
            "rank_persistence": round(self.rank_persistence, 6),
            "spread_pct": _round_optional(self.spread_pct),
            "dollar_volume": _round_optional(self.dollar_volume),
            "analysis_score": round(self.analysis_score, 6),
            "analysis_label": self.analysis_label,
            "predicted": int(self.predicted),
            "watchlist_rank": self.watchlist_rank if self.watchlist_rank is not None else "",
            "watchlist_score": _round_optional(self.watchlist_score),
            "above_vwap": int(self.above_vwap),
            "vwap_reclaim": int(self.vwap_reclaim),
            "hod_breakout": int(self.hod_breakout),
            "opening_range_breakout": int(self.opening_range_breakout),
            "failed_breakout": int(self.failed_breakout),
            "pullback_hold": int(self.pullback_hold),
            "volume_expansion": int(self.volume_expansion),
            "volume_dryup": int(self.volume_dryup),
            "higher_low": int(self.higher_low),
            "lower_high": int(self.lower_high),
            "true_leader": int(self.true_leader),
            "spread_ok": int(self.spread_ok),
            "liquidity_ok": int(self.liquidity_ok),
            "dilution_risk_flag": int(self.dilution_risk_flag),
            "catalyst_risk_flag": int(self.catalyst_risk_flag),
            "context_reasons": ", ".join(self.reasons),
        }
        row.update(judgement.csv_fields())
        return row


@dataclass(frozen=True)
class SetupJudgement:
    setup_state: str
    quality: int
    risk: int
    action_bias: str
    confidence: float
    invalidation: str
    add_condition: str
    trim_condition: str
    reasons: tuple[str, ...]

    def csv_fields(self) -> dict[str, object]:
        payload = {
            "setup_state": self.setup_state,
            "quality": self.quality,
            "risk": self.risk,
            "action_bias": self.action_bias,
            "confidence": round(self.confidence, 6),
            "invalidation": self.invalidation,
            "add_condition": self.add_condition,
            "trim_condition": self.trim_condition,
            "judge_reasons": ", ".join(self.reasons),
        }
        payload["judge_json"] = json.dumps(
            {
                "setup_state": self.setup_state,
                "quality": self.quality,
                "risk": self.risk,
                "action_bias": self.action_bias,
                "confidence": round(self.confidence, 6),
                "invalidation": self.invalidation,
                "add_condition": self.add_condition,
                "trim_condition": self.trim_condition,
                "reasons": list(self.reasons),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        return payload

    def prefixed_fields(self, prefix: str) -> dict[str, object]:
        return {
            f"{prefix}_setup_state": self.setup_state,
            f"{prefix}_setup_quality": self.quality,
            f"{prefix}_setup_risk": self.risk,
            f"{prefix}_action_bias": self.action_bias,
            f"{prefix}_setup_confidence": round(self.confidence, 6),
            f"{prefix}_setup_reasons": ", ".join(self.reasons),
        }


class SetupContextBuilder:
    """Builds point-in-time setup features from minute bars.

    This is intentionally deterministic and data-local. It does not place orders
    and it does not call an LLM; later LLM judges can consume the same context.
    """

    def __init__(
        self,
        *,
        run_id: str,
        min_dollar_volume: float,
        max_spread_pct: float,
        opening_range_minutes: int = 5,
        rank_window: int = 5,
    ) -> None:
        self.run_id = run_id
        self.min_dollar_volume = float(min_dollar_volume)
        self.max_spread_pct = float(max_spread_pct)
        self.opening_range_minutes = max(int(opening_range_minutes), 1)
        self.rank_window = max(int(rank_window), 1)
        self._rank_history: dict[tuple[str, str, str], list[int]] = {}

    def build(
        self,
        *,
        market_date: str,
        bucket: str,
        row: MarketActivity,
        snapshot: PreparedBarSnapshot,
        observed_at: datetime,
        cutoff_at: datetime,
        position_opened_at: datetime | None = None,
        position_entry_price: float | None = None,
    ) -> SetupContext:
        bars = list(snapshot.series.bars[: snapshot.index + 1])
        previous = bars[-2] if len(bars) >= 2 else None
        current = snapshot.current
        last_price = _positive(current.close_price)
        vwap = _vwap(bars)
        previous_vwap = _vwap(bars[:-1]) if len(bars) >= 2 else None
        distance_to_vwap_pct = _move_pct(last_price, vwap)
        premarket_high = _max_high(_premarket_bars(bars))
        hod = _max_high(bars)
        previous_hod = _max_high(bars[:-1])
        opening_range = _opening_range_bars(
            bars,
            current=current,
            cutoff_at=cutoff_at,
            minutes=self.opening_range_minutes,
        )
        opening_range_high = _max_high(opening_range)
        opening_range_low = _min_low(opening_range)
        hod_at = _latest_high_at(bars, hod)
        minutes_since_hod = (
            (current.bar_at - hod_at).total_seconds() / 60.0
            if hod_at is not None
            else None
        )
        pullback_depth_pct = (
            ((hod - last_price) / hod) * 100.0
            if hod is not None and hod > 0 and last_price is not None
            else None
        )
        previous_close = _positive(previous.close_price) if previous is not None else None
        volume_baseline = _avg(bar.volume for bar in bars[-6:-1] if bar.volume > 0)
        volume_ratio = (
            current.volume / volume_baseline
            if volume_baseline is not None and volume_baseline > 0
            else None
        )
        best_rank = _best_rank(row)
        rank_persistence = self._rank_persistence(
            market_date=market_date,
            bucket=bucket,
            symbol=row.symbol,
            best_rank=best_rank,
        )
        spread_pct = row.spread_pct
        spread_ok = spread_pct is None or spread_pct <= self.max_spread_pct
        dollar_volume = row.dollar_volume
        liquidity_ok = (dollar_volume is None or dollar_volume >= self.min_dollar_volume) and spread_ok
        above_vwap = last_price is not None and vwap is not None and last_price >= vwap
        vwap_reclaim = (
            previous_close is not None
            and previous_vwap is not None
            and last_price is not None
            and vwap is not None
            and previous_close <= previous_vwap
            and last_price > vwap
        )
        hod_breakout = (
            previous_hod is not None
            and previous_hod > 0
            and current.high_price > previous_hod
            and last_price is not None
            and last_price >= previous_hod
        )
        opening_range_breakout = (
            opening_range_high is not None
            and previous_close is not None
            and last_price is not None
            and previous_close <= opening_range_high
            and last_price > opening_range_high
        )
        failed_breakout = (
            (
                previous_hod is not None
                and current.high_price > previous_hod
                and last_price is not None
                and last_price < previous_hod
            )
            or (
                opening_range_high is not None
                and current.high_price > opening_range_high
                and last_price is not None
                and last_price < opening_range_high
            )
        )
        higher_low = previous is not None and current.low_price > previous.low_price
        lower_high = previous is not None and current.high_price < previous.high_price
        volume_expansion = volume_ratio is not None and volume_ratio >= 2.0
        volume_dryup = volume_ratio is not None and volume_ratio <= 0.5
        pullback_hold = (
            above_vwap
            and pullback_depth_pct is not None
            and 1.0 <= pullback_depth_pct <= 12.0
            and (higher_low or (vwap is not None and current.low_price >= vwap * 0.97))
        )
        true_leader = best_rank <= 3 and rank_persistence >= 0.6 and liquidity_ok
        context_reasons = _context_reasons(
            row=row,
            above_vwap=above_vwap,
            vwap_reclaim=vwap_reclaim,
            hod_breakout=hod_breakout,
            opening_range_breakout=opening_range_breakout,
            failed_breakout=failed_breakout,
            pullback_hold=pullback_hold,
            volume_expansion=volume_expansion,
            volume_dryup=volume_dryup,
            true_leader=true_leader,
            liquidity_ok=liquidity_ok,
        )
        dilution_risk_flag = _has_keyword(row.reasons, _DILUTION_KEYWORDS)
        catalyst_risk_flag = _has_keyword(row.reasons, _CATALYST_RISK_KEYWORDS)
        position_age_minutes = (
            (observed_at - position_opened_at).total_seconds() / 60.0
            if position_opened_at is not None
            else None
        )
        unrealized_pct = _move_pct(last_price, position_entry_price)
        return SetupContext(
            run_id=self.run_id,
            market_date=market_date,
            bucket=bucket,
            symbol=row.symbol,
            observed_at=observed_at,
            bar_at=current.bar_at,
            market_phase=row.market_phase,
            position_state="open" if position_entry_price is not None else "flat",
            position_entry_price=position_entry_price,
            position_age_minutes=position_age_minutes,
            unrealized_pct=unrealized_pct,
            last_price=last_price,
            bar_high_price=_positive(current.high_price),
            bar_low_price=_positive(current.low_price),
            vwap=vwap,
            distance_to_vwap_pct=distance_to_vwap_pct,
            premarket_high=premarket_high,
            hod=hod,
            opening_range_high=opening_range_high,
            opening_range_low=opening_range_low,
            pullback_depth_pct=pullback_depth_pct,
            volume_bar_ratio=volume_ratio,
            minutes_since_hod=minutes_since_hod,
            best_rank=best_rank,
            rank_persistence=rank_persistence,
            spread_pct=spread_pct,
            dollar_volume=dollar_volume,
            analysis_score=float(row.analysis_score),
            analysis_label=row.analysis_label,
            predicted=row.predicted,
            watchlist_rank=row.watchlist_rank,
            watchlist_score=row.watchlist_score,
            above_vwap=above_vwap,
            vwap_reclaim=vwap_reclaim,
            hod_breakout=hod_breakout,
            opening_range_breakout=opening_range_breakout,
            failed_breakout=failed_breakout,
            pullback_hold=pullback_hold,
            volume_expansion=volume_expansion,
            volume_dryup=volume_dryup,
            higher_low=higher_low,
            lower_high=lower_high,
            true_leader=true_leader,
            spread_ok=spread_ok,
            liquidity_ok=liquidity_ok,
            dilution_risk_flag=dilution_risk_flag,
            catalyst_risk_flag=catalyst_risk_flag,
            reasons=tuple(context_reasons),
        )

    def _rank_persistence(self, *, market_date: str, bucket: str, symbol: str, best_rank: int) -> float:
        key = (market_date, bucket, symbol)
        history = self._rank_history.setdefault(key, [])
        history.append(best_rank)
        del history[:-self.rank_window]
        return sum(1 for rank in history if rank <= 5) / len(history)


class AISetupJudgeV1:
    """Rule-backed JSON judge for setup interpretation.

    The name reflects the target interface: this object returns the same JSON
    shape an LLM judge will later produce, but v1 is deterministic.
    """

    def judge(self, context: SetupContext) -> SetupJudgement:
        quality = _clamp_int(_quality_score(context), 0, 100)
        risk = _clamp_int(_risk_score(context), 0, 100)
        state = _setup_state(context, quality=quality, risk=risk)
        action_bias = _action_bias(context, state=state)
        reasons = _judge_reasons(context, state=state, quality=quality, risk=risk)
        return SetupJudgement(
            setup_state=state,
            quality=quality,
            risk=risk,
            action_bias=action_bias,
            confidence=_confidence(context, reasons),
            invalidation=_invalidation(context, state),
            add_condition=_add_condition(context, state),
            trim_condition=_trim_condition(context, state),
            reasons=tuple(reasons),
        )


def setup_judgement_from_feature_row(row: dict[str, object]) -> SetupJudgement:
    reasons = _split_reasons(str(row.get("judge_reasons") or ""))
    return SetupJudgement(
        setup_state=str(row.get("setup_state") or "DEAD_PUMP"),
        quality=int(float(row.get("quality") or 0)),
        risk=int(float(row.get("risk") or 0)),
        action_bias=str(row.get("action_bias") or "no_trade"),
        confidence=float(row.get("confidence") or 0.0),
        invalidation=str(row.get("invalidation") or ""),
        add_condition=str(row.get("add_condition") or ""),
        trim_condition=str(row.get("trim_condition") or ""),
        reasons=tuple(reasons),
    )


def _setup_state(context: SetupContext, *, quality: int, risk: int) -> str:
    if context.position_state == "open":
        if context.failed_breakout or (
            not context.above_vwap and context.lower_high and (context.unrealized_pct or 0.0) <= 0.0
        ):
            return "EXIT_FAIL"
        if _extended(context):
            return "TRIM_EXTENSION"
        if (
            (context.hod_breakout or context.opening_range_breakout or context.vwap_reclaim)
            and (context.unrealized_pct or 0.0) >= 2.0
            and quality >= 70
            and risk < 65
        ):
            return "ADD_VALID"
        if context.above_vwap and context.true_leader and risk < 65:
            return "RUNNER_HOLD"
        if context.pullback_hold:
            return "PULLBACK_HOLD"
        return "EXIT_FAIL" if risk >= 70 else "RUNNER_HOLD"

    if context.failed_breakout:
        return "FAILED_BREAKOUT"
    if _dead_pump(context, quality=quality, risk=risk):
        return "DEAD_PUMP"
    if _extended(context) and risk >= 60:
        return "TRIM_EXTENSION"
    if quality >= 72 and risk < 65 and (
        context.vwap_reclaim or context.hod_breakout or context.opening_range_breakout or context.pullback_hold
    ):
        return "STARTER_VALID"
    if context.vwap_reclaim:
        return "VWAP_RECLAIM"
    if context.hod_breakout or context.opening_range_breakout:
        return "ORB_BREAKOUT"
    if context.pullback_hold:
        return "PULLBACK_HOLD"
    if context.true_leader or quality >= 50:
        return "WATCH_LEADER"
    return "DEAD_PUMP"


def _action_bias(context: SetupContext, *, state: str) -> str:
    if state in {"DEAD_PUMP", "FAILED_BREAKOUT"}:
        return "exit" if context.position_state == "open" else "no_trade"
    if state == "EXIT_FAIL":
        return "exit"
    if state == "TRIM_EXTENSION":
        return "trim" if context.position_state == "open" else "no_trade"
    if state == "ADD_VALID":
        return "add_ok"
    if state == "STARTER_VALID":
        return "starter_ok"
    return "watch"


def _quality_score(context: SetupContext) -> float:
    score = 18.0
    score += min(max(context.analysis_score, 0.0) * 5.0, 24.0)
    if context.true_leader:
        score += 18.0
    elif context.best_rank <= 5:
        score += 10.0
    score += context.rank_persistence * 12.0
    if context.above_vwap:
        score += 8.0
    if context.vwap_reclaim:
        score += 10.0
    if context.hod_breakout or context.opening_range_breakout:
        score += 10.0
    if context.pullback_hold:
        score += 9.0
    if context.volume_expansion:
        score += 7.0
    if context.liquidity_ok:
        score += 7.0
    if context.predicted:
        score += 4.0
    if context.failed_breakout:
        score -= 22.0
    if context.volume_dryup and not context.pullback_hold:
        score -= 8.0
    if not context.spread_ok:
        score -= 12.0
    if context.dilution_risk_flag:
        score -= 12.0
    if context.catalyst_risk_flag:
        score -= 6.0
    if _extended(context):
        score -= 8.0
    return score


def _risk_score(context: SetupContext) -> float:
    risk = 18.0
    distance = context.distance_to_vwap_pct or 0.0
    pullback_depth = context.pullback_depth_pct or 0.0
    if distance >= 8.0:
        risk += min((distance - 8.0) * 2.0, 24.0)
    if pullback_depth >= 12.0:
        risk += min((pullback_depth - 12.0) * 1.5, 22.0)
    if context.failed_breakout:
        risk += 30.0
    if not context.above_vwap:
        risk += 12.0
    if context.lower_high:
        risk += 8.0
    if context.volume_dryup and not context.pullback_hold:
        risk += 9.0
    if not context.spread_ok:
        risk += 18.0
    if not context.liquidity_ok:
        risk += 8.0
    if context.dilution_risk_flag:
        risk += 18.0
    if context.catalyst_risk_flag:
        risk += 10.0
    if context.best_rank > 8:
        risk += 10.0
    if context.vwap_reclaim or context.pullback_hold:
        risk -= 7.0
    if context.true_leader:
        risk -= 5.0
    return risk


def _judge_reasons(context: SetupContext, *, state: str, quality: int, risk: int) -> list[str]:
    reasons = [f"state:{state}", f"quality:{quality}", f"risk:{risk}"]
    reasons.extend(context.reasons)
    if context.position_state == "open" and context.unrealized_pct is not None:
        reasons.append(f"unrealized_pct:{context.unrealized_pct:.2f}")
    return _dedupe(reasons)


def _confidence(context: SetupContext, reasons: Iterable[str]) -> float:
    confidence = 0.45
    confidence += min(len(list(reasons)) * 0.025, 0.18)
    if context.vwap is not None:
        confidence += 0.08
    if context.volume_bar_ratio is not None:
        confidence += 0.06
    if context.has_l1_proxy:
        confidence += 0.04
    else:
        confidence -= 0.05
    if context.rank_persistence >= 0.6:
        confidence += 0.05
    return round(max(0.05, min(confidence, 0.9)), 6)


def _invalidation(context: SetupContext, state: str) -> str:
    if state in {"STARTER_VALID", "ADD_VALID", "RUNNER_HOLD", "VWAP_RECLAIM", "PULLBACK_HOLD"}:
        if context.vwap is not None:
            return f"close loses VWAP {context.vwap:.4f} or breakout fails on rising volume"
        return "close loses reclaim level or breakout fails on rising volume"
    if state == "ORB_BREAKOUT" and context.opening_range_high is not None:
        return f"close back below ORH {context.opening_range_high:.4f}"
    if state == "TRIM_EXTENSION":
        return "extension cools into VWAP or volume expands against position"
    if state in {"FAILED_BREAKOUT", "EXIT_FAIL"}:
        return "requires fresh VWAP reclaim plus higher low before reconsidering"
    return "requires leader rank persistence, VWAP reclaim, and liquidity confirmation"


def _add_condition(context: SetupContext, state: str) -> str:
    if context.position_state != "open":
        return "only after starter is live; no add from flat state"
    if state == "ADD_VALID":
        return "add only through HOD/ORH with green position, rank persistence, and spread_ok"
    return "wait for HOD/ORH continuation above VWAP with risk below 65"


def _trim_condition(context: SetupContext, state: str) -> str:
    if state == "TRIM_EXTENSION":
        return "trim extension while price is stretched from VWAP"
    return "trim if distance_to_vwap expands above 12% or failed breakout appears"


def _dead_pump(context: SetupContext, *, quality: int, risk: int) -> bool:
    if risk >= 75 and quality < 55:
        return True
    if not context.above_vwap and context.volume_dryup and context.best_rank > 5:
        return True
    if context.dilution_risk_flag and not context.true_leader:
        return True
    return False


def _extended(context: SetupContext) -> bool:
    distance = context.distance_to_vwap_pct or 0.0
    pullback_depth = context.pullback_depth_pct
    return distance >= 12.0 or (
        distance >= 8.0
        and pullback_depth is not None
        and pullback_depth <= 1.5
        and context.volume_expansion
    )


def _context_reasons(
    *,
    row: MarketActivity,
    above_vwap: bool,
    vwap_reclaim: bool,
    hod_breakout: bool,
    opening_range_breakout: bool,
    failed_breakout: bool,
    pullback_hold: bool,
    volume_expansion: bool,
    volume_dryup: bool,
    true_leader: bool,
    liquidity_ok: bool,
) -> list[str]:
    reasons = list(row.reasons)
    if true_leader:
        reasons.append("true_leader")
    if above_vwap:
        reasons.append("above_vwap")
    if vwap_reclaim:
        reasons.append("vwap_reclaim")
    if hod_breakout:
        reasons.append("hod_breakout")
    if opening_range_breakout:
        reasons.append("opening_range_breakout")
    if failed_breakout:
        reasons.append("failed_breakout")
    if pullback_hold:
        reasons.append("pullback_hold")
    if volume_expansion:
        reasons.append("volume_expansion")
    if volume_dryup:
        reasons.append("volume_dryup")
    if liquidity_ok:
        reasons.append("liquidity_ok")
    return _dedupe(reasons)


def _opening_range_bars(
    bars: list[PreparedMinuteBar],
    *,
    current: PreparedMinuteBar,
    cutoff_at: datetime,
    minutes: int,
) -> list[PreparedMinuteBar]:
    session_open = current.bar_at.replace(hour=9, minute=30, second=0, microsecond=0)
    if current.bar_at >= session_open:
        start = session_open
    else:
        post_cutoff = [bar.bar_at for bar in bars if bar.bar_at >= cutoff_at]
        start = min(post_cutoff) if post_cutoff else bars[0].bar_at
    end = start + timedelta(minutes=minutes)
    return [bar for bar in bars if start <= bar.bar_at < end]


def _premarket_bars(bars: list[PreparedMinuteBar]) -> list[PreparedMinuteBar]:
    return [bar for bar in bars if bar.bar_at.time() < time(9, 30)]


def _vwap(bars: list[PreparedMinuteBar]) -> float | None:
    volume = sum(bar.volume for bar in bars if bar.volume > 0)
    if volume <= 0:
        return None
    return (
        sum(
            ((bar.high_price + bar.low_price + bar.close_price) / 3.0) * bar.volume
            for bar in bars
            if bar.volume > 0
        )
        / volume
    )


def _max_high(bars: list[PreparedMinuteBar]) -> float | None:
    highs = [bar.high_price for bar in bars if bar.high_price > 0]
    return max(highs) if highs else None


def _min_low(bars: list[PreparedMinuteBar]) -> float | None:
    lows = [bar.low_price for bar in bars if bar.low_price > 0]
    return min(lows) if lows else None


def _latest_high_at(bars: list[PreparedMinuteBar], high: float | None) -> datetime | None:
    if high is None:
        return None
    for bar in reversed(bars):
        if bar.high_price == high:
            return bar.bar_at
    return None


def _best_rank(row: MarketActivity) -> int:
    ranks = [rank for rank in (row.pct_rank, row.volume_rank) if rank is not None and int(rank) > 0]
    return min(int(rank) for rank in ranks) if ranks else 9999


def _avg(values: Iterable[float]) -> float | None:
    items = [float(value) for value in values if value is not None]
    return (sum(items) / len(items)) if items else None


def _move_pct(price: float | None, basis: float | None) -> float | None:
    if price is None or basis is None or basis <= 0:
        return None
    return ((price - basis) / basis) * 100.0


def _positive(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if number > 0 else None


def _round_optional(value: object, digits: int = 6) -> object:
    if value is None:
        return ""
    return round(float(value), digits)


def _clamp_int(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def _has_keyword(values: Iterable[str], keywords: tuple[str, ...]) -> bool:
    text = " ".join(values).lower()
    return any(keyword in text for keyword in keywords)


def _split_reasons(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _dedupe(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


_DILUTION_KEYWORDS = (
    "offering",
    "s-1",
    "s1",
    "atm",
    "warrant",
    "shelf",
    "convertible",
    "dilution",
)

_CATALYST_RISK_KEYWORDS = (
    "context_missing",
    "news_check_required",
    "unknown_catalyst",
    "no_public_catalyst",
)
