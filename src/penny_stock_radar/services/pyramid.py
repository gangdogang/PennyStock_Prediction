from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
from typing import Literal


class PyramidStage(str, Enum):
    STARTER = "starter"
    ADD_1 = "add_1"
    ADD_2 = "add_2"
    TRIM_1 = "trim_1"
    TRIM_2 = "trim_2"
    RUNNER = "runner"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class PyramidLeg:
    leg_index: int
    stage: PyramidStage
    entry_price: float
    entry_at: datetime
    size_shares: int
    initial_stop: float
    side: Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class AddLevel:
    trigger_kind: str
    threshold: float
    size_fraction: float
    new_stop_rule: str


@dataclass(frozen=True, slots=True)
class TrimLevel:
    trigger_kind: str
    threshold: float
    fraction_of_remaining: float


@dataclass(frozen=True, slots=True)
class RunnerTrail:
    kind: str
    multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class PyramidSchedule:
    starter_size: float = 1.0
    add_levels: tuple[AddLevel, ...] = ()
    trim_levels: tuple[TrimLevel, ...] = ()
    runner_trail: RunnerTrail = RunnerTrail(kind="none")


LEGACY_SCHEDULE = PyramidSchedule(starter_size=1.0)


AGGRESSIVE_DEFAULT_SCHEDULE = PyramidSchedule(
    starter_size=0.25,
    add_levels=(
        AddLevel(
            trigger_kind="r_multiple_above",
            threshold=0.5,
            size_fraction=0.25,
            new_stop_rule="starter_entry",
        ),
        AddLevel(
            trigger_kind="r_multiple_above",
            threshold=1.0,
            size_fraction=0.50,
            new_stop_rule="leg_minus_1_entry",
        ),
    ),
    trim_levels=(
        TrimLevel(
            trigger_kind="r_multiple_above",
            threshold=2.0,
            fraction_of_remaining=0.33,
        ),
        TrimLevel(
            trigger_kind="r_multiple_above",
            threshold=3.0,
            fraction_of_remaining=0.50,
        ),
    ),
    runner_trail=RunnerTrail(kind="structure", multiplier=1.0),
)


@dataclass(slots=True)
class PyramidPosition:
    symbol: str
    side: Literal["long", "short"]
    setup_id: str
    schedule: PyramidSchedule
    full_intended_size: int
    starter_entry_price: float
    starter_stop_price: float
    legs: list[PyramidLeg] = field(default_factory=list)
    closed_legs: list[PyramidLeg] = field(default_factory=list)
    current_stop: float | None = None

    @property
    def total_shares(self) -> int:
        return sum(leg.size_shares for leg in self.legs)

    @property
    def average_price(self) -> float:
        if not self.legs:
            return 0.0
        notional = sum(leg.entry_price * leg.size_shares for leg in self.legs)
        return notional / self.total_shares

    @property
    def realized_pnl(self) -> float:
        sign = 1 if self.side == "long" else -1
        return sum(
            (leg.entry_price - self.starter_entry_price) * leg.size_shares * sign
            for leg in self.closed_legs
        )

    @property
    def initial_risk_per_share(self) -> float:
        return abs(self.starter_entry_price - self.starter_stop_price)

    def current_r_multiple(self, current_price: float) -> float:
        risk = self.initial_risk_per_share
        if risk == 0:
            return 0.0
        sign = 1 if self.side == "long" else -1
        return sign * (current_price - self.starter_entry_price) / risk

    def can_add(
        self,
        *,
        current_price: float,
        vwap: float | None,
        hod: float | None,
        now: datetime,
    ) -> AddLevel | None:
        next_index = len(self.legs)
        next_add_position = next_index - 1
        if next_add_position < 0 or next_add_position >= len(self.schedule.add_levels):
            return None
        add = self.schedule.add_levels[next_add_position]
        if add.trigger_kind == "r_multiple_above":
            return add if self.current_r_multiple(current_price) >= add.threshold else None
        if add.trigger_kind == "vwap_reclaim" and vwap is not None:
            return add if current_price > vwap else None
        if add.trigger_kind == "hod_break" and hod is not None:
            return add if current_price > hod else None
        if add.trigger_kind == "time_after_entry" and self.legs:
            elapsed_min = (now - self.legs[0].entry_at).total_seconds() / 60
            return add if elapsed_min >= add.threshold else None
        return None

    def stop_for_add(
        self,
        add: AddLevel,
        *,
        current_price: float,
        vwap: float | None = None,
        structure_low: float | None = None,
    ) -> float:
        if add.new_stop_rule == "starter_entry":
            return self.starter_entry_price
        if add.new_stop_rule == "leg_minus_1_entry" and self.legs:
            return self.legs[-1].entry_price
        if add.new_stop_rule == "structure_low" and structure_low is not None:
            return structure_low
        if add.new_stop_rule == "vwap" and vwap is not None:
            return vwap
        if add.new_stop_rule == "keep" and self.current_stop is not None:
            return self.current_stop
        return self.current_stop if self.current_stop is not None else current_price

    def can_trim(self, current_price: float) -> TrimLevel | None:
        trims_done = sum(
            1
            for leg in self.closed_legs
            if leg.stage in (PyramidStage.TRIM_1, PyramidStage.TRIM_2)
        )
        if trims_done >= len(self.schedule.trim_levels):
            return None
        trim = self.schedule.trim_levels[trims_done]
        if trim.trigger_kind == "r_multiple_above":
            return trim if self.current_r_multiple(current_price) >= trim.threshold else None
        return None

    def add_leg(
        self,
        *,
        price: float,
        shares: int,
        stop: float,
        at: datetime,
        stage: PyramidStage,
    ) -> PyramidLeg:
        leg = PyramidLeg(
            leg_index=len(self.legs),
            stage=stage,
            entry_price=price,
            entry_at=at,
            size_shares=shares,
            initial_stop=stop,
            side=self.side,
        )
        self.legs.append(leg)
        self.current_stop = stop
        return leg

    def trim(
        self,
        *,
        fraction: float,
        price: float,
        at: datetime,
        stage: PyramidStage,
    ) -> list[PyramidLeg]:
        target = int(self.total_shares * fraction)
        closed: list[PyramidLeg] = []
        remaining = target
        new_legs: list[PyramidLeg] = []
        for leg in self.legs:
            if remaining <= 0:
                new_legs.append(leg)
                continue
            if leg.size_shares <= remaining:
                closed.append(
                    PyramidLeg(
                        leg_index=leg.leg_index,
                        stage=stage,
                        entry_price=price,
                        entry_at=at,
                        size_shares=leg.size_shares,
                        initial_stop=leg.initial_stop,
                        side=leg.side,
                    )
                )
                remaining -= leg.size_shares
                continue
            closed.append(
                PyramidLeg(
                    leg_index=leg.leg_index,
                    stage=stage,
                    entry_price=price,
                    entry_at=at,
                    size_shares=remaining,
                    initial_stop=leg.initial_stop,
                    side=leg.side,
                )
            )
            new_legs.append(
                PyramidLeg(
                    leg_index=leg.leg_index,
                    stage=leg.stage,
                    entry_price=leg.entry_price,
                    entry_at=leg.entry_at,
                    size_shares=leg.size_shares - remaining,
                    initial_stop=leg.initial_stop,
                    side=leg.side,
                )
            )
            remaining = 0
        self.legs = new_legs
        self.closed_legs.extend(closed)
        return closed

    def close_all(self, *, price: float, at: datetime) -> list[PyramidLeg]:
        closed = [
            PyramidLeg(
                leg_index=leg.leg_index,
                stage=PyramidStage.CLOSED,
                entry_price=price,
                entry_at=at,
                size_shares=leg.size_shares,
                initial_stop=leg.initial_stop,
                side=leg.side,
            )
            for leg in self.legs
        ]
        self.closed_legs.extend(closed)
        self.legs = []
        return closed

    def to_state_json(self) -> str:
        return json.dumps(
            {
                "symbol": self.symbol,
                "side": self.side,
                "setup_id": self.setup_id,
                "starter_entry_price": self.starter_entry_price,
                "starter_stop_price": self.starter_stop_price,
                "current_stop": self.current_stop,
                "legs": [_leg_to_dict(leg) for leg in self.legs],
                "closed_legs": [_leg_to_dict(leg) for leg in self.closed_legs],
            },
            sort_keys=True,
        )


def _leg_to_dict(leg: PyramidLeg) -> dict[str, object]:
    return {
        "leg_index": leg.leg_index,
        "stage": leg.stage.value,
        "entry_price": leg.entry_price,
        "entry_at": leg.entry_at.isoformat(),
        "size_shares": leg.size_shares,
        "initial_stop": leg.initial_stop,
        "side": leg.side,
    }
