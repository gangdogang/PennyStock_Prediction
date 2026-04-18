from __future__ import annotations

from dataclasses import dataclass
import math

from ..config import AppSettings
from ..models import MarketActivity, PaperPosition
from .trading_support import normalize_market_status


@dataclass(frozen=True, slots=True)
class FillResult:
    quantity: int
    remaining_quantity: int
    fill_status: str
    fill_price: float | None
    fill_reference_price: float | None
    fill_slippage_pct: float | None
    transaction_cost: float


class FillModel:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def buy(
        self,
        row: MarketActivity,
        *,
        market_phase: str,
        requested_quantity: int,
    ) -> FillResult:
        reference = (
            row.ask_price
            if row.ask_price is not None and row.ask_price > 0
            else row.last_price
        )
        if reference is None:
            return FillResult(
                quantity=0,
                remaining_quantity=requested_quantity,
                fill_status="CANCELLED",
                fill_price=None,
                fill_reference_price=None,
                fill_slippage_pct=None,
                transaction_cost=0.0,
            )
        quantity, remaining_quantity, fill_status = self._capped_fill_quantity(
            row=row,
            requested_quantity=requested_quantity,
        )
        spread_abs = self._spread_abs(row, reference=reference)
        slippage_pct = (
            (spread_abs / reference)
            * self._session_spread_multiplier(market_phase=market_phase)
            * 100.0
            if reference > 0 and spread_abs > 0
            else None
        )
        notional = reference * quantity
        return FillResult(
            quantity=quantity,
            remaining_quantity=remaining_quantity,
            fill_status=fill_status,
            fill_price=reference,
            fill_reference_price=reference,
            fill_slippage_pct=slippage_pct,
            transaction_cost=self._transaction_cost(notional),
        )

    def sell(
        self,
        *,
        position: PaperPosition,
        row: MarketActivity | None,
        reason: str,
        market_phase: str,
        requested_quantity: int,
    ) -> FillResult:
        reference: float | None = None
        if row is not None and row.bid_price is not None and row.bid_price > 0:
            reference = row.bid_price
        elif row is not None and row.last_price is not None and row.last_price > 0:
            reference = row.last_price
        else:
            reference = position.last_price or position.average_entry_price
        if reference is None or reference <= 0:
            return FillResult(
                quantity=0,
                remaining_quantity=requested_quantity,
                fill_status="CANCELLED",
                fill_price=None,
                fill_reference_price=None,
                fill_slippage_pct=None,
                transaction_cost=0.0,
            )
        quantity, remaining_quantity, fill_status = self._capped_fill_quantity(
            row=row,
            requested_quantity=requested_quantity,
        )
        halt_resume_elapsed_seconds = self._halt_resume_elapsed_seconds(row)
        halt_resume = halt_resume_elapsed_seconds is not None
        spread_abs = self._spread_abs(row, reference=reference) if row is not None else 0.0
        adjustment = 0.0
        if (
            reason == "stop_loss"
            and position.stop_price is not None
            and row is not None
            and row.last_price is not None
            and row.last_price < position.stop_price
        ):
            adjustment = max(
                adjustment,
                reference * (self.settings.paper_stop_gap_slippage_pct / 100.0),
            )
        if halt_resume:
            adjustment = max(
                adjustment,
                self._spread_penalty_abs(
                    spread_abs=spread_abs,
                    market_phase=market_phase,
                    halt_resume_elapsed_seconds=halt_resume_elapsed_seconds,
                ),
            )
        fill_price = max(reference - adjustment, 0.0001)
        slippage_pct = (
            ((reference - fill_price) / reference) * 100.0
            if reference > 0 and fill_price < reference
            else (
                (spread_abs / reference)
                * self._session_spread_multiplier(
                    market_phase=market_phase,
                    halt_resume=halt_resume,
                )
                * 100.0
                if reference > 0 and spread_abs > 0
                else None
            )
        )
        notional = fill_price * quantity
        return FillResult(
            quantity=quantity,
            remaining_quantity=remaining_quantity,
            fill_status=fill_status,
            fill_price=fill_price,
            fill_reference_price=reference,
            fill_slippage_pct=slippage_pct,
            transaction_cost=self._transaction_cost(notional),
        )

    def _spread_abs(
        self,
        row: MarketActivity,
        *,
        reference: float | None,
    ) -> float:
        if row.ask_price is not None and row.bid_price is not None and row.ask_price >= row.bid_price:
            return max(row.ask_price - row.bid_price, 0.0)
        if row.spread_pct is not None and reference is not None and reference > 0:
            return max(reference * float(row.spread_pct), 0.0)
        return 0.0

    def _session_spread_multiplier(
        self,
        *,
        market_phase: str,
        halt_resume: bool = False,
    ) -> float:
        if halt_resume:
            return self.settings.paper_halt_resume_spread_slippage_multiplier
        if market_phase == "premarket":
            return self.settings.paper_premarket_spread_slippage_multiplier
        return self.settings.paper_regular_spread_slippage_multiplier

    def _spread_penalty_abs(
        self,
        *,
        spread_abs: float,
        market_phase: str,
        halt_resume_elapsed_seconds: float | None = None,
    ) -> float:
        if halt_resume_elapsed_seconds is not None:
            elapsed_minutes = max(halt_resume_elapsed_seconds, 0.0) / 60.0
            decay = math.exp(-elapsed_minutes / self.settings.paper_halt_resume_decay_minutes)
            return max(
                spread_abs
                * self.settings.paper_halt_resume_spread_slippage_multiplier
                * decay,
                0.0,
            )
        multiplier = self._session_spread_multiplier(
            market_phase=market_phase,
            halt_resume=False,
        )
        extra_multiplier = max(multiplier - 1.0, 0.0)
        return max(spread_abs * extra_multiplier, 0.0)

    def _is_halt_resume_row(self, row: MarketActivity | None) -> bool:
        if row is None:
            return False
        normalized = normalize_market_status(row.market_status)
        if "resume" in normalized or "resumed" in normalized:
            return True
        return any("resume" in str(reason).lower() for reason in row.reasons)

    def _halt_resume_elapsed_seconds(self, row: MarketActivity | None) -> float | None:
        if not self._is_halt_resume_row(row):
            return None
        if row is None:
            return None
        if row.resume_elapsed_seconds is not None:
            return max(float(row.resume_elapsed_seconds), 0.0)
        for reason in row.reasons:
            text = str(reason).strip().lower()
            if text.startswith("resume_elapsed_seconds:"):
                return max(float(text.split(":", 1)[1]), 0.0)
            if text.startswith("resume_elapsed_minutes:"):
                return max(float(text.split(":", 1)[1]) * 60.0, 0.0)
        return 0.0

    def _volume_cap_pct(self, row: MarketActivity) -> float:
        dollar_volume = float(row.dollar_volume or 0.0)
        if dollar_volume >= self.settings.paper_volume_cap_large_dollar_volume:
            volume_cap_pct = self.settings.paper_volume_cap_large_pct / 100.0
        elif dollar_volume >= self.settings.paper_volume_cap_mid_dollar_volume:
            volume_cap_pct = self.settings.paper_volume_cap_mid_pct / 100.0
        else:
            volume_cap_pct = self.settings.paper_volume_cap_small_pct / 100.0
        market_cap_cap_pct = self._market_cap_cap_pct(
            row,
            volume_cap_pct=volume_cap_pct,
        )
        return min(volume_cap_pct, market_cap_cap_pct)

    def _market_cap_cap_pct(
        self,
        row: MarketActivity,
        *,
        volume_cap_pct: float,
    ) -> float:
        if (
            row.market_cap is None
            or row.market_cap >= self.settings.paper_volume_cap_small_market_cap_threshold
        ):
            return volume_cap_pct
        return volume_cap_pct * self.settings.paper_volume_cap_small_market_cap_scale

    def _capped_fill_quantity(
        self,
        *,
        row: MarketActivity | None,
        requested_quantity: int,
    ) -> tuple[int, int, str]:
        if requested_quantity <= 0:
            return 0, 0, "CANCELLED"
        if row is None or row.volume is None or row.volume <= 0:
            return requested_quantity, 0, "FILLED"
        max_fill = max(int(float(row.volume) * self._volume_cap_pct(row)), 1)
        quantity = min(requested_quantity, max_fill)
        remaining_quantity = max(requested_quantity - quantity, 0)
        return quantity, remaining_quantity, ("PARTIAL" if remaining_quantity > 0 else "FILLED")

    def _transaction_cost(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        variable_fee = notional * (self.settings.paper_notional_fee_rate_pct / 100.0)
        return max(self.settings.paper_min_trade_fee, variable_fee)
