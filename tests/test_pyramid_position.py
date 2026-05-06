from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from penny_stock_radar.services.pyramid import (
    AddLevel,
    PyramidPosition,
    PyramidSchedule,
    PyramidStage,
    TrimLevel,
)


NOW = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)


def _position(
    *,
    side: str = "long",
    schedule: PyramidSchedule | None = None,
) -> PyramidPosition:
    position = PyramidPosition(
        symbol="AAA",
        side=side,  # type: ignore[arg-type]
        setup_id="test_setup",
        schedule=schedule or PyramidSchedule(),
        full_intended_size=300,
        starter_entry_price=10.0,
        starter_stop_price=9.0 if side == "long" else 11.0,
    )
    position.add_leg(
        price=10.0,
        shares=100,
        stop=9.0 if side == "long" else 11.0,
        at=NOW,
        stage=PyramidStage.STARTER,
    )
    return position


def test_add_leg_starter_sets_total_shares_and_stop() -> None:
    position = _position()

    assert position.total_shares == 100
    assert position.current_stop == 9.0


def test_add_leg_add_1_updates_weighted_average_price() -> None:
    position = _position()
    position.add_leg(price=12.0, shares=50, stop=10.0, at=NOW, stage=PyramidStage.ADD_1)

    assert position.total_shares == 150
    assert position.average_price == (10.0 * 100 + 12.0 * 50) / 150


def test_current_r_multiple_long_zero_one_and_negative_one() -> None:
    position = _position()

    assert position.current_r_multiple(10.0) == 0.0
    assert position.current_r_multiple(11.0) == 1.0
    assert position.current_r_multiple(9.0) == -1.0


def test_current_r_multiple_short_zero_one_and_negative_one() -> None:
    position = _position(side="short")

    assert position.current_r_multiple(10.0) == 0.0
    assert position.current_r_multiple(9.0) == 1.0
    assert position.current_r_multiple(11.0) == -1.0


def test_current_r_multiple_zero_risk_returns_zero() -> None:
    position = PyramidPosition(
        symbol="AAA",
        side="long",
        setup_id="test_setup",
        schedule=PyramidSchedule(),
        full_intended_size=100,
        starter_entry_price=10.0,
        starter_stop_price=10.0,
    )

    assert position.current_r_multiple(12.0) == 0.0


def test_can_add_r_multiple_above_triggers_and_rejects() -> None:
    schedule = PyramidSchedule(
        add_levels=(AddLevel("r_multiple_above", 0.5, 0.25, "starter_entry"),)
    )
    position = _position(schedule=schedule)

    assert position.can_add(current_price=10.49, vwap=None, hod=None, now=NOW) is None
    assert position.can_add(current_price=10.50, vwap=None, hod=None, now=NOW) == schedule.add_levels[0]


def test_can_add_vwap_reclaim_triggers_and_rejects() -> None:
    schedule = PyramidSchedule(add_levels=(AddLevel("vwap_reclaim", 0.0, 0.25, "vwap"),))
    position = _position(schedule=schedule)

    assert position.can_add(current_price=10.0, vwap=10.0, hod=None, now=NOW) is None
    assert position.can_add(current_price=10.1, vwap=10.0, hod=None, now=NOW) == schedule.add_levels[0]


def test_can_add_hod_break_triggers_and_rejects() -> None:
    schedule = PyramidSchedule(add_levels=(AddLevel("hod_break", 0.0, 0.25, "keep"),))
    position = _position(schedule=schedule)

    assert position.can_add(current_price=10.0, vwap=None, hod=10.0, now=NOW) is None
    assert position.can_add(current_price=10.1, vwap=None, hod=10.0, now=NOW) == schedule.add_levels[0]


def test_can_add_time_after_entry_triggers_and_rejects() -> None:
    schedule = PyramidSchedule(
        add_levels=(AddLevel("time_after_entry", 15.0, 0.25, "keep"),)
    )
    position = _position(schedule=schedule)

    assert position.can_add(current_price=10.0, vwap=None, hod=None, now=NOW + timedelta(minutes=14)) is None
    assert position.can_add(current_price=10.0, vwap=None, hod=None, now=NOW + timedelta(minutes=15)) == schedule.add_levels[0]


def test_can_add_returns_none_when_schedule_has_no_next_add() -> None:
    position = _position(schedule=PyramidSchedule(add_levels=()))

    assert position.can_add(current_price=20.0, vwap=1.0, hod=1.0, now=NOW) is None


def test_can_trim_first_second_and_exhausted_levels() -> None:
    schedule = PyramidSchedule(
        trim_levels=(
            TrimLevel("r_multiple_above", 1.0, 0.25),
            TrimLevel("r_multiple_above", 2.0, 0.50),
        )
    )
    position = _position(schedule=schedule)

    assert position.can_trim(10.9) is None
    assert position.can_trim(11.0) == schedule.trim_levels[0]
    position.trim(fraction=0.25, price=11.0, at=NOW, stage=PyramidStage.TRIM_1)
    assert position.can_trim(11.9) is None
    assert position.can_trim(12.0) == schedule.trim_levels[1]
    position.trim(fraction=0.50, price=12.0, at=NOW, stage=PyramidStage.TRIM_2)
    assert position.can_trim(13.0) is None


def test_trim_fifo_splits_oldest_leg() -> None:
    position = _position()
    position.add_leg(price=11.0, shares=200, stop=10.0, at=NOW, stage=PyramidStage.ADD_1)

    closed = position.trim(fraction=0.33, price=12.0, at=NOW, stage=PyramidStage.TRIM_1)

    assert [leg.size_shares for leg in closed] == [99]
    assert closed[0].leg_index == 0
    assert [leg.size_shares for leg in position.legs] == [1, 200]
    assert position.closed_legs == closed


def test_trim_fifo_closes_multiple_legs() -> None:
    position = _position()
    position.add_leg(price=11.0, shares=50, stop=10.0, at=NOW, stage=PyramidStage.ADD_1)

    closed = position.trim(fraction=0.80, price=12.0, at=NOW, stage=PyramidStage.TRIM_1)

    assert [leg.size_shares for leg in closed] == [100, 20]
    assert [leg.size_shares for leg in position.legs] == [30]


def test_close_all_moves_all_open_legs_to_closed() -> None:
    position = _position()
    position.add_leg(price=11.0, shares=50, stop=10.0, at=NOW, stage=PyramidStage.ADD_1)

    closed = position.close_all(price=12.0, at=NOW)

    assert [leg.stage for leg in closed] == [PyramidStage.CLOSED, PyramidStage.CLOSED]
    assert position.legs == []
    assert position.closed_legs == closed


def test_realized_pnl_uses_starter_r_reference_for_long_and_short() -> None:
    long_position = _position()
    long_position.trim(fraction=0.50, price=12.0, at=NOW, stage=PyramidStage.TRIM_1)
    short_position = _position(side="short")
    short_position.trim(fraction=0.50, price=8.0, at=NOW, stage=PyramidStage.TRIM_1)

    assert long_position.realized_pnl == 100.0
    assert short_position.realized_pnl == 100.0


def test_to_state_json_is_parseable_and_contains_legs() -> None:
    position = _position()
    position.add_leg(price=11.0, shares=50, stop=10.0, at=NOW, stage=PyramidStage.ADD_1)
    position.trim(fraction=0.25, price=12.0, at=NOW, stage=PyramidStage.TRIM_1)

    payload = json.loads(position.to_state_json())

    assert payload["symbol"] == "AAA"
    assert payload["setup_id"] == "test_setup"
    assert payload["legs"]
    assert payload["closed_legs"]
