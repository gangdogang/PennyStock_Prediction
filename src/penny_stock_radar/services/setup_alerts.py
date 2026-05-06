from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .csv_utils import iter_non_comment_lines

EASTERN = ZoneInfo("America/New_York")

ALERT_CANDIDATE = "CANDIDATE"
ALERT_DIAGNOSTIC = "DIAGNOSTIC"
ALERT_BLOCKED = "BLOCKED"

DATA_GRADE_GROSS_OHLCV = "gross_ohlcv_only"
DATA_GRADE_L1_NEWS_FLOAT = "l1_news_float_required"
DATA_GRADE_FULL_CONTEXT = "full_setup_context_required"

DEFAULT_SETUP_ALERT_ROOT = Path("data/backtest_lab/setup_alerts")


@dataclass(frozen=True, slots=True)
class SetupDefinition:
    setup_id: str
    display_name: str
    family: str
    entry_window_et: str
    required_context: tuple[str, ...]
    stop_structure: str
    add_rule: str
    invalidation: str
    required_data_grade: str


SETUP_TAXONOMY: tuple[SetupDefinition, ...] = (
    SetupDefinition(
        setup_id="afternoon_vwap_reclaim",
        display_name="Afternoon VWAP Reclaim",
        family="long_continuation",
        entry_window_et="14:00-15:30",
        required_context=(
            "vwap_reclaim_or_pullback_hold",
            "spread_or_l1_proxy",
            "news_trap_filter",
            "float_rotation_optional_v0",
        ),
        stop_structure="below_vwap_or_last_higher_low",
        add_rule="anti_martingale_after_starter_positive_r",
        invalidation="loses_vwap_or_failed_reclaim",
        required_data_grade=DATA_GRADE_L1_NEWS_FLOAT,
    ),
    SetupDefinition(
        setup_id="day2_morning_panic",
        display_name="Day 2 Morning Panic",
        family="long_panic_dip",
        entry_window_et="09:45-10:30",
        required_context=(
            "day_state_d2",
            "panic_pullback_then_hold",
            "news_trap_filter",
            "spread_or_l1_proxy",
        ),
        stop_structure="below_panic_low_or_reclaim_level",
        add_rule="fast_add_only_after_reclaim_holds",
        invalidation="new_low_after_reclaim_or_halt_risk",
        required_data_grade=DATA_GRADE_FULL_CONTEXT,
    ),
    SetupDefinition(
        setup_id="first_green_day_continuation",
        display_name="First Green Day Continuation",
        family="long_catalyst_continuation",
        entry_window_et="10:00-11:30",
        required_context=(
            "day_state_d1_or_first_green",
            "tier1_or_tier2_news",
            "vwap_reclaim_or_hod_break",
            "spread_or_l1_proxy",
        ),
        stop_structure="below_vwap_or_morning_low_whichever_closer",
        add_rule="add_on_hod_break_after_starter_risk_is_free",
        invalidation="loses_vwap_for_two_5m_bars_or_trap_news",
        required_data_grade=DATA_GRADE_FULL_CONTEXT,
    ),
)

SETUP_DEFINITIONS_BY_ID = {definition.setup_id: definition for definition in SETUP_TAXONOMY}


@dataclass(frozen=True, slots=True)
class SetupAlertContext:
    run_id: str
    source: str
    market_date: str
    symbol: str
    observed_at: datetime
    market_phase: str
    position_state: str = "flat"
    last_price: float | None = None
    vwap: float | None = None
    distance_to_vwap_pct: float | None = None
    premarket_high: float | None = None
    hod: float | None = None
    opening_range_high: float | None = None
    pullback_depth_pct: float | None = None
    volume_bar_ratio: float | None = None
    spread_pct: float | None = None
    dollar_volume: float | None = None
    analysis_label: str = ""
    analysis_score: float = 0.0
    setup_state: str = ""
    action_bias: str = ""
    quality: int = 0
    risk: int = 0
    above_vwap: bool = False
    vwap_reclaim: bool = False
    hod_breakout: bool = False
    opening_range_breakout: bool = False
    failed_breakout: bool = False
    pullback_hold: bool = False
    volume_expansion: bool = False
    true_leader: bool = False
    spread_ok: bool = True
    liquidity_ok: bool = True
    dilution_risk_flag: bool = False
    catalyst_risk_flag: bool = False
    day_state: str | None = None
    news_tier: str | None = None
    trap_reason: str | None = None
    float_rotation: float | None = None
    halt_code: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupAlert:
    run_id: str
    source: str
    market_date: str
    symbol: str
    observed_at: datetime
    market_phase: str
    setup_id: str
    setup_family: str
    alert_state: str
    time_bucket: str
    confidence: float
    quality: int
    risk: int
    data_grade: str
    required_data_grade: str
    blocked_reasons: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    payload: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_row(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "market_date": self.market_date,
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "market_phase": self.market_phase,
            "setup_id": self.setup_id,
            "setup_family": self.setup_family,
            "alert_state": self.alert_state,
            "time_bucket": self.time_bucket,
            "confidence": round(self.confidence, 6),
            "quality": self.quality,
            "risk": self.risk,
            "data_grade": self.data_grade,
            "required_data_grade": self.required_data_grade,
            "blocked_reasons": json.dumps(list(self.blocked_reasons), ensure_ascii=True),
            "reasons": json.dumps(list(self.reasons), ensure_ascii=True),
            "payload": json.dumps(self.payload, ensure_ascii=True, sort_keys=True),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SetupAlertBuildResult:
    export_dir: Path
    csv_path: Path
    json_path: Path
    summary_path: Path
    summary: dict[str, object]
    alerts: tuple[SetupAlert, ...]


class SetupAlertClassifier:
    def classify(self, context: SetupAlertContext) -> tuple[SetupAlert, ...]:
        alerts = [
            alert
            for alert in (
                self._afternoon_vwap_reclaim(context),
                self._day2_morning_panic(context),
                self._first_green_day_continuation(context),
            )
            if alert is not None
        ]
        return tuple(alerts)

    def _afternoon_vwap_reclaim(self, context: SetupAlertContext) -> SetupAlert | None:
        if not _in_window(context.observed_at, start_minute=14 * 60, end_minute=15 * 60 + 30):
            return None
        pattern = (
            context.vwap_reclaim
            or context.pullback_hold
            or (
                context.above_vwap
                and context.volume_expansion
                and context.setup_state in {"VWAP_RECLAIM", "PULLBACK_HOLD", "STARTER_VALID"}
            )
        )
        if not pattern:
            return None
        return self._build_alert(
            context,
            setup_id="afternoon_vwap_reclaim",
            base_reasons=["afternoon_window", "vwap_reclaim_or_hold"],
            hard_blockers=_hard_blockers(context),
            missing_context=_missing_execution_context(context),
            confidence_boost=0.06,
        )

    def _day2_morning_panic(self, context: SetupAlertContext) -> SetupAlert | None:
        if not _in_window(context.observed_at, start_minute=9 * 60 + 45, end_minute=10 * 60 + 30):
            return None
        if context.day_state is not None and _normalize_token(context.day_state) not in {"D2", "DAY2"}:
            return None
        panic_depth = context.pullback_depth_pct is not None and 5.0 <= context.pullback_depth_pct <= 35.0
        reclaim_or_hold = context.vwap_reclaim or context.pullback_hold or context.above_vwap
        if not (panic_depth and reclaim_or_hold):
            return None
        missing = list(_missing_execution_context(context))
        if context.day_state is None:
            missing.append("day_state_missing")
        return self._build_alert(
            context,
            setup_id="day2_morning_panic",
            base_reasons=["morning_panic_window", "panic_pullback_then_hold"],
            hard_blockers=_hard_blockers(context),
            missing_context=tuple(missing),
            confidence_boost=0.02,
        )

    def _first_green_day_continuation(self, context: SetupAlertContext) -> SetupAlert | None:
        if not _in_window(context.observed_at, start_minute=10 * 60, end_minute=11 * 60 + 30):
            return None
        day_state = _normalize_token(context.day_state or "")
        if context.day_state is not None and day_state not in {"D1", "DAY1", "FIRST_GREEN_DAY"}:
            return None
        technical = context.vwap_reclaim or context.hod_breakout or context.opening_range_breakout
        if not technical:
            return None
        news_tier = _normalize_token(context.news_tier or "")
        if context.news_tier is not None and news_tier not in {"TIER1", "TIER2", "T1", "T2"}:
            return None
        missing = list(_missing_execution_context(context))
        if context.day_state is None:
            missing.append("day_state_missing")
        if context.news_tier is None:
            missing.append("news_tier_missing")
        return self._build_alert(
            context,
            setup_id="first_green_day_continuation",
            base_reasons=["first_green_window", "vwap_or_hod_continuation"],
            hard_blockers=_hard_blockers(context),
            missing_context=tuple(missing),
            confidence_boost=0.04,
        )

    def _build_alert(
        self,
        context: SetupAlertContext,
        *,
        setup_id: str,
        base_reasons: list[str],
        hard_blockers: tuple[str, ...],
        missing_context: tuple[str, ...],
        confidence_boost: float,
    ) -> SetupAlert:
        definition = SETUP_DEFINITIONS_BY_ID[setup_id]
        if hard_blockers:
            state = ALERT_BLOCKED
            data_grade = DATA_GRADE_GROSS_OHLCV
        elif missing_context:
            state = ALERT_DIAGNOSTIC
            data_grade = DATA_GRADE_GROSS_OHLCV
        else:
            state = ALERT_CANDIDATE
            data_grade = definition.required_data_grade
        confidence = _confidence(context, state=state, boost=confidence_boost)
        payload = {
            "last_price": context.last_price,
            "vwap": context.vwap,
            "distance_to_vwap_pct": context.distance_to_vwap_pct,
            "pullback_depth_pct": context.pullback_depth_pct,
            "volume_bar_ratio": context.volume_bar_ratio,
            "spread_pct": context.spread_pct,
            "dollar_volume": context.dollar_volume,
            "analysis_label": context.analysis_label,
            "analysis_score": context.analysis_score,
            "setup_state": context.setup_state,
            "action_bias": context.action_bias,
            "day_state": context.day_state,
            "news_tier": context.news_tier,
            "trap_reason": context.trap_reason,
            "float_rotation": context.float_rotation,
            "halt_code": context.halt_code,
            "taxonomy": {
                "entry_window_et": definition.entry_window_et,
                "stop_structure": definition.stop_structure,
                "add_rule": definition.add_rule,
                "invalidation": definition.invalidation,
            },
        }
        reasons = tuple(base_reasons + list(context.reasons))
        return SetupAlert(
            run_id=context.run_id,
            source=context.source,
            market_date=context.market_date,
            symbol=context.symbol,
            observed_at=context.observed_at,
            market_phase=context.market_phase,
            setup_id=setup_id,
            setup_family=definition.family,
            alert_state=state,
            time_bucket=_time_bucket(context.observed_at),
            confidence=confidence,
            quality=context.quality,
            risk=context.risk,
            data_grade=data_grade,
            required_data_grade=definition.required_data_grade,
            blocked_reasons=tuple(dict.fromkeys((*hard_blockers, *missing_context))),
            reasons=tuple(dict.fromkeys(reasons)),
            payload=payload,
        )


class SetupAlertBus:
    def __init__(self, classifier: SetupAlertClassifier | None = None) -> None:
        self.classifier = classifier or SetupAlertClassifier()

    def build_from_feature_csv(
        self,
        features_csv: Path,
        *,
        run_id: str | None = None,
        source: str = "paper_setup_features",
    ) -> tuple[SetupAlert, ...]:
        contexts = setup_contexts_from_feature_csv(features_csv, run_id=run_id, source=source)
        return self.build_from_contexts(contexts)

    def build_from_contexts(self, contexts: Iterable[SetupAlertContext]) -> tuple[SetupAlert, ...]:
        alerts: list[SetupAlert] = []
        for context in contexts:
            alerts.extend(self.classifier.classify(context))
        return tuple(alerts)


def setup_contexts_from_feature_csv(
    features_csv: Path,
    *,
    run_id: str | None = None,
    source: str = "paper_setup_features",
) -> tuple[SetupAlertContext, ...]:
    with features_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(iter_non_comment_lines(handle))
        rows = list(reader)
    contexts = [
        setup_context_from_feature_row(row, run_id=run_id, source=source)
        for row in rows
    ]
    return tuple(context for context in contexts if context is not None)


def setup_context_from_feature_row(
    row: dict[str, object],
    *,
    run_id: str | None = None,
    source: str = "paper_setup_features",
) -> SetupAlertContext | None:
    observed_at = _parse_datetime(str(row.get("observed_at") or row.get("bar_at") or ""))
    if observed_at is None:
        return None
    symbol = str(row.get("symbol") or "").strip().upper()
    market_date = str(row.get("market_date") or "").strip()
    if not symbol or not market_date:
        return None
    reasons = _split_reason_text(
        str(row.get("context_reasons") or row.get("judge_reasons") or "")
    )
    return SetupAlertContext(
        run_id=str(run_id or row.get("run_id") or "setup_alerts"),
        source=source,
        market_date=market_date,
        symbol=symbol,
        observed_at=observed_at,
        market_phase=str(row.get("market_phase") or ""),
        position_state=str(row.get("position_state") or "flat"),
        last_price=_optional_float(row.get("last_price")),
        vwap=_optional_float(row.get("vwap")),
        distance_to_vwap_pct=_optional_float(row.get("distance_to_vwap_pct")),
        premarket_high=_optional_float(row.get("premarket_high")),
        hod=_optional_float(row.get("hod")),
        opening_range_high=_optional_float(row.get("opening_range_high")),
        pullback_depth_pct=_optional_float(row.get("pullback_depth_pct")),
        volume_bar_ratio=_optional_float(row.get("volume_bar_ratio")),
        spread_pct=_optional_float(row.get("spread_pct")),
        dollar_volume=_optional_float(row.get("dollar_volume")),
        analysis_label=str(row.get("analysis_label") or ""),
        analysis_score=_optional_float(row.get("analysis_score")) or 0.0,
        setup_state=str(row.get("setup_state") or ""),
        action_bias=str(row.get("action_bias") or ""),
        quality=int(_optional_float(row.get("quality")) or 0),
        risk=int(_optional_float(row.get("risk")) or 0),
        above_vwap=_bool(row.get("above_vwap")),
        vwap_reclaim=_bool(row.get("vwap_reclaim")),
        hod_breakout=_bool(row.get("hod_breakout")),
        opening_range_breakout=_bool(row.get("opening_range_breakout")),
        failed_breakout=_bool(row.get("failed_breakout")),
        pullback_hold=_bool(row.get("pullback_hold")),
        volume_expansion=_bool(row.get("volume_expansion")),
        true_leader=_bool(row.get("true_leader")),
        spread_ok=_bool(row.get("spread_ok"), default=True),
        liquidity_ok=_bool(row.get("liquidity_ok"), default=True),
        dilution_risk_flag=_bool(row.get("dilution_risk_flag")),
        catalyst_risk_flag=_bool(row.get("catalyst_risk_flag")),
        day_state=_optional_text(row.get("day_state")),
        news_tier=_optional_text(row.get("news_tier")),
        trap_reason=_optional_text(row.get("trap_reason")),
        float_rotation=_optional_float(row.get("float_rotation")),
        halt_code=_optional_text(row.get("halt_code")),
        reasons=reasons,
    )


def write_setup_alert_exports(
    alerts: Iterable[SetupAlert],
    *,
    output_root: Path = DEFAULT_SETUP_ALERT_ROOT,
    run_id: str | None = None,
) -> SetupAlertBuildResult:
    alert_tuple = tuple(alerts)
    export_run_id = run_id or _run_id_from_alerts(alert_tuple)
    export_dir = _unique_export_dir(output_root / export_run_id)
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_path = export_dir / "setup_alerts.csv"
    json_path = export_dir / "setup_alerts.json"
    summary_path = export_dir / "setup_alert_summary.json"
    rows = [alert.to_row() for alert in alert_tuple]
    fieldnames = list(rows[0].keys()) if rows else _empty_alert_fieldnames()
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = setup_alert_summary(alert_tuple, run_id=export_run_id)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return SetupAlertBuildResult(
        export_dir=export_dir,
        csv_path=csv_path,
        json_path=json_path,
        summary_path=summary_path,
        summary=summary,
        alerts=alert_tuple,
    )


def setup_alert_summary(alerts: Iterable[SetupAlert], *, run_id: str) -> dict[str, object]:
    alert_tuple = tuple(alerts)
    state_counts = Counter(alert.alert_state for alert in alert_tuple)
    setup_counts = Counter(alert.setup_id for alert in alert_tuple)
    diagnostic_reasons = Counter(
        reason
        for alert in alert_tuple
        if alert.alert_state != ALERT_CANDIDATE
        for reason in alert.blocked_reasons
    )
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "scope": "setup_alert_bus_diagnostic_only",
            "decision_grade": False,
            "cost_grade": "none",
            "notes": (
                "Setup alerts are candidate/context diagnostics. They are not "
                "setup backtest results and do not authorize live execution."
            ),
        },
        "alert_count": len(alert_tuple),
        "alert_state_counts": dict(sorted(state_counts.items())),
        "setup_counts": dict(sorted(setup_counts.items())),
        "blocked_reason_counts": dict(sorted(diagnostic_reasons.items())),
        "taxonomy": [asdict(definition) for definition in SETUP_TAXONOMY],
    }


def _hard_blockers(context: SetupAlertContext) -> tuple[str, ...]:
    blockers: list[str] = []
    if context.position_state.lower() == "open":
        blockers.append("position_already_open")
    if context.failed_breakout:
        blockers.append("failed_breakout")
    if context.dilution_risk_flag:
        blockers.append("dilution_risk_flag")
    if context.catalyst_risk_flag:
        blockers.append("catalyst_risk_flag")
    if context.trap_reason:
        blockers.append("trap_news")
    if not context.spread_ok:
        blockers.append("spread_not_ok")
    if not context.liquidity_ok:
        blockers.append("liquidity_not_ok")
    if context.risk >= 85:
        blockers.append("setup_risk_too_high")
    return tuple(blockers)


def _missing_execution_context(context: SetupAlertContext) -> tuple[str, ...]:
    missing: list[str] = []
    if context.spread_pct is None:
        missing.append("l1_spread_missing_cost_not_decision_grade")
    if context.news_tier is None and not context.trap_reason:
        missing.append("news_tier_missing")
    if context.float_rotation is None:
        missing.append("float_rotation_missing")
    if context.halt_code is None:
        missing.append("halt_context_missing")
    return tuple(missing)


def _confidence(context: SetupAlertContext, *, state: str, boost: float) -> float:
    quality_component = max(min(context.quality / 100.0, 1.0), 0.0) * 0.48
    risk_component = (1.0 - max(min(context.risk / 100.0, 1.0), 0.0)) * 0.22
    structure_component = 0.0
    if context.vwap_reclaim:
        structure_component += 0.10
    if context.pullback_hold:
        structure_component += 0.08
    if context.hod_breakout or context.opening_range_breakout:
        structure_component += 0.06
    if context.true_leader:
        structure_component += 0.04
    confidence = quality_component + risk_component + structure_component + boost
    if state == ALERT_DIAGNOSTIC:
        confidence *= 0.72
    elif state == ALERT_BLOCKED:
        confidence *= 0.35
    return round(max(min(confidence, 0.99), 0.01), 6)


def _in_window(value: datetime, *, start_minute: int, end_minute: int) -> bool:
    minute = _et_minute(value)
    return start_minute <= minute <= end_minute


def _time_bucket(value: datetime) -> str:
    minute = _et_minute(value)
    if 4 * 60 <= minute <= 9 * 60 + 29:
        return "premarket"
    if 9 * 60 + 30 <= minute <= 9 * 60 + 44:
        return "open_fakeout_risk"
    if 9 * 60 + 45 <= minute <= 10 * 60 + 30:
        return "real_money_morning"
    if 10 * 60 + 31 <= minute <= 12 * 60:
        return "late_morning_dead_zone"
    if 14 * 60 <= minute <= 15 * 60 + 30:
        return "afternoon_runner_window"
    if 15 * 60 + 31 <= minute <= 16 * 60:
        return "closing_ramp_dump_window"
    return "midday_or_afterhours"


def _et_minute(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=EASTERN)
    et_value = value.astimezone(EASTERN)
    return et_value.hour * 60 + et_value.minute


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EASTERN)
    return parsed


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bool(value: object, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _split_reason_text(value: str) -> tuple[str, ...]:
    parts = [part.strip() for part in value.replace(";", ",").split(",")]
    return tuple(part for part in parts if part)


def _normalize_token(value: str) -> str:
    return value.strip().replace(" ", "_").replace("-", "_").upper()


def _run_id_from_alerts(alerts: tuple[SetupAlert, ...]) -> str:
    if alerts:
        return alerts[0].run_id
    return datetime.now(timezone.utc).strftime("setup_alerts_%Y%m%d_%H%M%S")


def _empty_alert_fieldnames() -> list[str]:
    sample = SetupAlert(
        run_id="",
        source="",
        market_date="",
        symbol="",
        observed_at=datetime.now(EASTERN),
        market_phase="",
        setup_id="",
        setup_family="",
        alert_state="",
        time_bucket="",
        confidence=0.0,
        quality=0,
        risk=0,
        data_grade="",
        required_data_grade="",
    )
    return list(sample.to_row().keys())


def _unique_export_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Unable to allocate unique setup alert export directory under {path.parent}")
