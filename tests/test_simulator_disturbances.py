"""Tests for Simulator disturbance management (Task 9, Property 14 of Task 11).

Covers Property 6 (total contribution is the arithmetic sum), Property 13
(membership takes effect on the next step), Property 14 (invalid returns are
excluded) and the "disturbance not found" example (Requirement 5.8).
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.disturbances import Person_Disturbance
from tests.strategies import (
    FixedDisturbance,
    configured_simulator,
    disturbance_sets,
    disturbances,
    durations,
    moisture_rates,
)

# A large, cold-coil-free room so additions are never capped or removed:
# saturation at 100 deg C over 100000 m^3 is far above any test addition, and
# a zero-flow fan moves no air across the coil.
_OPEN_ROOM = (
    RoomConfig(100000.0, 100.0, 0.0),
    FanConfig(0.0),
    CoilConfig(5.0, 0.0),
    TimeStepConfig(1.0),
)


# Feature: room-humidity-simulator, Property 6: Total disturbance contribution is the arithmetic sum
@given(ds=disturbance_sets(100), dt=durations)
def test_total_contribution_is_arithmetic_sum(ds, dt):
    sim = configured_simulator(_OPEN_ROOM)
    for d in ds:
        assert sim.add_disturbance(d).is_ok
    assert len(sim.active_disturbances) == len(ds)

    sim.advance(dt=dt)

    expected = math.fsum(d.contribution(dt) for d in ds)
    assert math.isclose(sim.last_step.added_grams, expected, rel_tol=1e-9, abs_tol=1e-9)


# Feature: room-humidity-simulator, Property 13: Disturbance membership takes effect on the next step
@given(
    base=disturbance_sets(10),
    rate=moisture_rates.filter(lambda r: r > 0.0),
    steps_before=st.integers(min_value=0, max_value=3),
)
def test_membership_takes_effect_on_next_step(base, rate, steps_before):
    sim = configured_simulator(_OPEN_ROOM)
    for d in base:
        sim.add_disturbance(d)
    if steps_before:
        sim.advance(n=steps_before)
    base_total = math.fsum(d.contribution(1.0) for d in base)

    newcomer = Person_Disturbance(rate)
    before = (sim.room.exact_absolute_humidity_grams, sim.simulation_time)
    sim.add_disturbance(newcomer)
    # Adding does not touch the room until the next step.
    assert (sim.room.exact_absolute_humidity_grams, sim.simulation_time) == before

    sim.advance()
    assert math.isclose(sim.last_step.added_grams, base_total + rate, rel_tol=1e-9, abs_tol=1e-9)

    assert sim.remove_disturbance(newcomer).is_ok
    sim.advance()
    assert math.isclose(sim.last_step.added_grams, base_total, rel_tol=1e-9, abs_tol=1e-9)


_INVALID_VALUES = st.one_of(
    st.floats(max_value=0.0, exclude_max=True, allow_nan=False, allow_infinity=False),
    st.sampled_from([math.nan, math.inf, -math.inf, None, "3 g", [1.0]]),
)


# Feature: room-humidity-simulator, Property 14: Invalid disturbance returns are excluded
@given(
    valid=st.lists(disturbances(), max_size=20),
    invalid_values=st.lists(_INVALID_VALUES, min_size=1, max_size=5),
    data=st.data(),
)
def test_invalid_disturbance_returns_are_excluded(valid, invalid_values, data):
    sim = configured_simulator(_OPEN_ROOM)
    invalid = [FixedDisturbance(v) for v in invalid_values]
    mixed = data.draw(st.permutations(valid + invalid))
    for d in mixed:
        assert sim.add_disturbance(d).is_ok
    before = sim.room.exact_absolute_humidity_grams

    sim.advance()

    expected = math.fsum(d.contribution(1.0) for d in valid)
    assert math.isclose(sim.last_step.added_grams, expected, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(
        sim.room.exact_absolute_humidity_grams, before + expected, rel_tol=1e-9, abs_tol=1e-9
    )
    offenders = [e.disturbance for e in sim.last_step.disturbance_errors]
    assert len(offenders) == len(invalid)
    assert all(any(o is d for o in offenders) for d in invalid)


def test_raising_disturbance_is_excluded_and_identified():
    class Exploding(FixedDisturbance):
        def contribution(self, dt):
            raise RuntimeError("boom")

    sim = configured_simulator(_OPEN_ROOM)
    bad = Exploding(None)
    sim.add_disturbance(bad)
    sim.add_disturbance(Person_Disturbance(2.0))
    sim.advance()
    assert sim.last_step.added_grams == 2.0
    assert sim.last_step.disturbance_errors[0].disturbance is bad


def test_inactive_disturbance_does_not_contribute():
    sim = configured_simulator(_OPEN_ROOM)
    sim.add_disturbance(Person_Disturbance(5.0, active=False))
    sim.advance()
    assert sim.last_step.added_grams == 0.0


def test_remove_non_active_disturbance_returns_not_found():
    """Requirement 5.8."""
    sim = configured_simulator(_OPEN_ROOM)
    kept = Person_Disturbance(1.0)
    sim.add_disturbance(kept)
    result = sim.remove_disturbance(Person_Disturbance(1.0))
    assert result.is_error
    assert result.error.message == "disturbance not found"
    assert sim.active_disturbances == (kept,)


def test_maximum_active_disturbances():
    """Requirements 5.6, 6.5: up to 100 accepted, the 101st rejected."""
    sim = configured_simulator(_OPEN_ROOM)
    for _ in range(100):
        assert sim.add_disturbance(Person_Disturbance(0.1)).is_ok
    result = sim.add_disturbance(Person_Disturbance(0.1))
    assert result.error.message == "maximum active disturbances reached"
    assert len(sim.active_disturbances) == 100


def test_out_of_range_moisture_rate_rejected():
    """Requirement 5.7."""
    sim = configured_simulator(_OPEN_ROOM)
    result = sim.add_disturbance(Person_Disturbance(1000.5))
    assert result.error.message == "moisture rate out of range"
    assert sim.active_disturbances == ()


def test_custom_disturbance_without_rate_is_accepted():
    """Requirements 6.1, 6.3: any conforming object is incorporated."""
    sim = configured_simulator(_OPEN_ROOM)
    assert sim.add_disturbance(FixedDisturbance(7.5)).is_ok
    sim.advance()
    assert sim.last_step.added_grams == 7.5
