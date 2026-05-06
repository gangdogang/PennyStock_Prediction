from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal


SubscriptionTier = Literal["tier1_continuous", "tier2_rotation"]
PriorityMetric = Literal["dollar_volume", "watchlist_rank", "volatility"]


@dataclass(frozen=True, slots=True)
class SubscriptionSlot:
    tier: SubscriptionTier
    symbol: str
    last_subscribed_at: datetime | None = None
    last_unsubscribed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RotationPolicy:
    tier1_size: int = 30
    tier2_window_seconds: int = 300
    tier2_concurrent: int = 10
    priority_metric: PriorityMetric = "dollar_volume"


class KisWebSocketRotationManager:
    def __init__(
        self,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._policy: RotationPolicy | None = None
        self._tier2_order: list[str] = []
        self._slots: dict[str, SubscriptionSlot] = {}

    def assign_tiers(
        self,
        universe: list[str],
        priority_data: dict[str, float],
        policy: RotationPolicy,
    ) -> dict[str, SubscriptionSlot]:
        now = self._now()
        symbols = _dedupe_symbols(universe)
        priority_by_symbol = {
            str(symbol).upper(): float(value)
            for symbol, value in priority_data.items()
            if str(symbol).strip()
        }
        original_index = {symbol: index for index, symbol in enumerate(symbols)}
        ordered = sorted(
            symbols,
            key=lambda symbol: (-priority_by_symbol.get(symbol, 0.0), original_index[symbol]),
        )
        tier1_size = min(max(int(policy.tier1_size), 0), len(ordered))
        tier2_concurrent = max(int(policy.tier2_concurrent), 0)
        tier1_symbols = set(ordered[:tier1_size])
        tier2_symbols = ordered[tier1_size:]
        active_tier2 = set(tier2_symbols[:tier2_concurrent])

        slots: dict[str, SubscriptionSlot] = {}
        for symbol in ordered:
            if symbol in tier1_symbols:
                slots[symbol] = SubscriptionSlot(
                    tier="tier1_continuous",
                    symbol=symbol,
                    last_subscribed_at=now,
                    last_unsubscribed_at=None,
                )
            elif symbol in active_tier2:
                slots[symbol] = SubscriptionSlot(
                    tier="tier2_rotation",
                    symbol=symbol,
                    last_subscribed_at=now,
                    last_unsubscribed_at=None,
                )
            else:
                slots[symbol] = SubscriptionSlot(
                    tier="tier2_rotation",
                    symbol=symbol,
                    last_subscribed_at=None,
                    last_unsubscribed_at=now,
                )

        self._policy = policy
        self._tier2_order = tier2_symbols
        self._slots = dict(slots)
        return dict(slots)

    def next_rotation_step(
        self,
        current_slots: dict[str, SubscriptionSlot],
        elapsed_seconds: int,
    ) -> dict[str, SubscriptionSlot]:
        policy = self._policy or _policy_from_slots(current_slots)
        tier2_order = [symbol for symbol in self._tier2_order if symbol in current_slots]
        if not tier2_order:
            tier2_order = [
                symbol
                for symbol, slot in current_slots.items()
                if slot.tier == "tier2_rotation"
            ]
        tier2_concurrent = max(int(policy.tier2_concurrent), 0)
        window_seconds = max(int(policy.tier2_window_seconds), 1)
        if (
            elapsed_seconds < window_seconds
            or not tier2_order
            or tier2_concurrent <= 0
            or tier2_concurrent >= len(tier2_order)
        ):
            self._slots = dict(current_slots)
            self._tier2_order = tier2_order
            self._policy = policy
            return dict(current_slots)

        steps = max(int(elapsed_seconds // window_seconds), 1)
        current_active = [
            symbol
            for symbol in tier2_order
            if _is_active(current_slots[symbol])
        ]
        if current_active:
            current_start = tier2_order.index(current_active[0])
            next_start = (current_start + steps * tier2_concurrent) % len(tier2_order)
        else:
            next_start = 0
        next_active = {
            tier2_order[(next_start + offset) % len(tier2_order)]
            for offset in range(min(tier2_concurrent, len(tier2_order)))
        }

        now = self._now()
        rotated: dict[str, SubscriptionSlot] = {}
        for symbol, slot in current_slots.items():
            if slot.tier == "tier1_continuous":
                rotated[symbol] = slot
                continue
            was_active = _is_active(slot)
            should_be_active = symbol in next_active
            if was_active and not should_be_active:
                rotated[symbol] = SubscriptionSlot(
                    tier=slot.tier,
                    symbol=slot.symbol,
                    last_subscribed_at=slot.last_subscribed_at,
                    last_unsubscribed_at=now,
                )
            elif should_be_active and not was_active:
                rotated[symbol] = SubscriptionSlot(
                    tier=slot.tier,
                    symbol=slot.symbol,
                    last_subscribed_at=now,
                    last_unsubscribed_at=slot.last_unsubscribed_at,
                )
            else:
                rotated[symbol] = slot

        self._slots = dict(rotated)
        self._tier2_order = tier2_order
        self._policy = policy
        return dict(rotated)

    def stamp_quote(self, quote_row: dict[str, Any]) -> dict[str, Any]:
        symbol = str(quote_row.get("symbol") or "").upper()
        slot = self._slots.get(symbol)
        tier = slot.tier if slot is not None else quote_row.get("tier")
        stamped = dict(quote_row)
        stamped["subscription_continuous"] = True if tier is None else tier == "tier1_continuous"
        return stamped

    def active_symbols(
        self,
        current_slots: dict[str, SubscriptionSlot] | None = None,
    ) -> list[str]:
        slots = current_slots or self._slots
        return [
            symbol
            for symbol, slot in slots.items()
            if slot.tier == "tier1_continuous" or _is_active(slot)
        ]

    def slot_counts(
        self,
        current_slots: dict[str, SubscriptionSlot] | None = None,
    ) -> dict[str, int]:
        slots = current_slots or self._slots
        return {
            "tier1_continuous_symbol_count": sum(
                1 for slot in slots.values() if slot.tier == "tier1_continuous"
            ),
            "tier2_rotation_symbol_count": sum(
                1 for slot in slots.values() if slot.tier == "tier2_rotation"
            ),
        }

    def rotation_gap_seconds_p90(
        self,
        current_slots: dict[str, SubscriptionSlot] | None = None,
        *,
        now: datetime | None = None,
    ) -> float | None:
        slots = current_slots or self._slots
        observed_at = now or self._now()
        gaps: list[float] = []
        for slot in slots.values():
            if slot.tier != "tier2_rotation" or _is_active(slot):
                continue
            if slot.last_unsubscribed_at is None:
                continue
            gaps.append(max((observed_at - slot.last_unsubscribed_at).total_seconds(), 0.0))
        if not gaps:
            return None
        return _percentile(sorted(gaps), 0.90)

    def _now(self) -> datetime:
        now = self._now_provider()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now


def _dedupe_symbols(universe: list[str]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for value in universe:
        symbol = str(value).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _is_active(slot: SubscriptionSlot) -> bool:
    if slot.last_subscribed_at is None:
        return False
    if slot.last_unsubscribed_at is None:
        return True
    return slot.last_subscribed_at > slot.last_unsubscribed_at


def _policy_from_slots(slots: dict[str, SubscriptionSlot]) -> RotationPolicy:
    return RotationPolicy(
        tier1_size=sum(1 for slot in slots.values() if slot.tier == "tier1_continuous"),
        tier2_concurrent=sum(
            1 for slot in slots.values() if slot.tier == "tier2_rotation" and _is_active(slot)
        ),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight
