from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import AppSettings
from ..db import (
    fetch_latest_filings,
    fetch_latest_paper_trading_run,
    fetch_paper_positions,
)
from ..models import MarketActivity, PaperPosition, TradePlanCandidate
from .market_activity import MarketActivityScanner
from .paper_trading import PRIMARY_PAPER_STRATEGY, PaperTradingEngine
from .trading_support import (
    EASTERN,
    best_live_rank,
    classify_day_regime,
    halt_suspected,
    is_premarket_entry_window,
    is_winner_add_window,
    regime_limits,
)


@dataclass(slots=True)
class TradePlanResult:
    market_phase: str
    regime: str
    daily_loss_locked: bool
    current_day_pnl: float
    current_open_risk: float
    candidates: list[TradePlanCandidate]
    csv_path: Path
    checklist_path: Path
    generated_at: datetime


class TradePlanService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        now_fn: Callable[[], datetime] | None = None,
        export_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.export_root = export_root or Path("sample_outputs")
        self.scanner = MarketActivityScanner(settings)
        self.engine = PaperTradingEngine(
            settings,
            strategy_name=PRIMARY_PAPER_STRATEGY,
            strategy_mode="adaptive",
            now_fn=self.now_fn,
        )

    def generate(
        self,
        *,
        phase: str = "auto",
        activity: list[MarketActivity] | None = None,
        export: bool = True,
    ) -> TradePlanResult:
        now = self.now_fn()
        market_phase = self.scanner.resolve_market_phase(phase)
        rows = list(activity or [])
        if not rows and market_phase in {"premarket", "regular"}:
            scan_result = self.scanner.scan(
                phase=market_phase,
                top_limit=10,
                persist=True,
            )
            rows = scan_result.activity

        regime = classify_day_regime(self.settings, rows) if rows else "cold"
        run = fetch_latest_paper_trading_run(self.settings.database_path, PRIMARY_PAPER_STRATEGY)
        positions = (
            fetch_paper_positions(self.settings.database_path, run.run_id)
            if run is not None
            else []
        )
        open_positions = [row for row in positions if row.status == "OPEN"]
        open_by_symbol = {row.symbol: row for row in open_positions}
        market_date = self._market_date(now)
        traded_today = {
            row.symbol
            for row in positions
            if self._market_date(row.opened_at) == market_date
        }
        open_symbols = set(open_by_symbol)

        filing_flags = self._filing_flags(tuple(sorted({row.symbol for row in rows})))
        current_day_pnl = self._current_day_pnl(now=now, positions=positions)
        equity_basis = run.equity if run is not None and run.equity > 0 else self.settings.paper_initial_capital
        daily_loss_locked = current_day_pnl <= -(equity_basis * self.settings.trade_plan_daily_loss_lock_pct / 100.0)
        current_open_risk = self._current_open_risk(open_positions)
        concurrent_risk_locked = current_open_risk >= equity_basis * self.settings.trade_plan_max_concurrent_open_risk_pct / 100.0

        candidates = self._build_candidates(
            rows=rows,
            market_phase=market_phase,
            now=now,
            regime=regime,
            equity_basis=equity_basis,
            open_by_symbol=open_by_symbol,
            open_symbols=open_symbols,
            traded_today=traded_today,
            filing_flags=filing_flags,
            daily_loss_locked=daily_loss_locked,
            concurrent_risk_locked=concurrent_risk_locked,
        )

        csv_path = (self.export_root / "live_trade_plan.csv").resolve()
        checklist_path = (self.export_root / "live_trade_checklist.md").resolve()
        if export:
            self._write_csv(csv_path, candidates)
            self._write_checklist(
                checklist_path,
                market_phase=market_phase,
                regime=regime,
                daily_loss_locked=daily_loss_locked,
                current_day_pnl=current_day_pnl,
                current_open_risk=current_open_risk,
                generated_at=now,
                candidates=candidates,
            )
        return TradePlanResult(
            market_phase=market_phase,
            regime=regime,
            daily_loss_locked=daily_loss_locked,
            current_day_pnl=current_day_pnl,
            current_open_risk=current_open_risk,
            candidates=candidates,
            csv_path=csv_path,
            checklist_path=checklist_path,
            generated_at=now,
        )

    def _build_candidates(
        self,
        *,
        rows: list[MarketActivity],
        market_phase: str,
        now: datetime,
        regime: str,
        equity_basis: float,
        open_by_symbol: dict[str, PaperPosition],
        open_symbols: set[str],
        traded_today: set[str],
        filing_flags: dict[str, list[str]],
        daily_loss_locked: bool,
        concurrent_risk_locked: bool,
    ) -> list[TradePlanCandidate]:
        limits = regime_limits(regime)
        starter_count = 0
        add_count = 0
        candidates: list[TradePlanCandidate] = []
        sorted_rows = sorted(rows, key=self._candidate_sort_key)

        for row in sorted_rows:
            bucket = self._bucket_for_row(
                row=row,
                market_phase=market_phase,
                now=now,
                open_by_symbol=open_by_symbol,
                open_symbols=open_symbols,
                traded_today=traded_today,
            )
            if bucket is None:
                continue

            blockers = self._base_blockers(
                row=row,
                market_phase=market_phase,
                filing_flags=filing_flags.get(row.symbol, []),
                daily_loss_locked=daily_loss_locked,
                concurrent_risk_locked=concurrent_risk_locked,
            )
            reasons = self._candidate_reasons(row=row, bucket=bucket)
            actionability = "watch_only"
            if blockers:
                actionability = "blocked"
            elif bucket == "live_exception":
                actionability = "watch_only"
                reasons.append("risk_first_live_exception_watch_only")
            elif bucket == "predicted_starter":
                if not is_premarket_entry_window(now):
                    reasons.append("starter_window_closed")
                elif starter_count >= limits["starter"]:
                    reasons.append("regime_starter_slots_full")
                else:
                    actionability = "actionable"
                    starter_count += 1
            elif bucket == "winner_add":
                if not is_winner_add_window(now):
                    reasons.append("winner_add_window_closed")
                elif add_count >= limits["add"]:
                    reasons.append("regime_add_slots_full")
                else:
                    actionability = "actionable"
                    add_count += 1

            entry_reference = self._entry_reference(row)
            stop = self._planned_stop(bucket=bucket, entry_reference=entry_reference, position=open_by_symbol.get(row.symbol))
            risk_per_share = (
                max(entry_reference - stop, 0.0)
                if entry_reference is not None and stop is not None
                else None
            )
            suggested_size = self._suggested_size(
                bucket=bucket,
                equity_basis=equity_basis,
                entry_reference=entry_reference,
                risk_per_share=risk_per_share,
            )
            if actionability == "actionable" and suggested_size <= 0:
                actionability = "blocked"
                blockers.append("size_zero")

            max_dollar_risk = (
                suggested_size * risk_per_share
                if risk_per_share is not None and suggested_size > 0
                else None
            )
            candidates.append(
                TradePlanCandidate(
                    symbol=row.symbol,
                    market_phase=market_phase,
                    bucket=bucket,
                    actionability=actionability,
                    entry_reference=entry_reference,
                    stop=stop,
                    risk_per_share=risk_per_share,
                    suggested_size=suggested_size,
                    max_dollar_risk=max_dollar_risk,
                    spread_pct=row.spread_pct,
                    data_age_seconds=row.data_age_seconds,
                    regime=regime,
                    day_loss_locked=daily_loss_locked,
                    reasons=reasons,
                    blockers=blockers,
                    created_at=now,
                )
            )

        candidates.sort(
            key=lambda item: (
                {"actionable": 0, "watch_only": 1, "blocked": 2}.get(item.actionability, 3),
                {"predicted_starter": 0, "winner_add": 1, "live_exception": 2}.get(item.bucket, 9),
            )
        )
        return candidates

    def _bucket_for_row(
        self,
        *,
        row: MarketActivity,
        market_phase: str,
        now: datetime,
        open_by_symbol: dict[str, PaperPosition],
        open_symbols: set[str],
        traded_today: set[str],
    ) -> str | None:
        position = open_by_symbol.get(row.symbol)
        if position is not None and self._winner_add_signal(position=position, row=row):
            return "winner_add"
        if self._predicted_starter_signal(
            row=row,
            open_symbols=open_symbols,
            traded_today=traded_today,
        ):
            return "predicted_starter"
        if self._live_exception_signal(
            row=row,
            market_phase=market_phase,
            open_symbols=open_symbols,
            traded_today=traded_today,
        ):
            return "live_exception"
        return None

    def _winner_add_signal(
        self,
        *,
        position: PaperPosition,
        row: MarketActivity,
    ) -> bool:
        if position.add_count >= self.settings.paper_max_adds_per_position:
            return False
        if position.partial_exit_count > 0:
            return False
        if position.strategy_bucket != "predicted_starter":
            return False
        if row.last_price is None or row.last_price <= 0:
            return False
        if row.analysis_label not in {"OPENING_RANGE_CANDIDATE", "CONDITIONAL_ENTRY"}:
            return False
        if (
            max(row.leader_persistence_score, row.pullback_absorption_score) > 0
            and row.leader_persistence_score < 60.0
            and row.pullback_absorption_score < 65.0
        ):
            return False
        gain_pct = ((row.last_price - position.average_entry_price) / position.average_entry_price) * 100.0
        if gain_pct < self.settings.paper_add_trigger_pct:
            return False
        return best_live_rank(row) <= 3 or row.behavioral_score >= 68.0

    def _predicted_starter_signal(
        self,
        *,
        row: MarketActivity,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        return self.engine._eligible_for_predicted_priority_entry(row=row)

    def _live_exception_signal(
        self,
        *,
        row: MarketActivity,
        market_phase: str,
        open_symbols: set[str],
        traded_today: set[str],
    ) -> bool:
        if row.symbol in open_symbols or row.symbol in traded_today:
            return False
        return self.engine._eligible_for_live_exception_entry(
            row=row,
            advice=None,
            market_phase=market_phase,
        )

    def _candidate_sort_key(self, row: MarketActivity) -> tuple[object, ...]:
        if row.predicted:
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
            best_live_rank(row),
            -float(row.analysis_score),
            -(row.dollar_volume or 0.0),
            row.symbol,
        )

    def _base_blockers(
        self,
        *,
        row: MarketActivity,
        market_phase: str,
        filing_flags: list[str],
        daily_loss_locked: bool,
        concurrent_risk_locked: bool,
    ) -> list[str]:
        blockers: list[str] = []
        if row.data_age_seconds is None or row.data_age_seconds > float(self.settings.trade_plan_stale_data_seconds):
            blockers.append("stale_data")
        if not row.has_live_quote:
            blockers.append("missing_live_quote")
        if row.spread_pct is not None and row.spread_pct > self.settings.premarket_max_spread_pct:
            blockers.append("spread_too_wide")
        if row.dollar_volume is not None and row.dollar_volume < self.settings.premarket_min_dollar_volume:
            blockers.append("low_dollar_volume")
        if row.behavioral_score > 0 and row.behavioral_score < 40.0:
            blockers.append("behavioral_too_low")
        if row.trap_score > 0 and row.trap_score >= 72.0:
            blockers.append("trap_risk_high")
        if halt_suspected(
            row,
            market_phase=market_phase,
            halt_seconds=self.settings.trade_plan_halt_suspected_seconds,
        ):
            blockers.append("halt_suspected")
        if daily_loss_locked:
            blockers.append("daily_loss_lock")
        if concurrent_risk_locked:
            blockers.append("concurrent_open_risk_limit")
        for flag in filing_flags:
            if flag not in blockers:
                blockers.append(flag)
        return blockers

    def _candidate_reasons(self, *, row: MarketActivity, bucket: str) -> list[str]:
        reasons = [bucket]
        if row.watchlist_rank is not None:
            reasons.append(f"watchlist_rank:{row.watchlist_rank}")
        reasons.extend(row.reasons[:5])
        return self._dedupe(reasons)

    def _entry_reference(self, row: MarketActivity) -> float | None:
        candidates = [value for value in (row.last_price, row.ask_price) if value is not None and value > 0]
        if not candidates:
            return None
        return max(candidates)

    def _planned_stop(
        self,
        *,
        bucket: str,
        entry_reference: float | None,
        position: PaperPosition | None,
    ) -> float | None:
        if entry_reference is None:
            return None
        base_stop = entry_reference * (1 - self.settings.paper_stop_loss_pct / 100.0)
        if bucket == "winner_add" and position is not None:
            return max(base_stop, position.average_entry_price)
        return base_stop

    def _suggested_size(
        self,
        *,
        bucket: str,
        equity_basis: float,
        entry_reference: float | None,
        risk_per_share: float | None,
    ) -> int:
        if entry_reference is None or entry_reference <= 0 or risk_per_share is None or risk_per_share <= 0:
            return 0
        cap_pct = (
            self.settings.trade_plan_add_notional_cap_pct
            if bucket == "winner_add"
            else self.settings.trade_plan_starter_notional_cap_pct
        )
        risk_budget = equity_basis * self.settings.trade_plan_per_trade_risk_pct / 100.0
        notional_budget = equity_basis * cap_pct / 100.0
        risk_shares = int(risk_budget // risk_per_share)
        notional_shares = int(notional_budget // entry_reference)
        return max(min(risk_shares, notional_shares), 0)

    def _filing_flags(self, symbols: tuple[str, ...]) -> dict[str, list[str]]:
        rows = fetch_latest_filings(
            self.settings.database_path,
            limit=max(len(symbols) * 5, 20),
            symbols=symbols,
        ) if symbols else []
        flags: dict[str, list[str]] = {}
        for row in rows:
            symbol = str(row["symbol"]).upper()
            summary = str(row["summary"] or "").lower()
            matched_keywords = " ".join(self._decode_keywords(row["matched_keywords"])).lower()
            form = str(row["form"] or "").upper()
            items = flags.setdefault(symbol, [])
            if form in {"S-1", "S-3", "424B5"} or any(
                keyword in matched_keywords
                for keyword in ("registration statement", "prospectus supplement", "purchase agreement")
            ):
                if "dilution_risk" not in items:
                    items.append("dilution_risk")
            if "reverse split" in summary or "reverse split" in matched_keywords:
                if "reverse_split_risk" not in items:
                    items.append("reverse_split_risk")
            if "warrant" in summary:
                if "warrant_risk" not in items:
                    items.append("warrant_risk")
        return flags

    def _decode_keywords(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
                if isinstance(decoded, list):
                    return [str(item) for item in decoded if item]
            except Exception:
                return [text]
        return [text]

    def _current_day_pnl(
        self,
        *,
        now: datetime,
        positions: list[PaperPosition],
    ) -> float:
        market_date = self._market_date(now)
        pnl = 0.0
        for row in positions:
            if self._market_date(row.opened_at) != market_date:
                continue
            pnl += row.total_pnl
        return pnl

    def _current_open_risk(self, positions: list[PaperPosition]) -> float:
        total = 0.0
        for row in positions:
            stop = row.planned_stop_price or row.stop_price
            last_price = row.last_price or row.average_entry_price
            if stop is None or last_price <= stop:
                continue
            total += (last_price - stop) * row.quantity
        return total

    def _market_date(self, value: datetime) -> str:
        return value.astimezone(EASTERN).date().isoformat()

    def _write_csv(self, path: Path, rows: list[TradePlanCandidate]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "symbol",
            "market_phase",
            "bucket",
            "actionability",
            "entry_reference",
            "stop",
            "risk_per_share",
            "suggested_size",
            "max_dollar_risk",
            "spread_pct",
            "data_age_seconds",
            "regime",
            "day_loss_locked",
            "reasons",
            "blockers",
            "created_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                payload = row.model_dump(mode="json")
                payload["reasons"] = ", ".join(row.reasons)
                payload["blockers"] = ", ".join(row.blockers)
                writer.writerow(payload)

    def _write_checklist(
        self,
        path: Path,
        *,
        market_phase: str,
        regime: str,
        daily_loss_locked: bool,
        current_day_pnl: float,
        current_open_risk: float,
        generated_at: datetime,
        candidates: list[TradePlanCandidate],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        actionable = [row for row in candidates if row.actionability == "actionable"]
        watch_only = [row for row in candidates if row.actionability == "watch_only"]
        blocked = [row for row in candidates if row.actionability == "blocked"]
        lines = [
            "# Live Trade Checklist",
            "",
            f"- generated_at: `{generated_at.isoformat()}`",
            f"- market_phase: `{market_phase}`",
            f"- regime: `{regime}`",
            f"- daily_loss_locked: `{'yes' if daily_loss_locked else 'no'}`",
            f"- current_day_pnl: `{current_day_pnl:.2f}`",
            f"- current_open_risk: `{current_open_risk:.2f}`",
            "",
            "## 지금 매매 가능",
        ]
        lines.extend(self._checklist_lines(actionable))
        lines.extend(["", "## 지켜보기"])
        lines.extend(self._checklist_lines(watch_only))
        lines.extend(["", "## 거래 금지"])
        lines.extend(self._checklist_lines(blocked, include_blockers=True))
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _checklist_lines(
        self,
        rows: list[TradePlanCandidate],
        *,
        include_blockers: bool = False,
    ) -> list[str]:
        if not rows:
            return ["- 없음"]
        lines: list[str] = []
        for row in rows[:12]:
            detail_bits = [
                f"stop=`{self._fmt(row.stop)}`",
                f"size=`{row.suggested_size}`",
                f"risk=`{self._fmt(row.max_dollar_risk)}`",
                f"spread=`{self._fmt_pct(row.spread_pct)}`",
                f"age=`{self._fmt(row.data_age_seconds)}`",
            ]
            extra = row.blockers if include_blockers else row.reasons[:4]
            label = "blockers" if include_blockers else "reasons"
            if extra:
                detail_bits.append(f"{label}=`{' | '.join(extra)}`")
            base = (
                f"- `{row.symbol}` `{row.bucket}` ref=`{self._fmt(row.entry_reference)}` "
                + " ".join(detail_bits)
            )
            lines.append(base)
        return lines

    def _dedupe(self, values: list[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            if value and value not in deduped:
                deduped.append(value)
        return deduped

    def _fmt(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value:.4f}" if abs(value) < 100 else f"{value:.2f}"

    def _fmt_pct(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value * 100:.2f}%"
