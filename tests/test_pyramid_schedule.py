from __future__ import annotations

from datetime import datetime, timezone

from penny_stock_radar.services.pyramid import (
    AGGRESSIVE_DEFAULT_SCHEDULE,
    LEGACY_SCHEDULE,
    AddLevel,
    PyramidPosition,
    PyramidSchedule,
    PyramidStage,
)


NOW = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)


def _position(schedule: PyramidSchedule) -> PyramidPosition:
    position = PyramidPosition(
        symbol="AAA",
        side="long",
        setup_id="schedule_test",
        schedule=schedule,
        full_intended_size=400,
        starter_entry_price=10.0,
        starter_stop_price=9.0,
    )
    position.add_leg(
        price=10.0,
        shares=int(400 * schedule.starter_size),
        stop=9.0,
        at=NOW,
        stage=PyramidStage.STARTER,
    )
    return position


def test_legacy_schedule_never_adds_on_large_fake_price_sequence() -> None:
    position = _position(LEGACY_SCHEDULE)

    for price in (10.5, 11.0, 12.0, 20.0):
        assert position.can_add(current_price=price, vwap=price - 0.1, hod=price - 0.1, now=NOW) is None


def test_aggressive_default_first_add_triggers_at_half_r() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)

    assert position.can_add(current_price=10.49, vwap=None, hod=None, now=NOW) is None
    assert (
        position.can_add(current_price=10.50, vwap=None, hod=None, now=NOW)
        == AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[0]
    )


def test_aggressive_default_second_add_triggers_at_one_r_after_first_add() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)
    first = AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[0]
    position.add_leg(
        price=10.5,
        shares=int(400 * first.size_fraction),
        stop=position.stop_for_add(first, current_price=10.5),
        at=NOW,
        stage=PyramidStage.ADD_1,
    )

    assert position.can_add(current_price=10.99, vwap=None, hod=None, now=NOW) is None
    assert (
        position.can_add(current_price=11.00, vwap=None, hod=None, now=NOW)
        == AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[1]
    )


def test_aggressive_default_trim_triggers_at_two_r() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)

    assert position.can_trim(11.99) is None
    assert position.can_trim(12.00) == AGGRESSIVE_DEFAULT_SCHEDULE.trim_levels[0]


def test_aggressive_default_second_trim_triggers_at_three_r_after_first_trim() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)
    first_trim = AGGRESSIVE_DEFAULT_SCHEDULE.trim_levels[0]
    position.trim(
        fraction=first_trim.fraction_of_remaining,
        price=12.0,
        at=NOW,
        stage=PyramidStage.TRIM_1,
    )

    assert position.can_trim(12.99) is None
    assert position.can_trim(13.00) == AGGRESSIVE_DEFAULT_SCHEDULE.trim_levels[1]


def test_r_multiple_trigger_below_threshold_does_not_add() -> None:
    schedule = PyramidSchedule(
        add_levels=(AddLevel("r_multiple_above", 2.0, 0.25, "keep"),)
    )
    position = _position(schedule)

    assert position.can_add(current_price=11.99, vwap=None, hod=None, now=NOW) is None


def test_first_add_stop_ratchets_to_starter_entry() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)
    add = AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[0]

    assert position.stop_for_add(add, current_price=10.5) == 10.0


def test_second_add_stop_ratchets_to_prior_leg_entry() -> None:
    position = _position(AGGRESSIVE_DEFAULT_SCHEDULE)
    first = AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[0]
    position.add_leg(
        price=10.5,
        shares=int(400 * first.size_fraction),
        stop=position.stop_for_add(first, current_price=10.5),
        at=NOW,
        stage=PyramidStage.ADD_1,
    )
    second = AGGRESSIVE_DEFAULT_SCHEDULE.add_levels[1]

    assert position.stop_for_add(second, current_price=11.0) == 10.5


def test_stop_rule_keep_vwap_and_structure_low() -> None:
    keep = AddLevel("r_multiple_above", 0.5, 0.25, "keep")
    vwap = AddLevel("r_multiple_above", 0.5, 0.25, "vwap")
    structure = AddLevel("r_multiple_above", 0.5, 0.25, "structure_low")
    position = _position(PyramidSchedule(add_levels=(keep,)))

    assert position.stop_for_add(keep, current_price=10.5) == 9.0
    assert position.stop_for_add(vwap, current_price=10.5, vwap=10.2) == 10.2
    assert position.stop_for_add(structure, current_price=10.5, structure_low=10.1) == 10.1
