"""Property tests for the Simulator step engine (Task 11).

Covers Properties 1, 2, 3, 9 and 10. Each test drives the real Simulator
with Hypothesis-generated configurations and disturbance sets.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from tests.strategies import (
    capacities,
    configurations,
    configured_simulator,
    disturbance_sets,
    durations,
    flow_rates,
    initial_humidities,
    temperatures,
    volumes,
)

_STEP_COUNTS = st.integers(min_value=1, max_value=20)


def _saturation(sim) -> float:
    return psychrometrics.saturation_content_grams(sim.room.temperature, sim.room.volume)


# Feature: room-humidity-simulator, Property 1: Reported humidity stays within bounds
@given(
    config=configurations(),
    ds=disturbance_sets(20),
    advances=st.lists(st.tuples(durations, _STEP_COUNTS), min_size=1, max_size=4),
)
def test_reported_humidity_within_bounds(config, ds, advances):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    for dt, n in advances:
        state = sim.advance(dt=dt, n=n).value
        assert 0.0 <= state.absolute_humidity_grams <= 999.9
        assert 0.0 <= state.relative_humidity_pct <= 100.0


# Feature: room-humidity-simulator, Property 2: Absolute humidity is never negative
@given(
    volume=volumes,
    temperature=temperatures,
    humidity=initial_humidities,
    flow=flow_rates,
    capacity=st.one_of(capacities, st.just(1e9)),
    coil_offset=st.floats(min_value=0.0, max_value=150.0),
    dt=durations,
    n=st.integers(min_value=1, max_value=200),
)
def test_absolute_humidity_never_negative(
    volume, temperature, humidity, flow, capacity, coil_offset, dt, n
):
    # A very cold, high-capacity coil and no disturbances: an aggressive,
    # disturbance-free dehumidification run.
    sim = configured_simulator(
        (
            RoomConfig(volume, temperature, humidity),
            FanConfig(flow),
            CoilConfig(temperature - coil_offset, capacity),
            TimeStepConfig(dt),
        )
    )
    for _ in range(n):
        sim.advance()
        assert sim.room.exact_absolute_humidity_grams >= 0.0


# Feature: room-humidity-simulator, Property 3: Saturation cap
@given(config=configurations(), ds=disturbance_sets(20), dt=durations)
def test_saturation_cap(config, ds, dt):
    room_cfg, _, coil_cfg, step_cfg = config
    # No removal so the post-addition state is observable at step end.
    sim = configured_simulator((room_cfg, FanConfig(0.0), coil_cfg, step_cfg))
    for d in ds:
        sim.add_disturbance(d)
    before = sim.room.exact_absolute_humidity_grams
    sim.advance(dt=dt)

    cap = _saturation(sim)
    after = sim.room.exact_absolute_humidity_grams
    assert after <= cap
    if before + sim.last_step.added_grams > cap:
        assert after == cap
        assert sim.room_state().relative_humidity_pct == 100.0


# Feature: room-humidity-simulator, Property 9: Step mass balance and update order
@given(config=configurations(), ds=disturbance_sets(20), steps_before=st.integers(0, 3))
def test_step_mass_balance_and_update_order(config, ds, steps_before):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    if steps_before:
        sim.advance(n=steps_before)
    h0 = sim.room.exact_absolute_humidity_grams
    sim.advance()
    step = sim.last_step
    cap = _saturation(sim)

    h1 = min(h0 + step.added_grams, cap)
    assert step.humidity_before_grams == h0
    assert step.humidity_after_additions_grams == h1
    # Removal is computed against the post-addition state.
    assert step.removed_grams <= h1 + 1e-9
    assert sim.room.exact_absolute_humidity_grams == max(h1 - step.removed_grams, 0.0)


# Feature: room-humidity-simulator, Property 10: Step composition and simulation-time accumulation
@given(config=configurations(), ds=disturbance_sets(20), n=st.integers(1, 15), dt=durations)
def test_step_composition_and_time_accumulation(config, ds, n, dt):
    batched = configured_simulator(config)
    stepped = configured_simulator(config)
    for d in ds:
        batched.add_disturbance(d)
        stepped.add_disturbance(d)

    batched.advance(dt=dt, n=n)
    times = []
    for _ in range(n):
        stepped.advance(dt=dt)
        times.append(stepped.simulation_time)

    assert batched.room.exact_absolute_humidity_grams == stepped.room.exact_absolute_humidity_grams
    assert batched.room_state() == stepped.room_state()
    assert math.isclose(batched.simulation_time, n * dt, rel_tol=1e-9)
    assert times == sorted(times)

    # Invalid counts around the n >= 1 boundary leave time and humidity untouched.
    t, h = batched.simulation_time, batched.room.exact_absolute_humidity_grams
    for bad in (0, -1):
        assert batched.advance(dt=dt, n=bad).is_error
    assert (batched.simulation_time, batched.room.exact_absolute_humidity_grams) == (t, h)
