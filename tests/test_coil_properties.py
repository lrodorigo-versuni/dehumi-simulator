"""Property-based tests for the Coil removal model.

Implements the design's Property 7 (removal follows the recirculating coil
model and respects its bounds) and Property 24 (removal grows with room
humidity and with coil subcooling) for the :class:`Coil` component, driving
the real :class:`~room_humidity_simulator.coil.Coil` against a real
:class:`~room_humidity_simulator.room.Room` and the real psychrometrics.

The shared ``simulator`` Hypothesis profile (registered in ``conftest.py``)
runs a minimum of 100 examples per property test.
"""

from __future__ import annotations

import math

from hypothesis import assume, event, given
from hypothesis import strategies as st

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.coil import Coil
from room_humidity_simulator.room import Room


def _floats(lo, hi):
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


_TEMPERATURES = _floats(-40.0, 90.0)
_VOLUMES = st.one_of(st.just(0.01), _floats(0.01, 100000.0))
_FILL = _floats(0.0, 1.0)
_FLOW_RATES = st.one_of(st.just(0.0), _floats(0.0, 10.0))
_DURATIONS = st.one_of(st.just(3600.0), _floats(0.001, 3600.0))
_CAPACITIES = st.one_of(st.just(0.0), st.just(math.inf), _floats(0.0, 50.0))
_EFFECTIVENESS = st.one_of(st.sampled_from([0.0, 1.0]), _floats(0.0, 1.0))
_SUBCOOLING = _floats(0.0, 60.0)


def _room(volume, temperature, fill):
    saturation = psychrometrics.saturation_content_grams(temperature, volume)
    return Room(volume, temperature, fill * saturation)


def _euler_removal(volume, q, q_out, fe, cap, dt, steps):
    """Reference: explicit integration of ``V dq/dt = -min(cap, Fe (q - q_out))``."""
    h = dt / steps
    removed = 0.0
    for _ in range(steps):
        rate = min(cap, fe * max(q - q_out, 0.0))
        removed += rate * h
        q -= rate * h / volume
    return removed


# Feature: room-humidity-simulator, Property 7: Coil removal follows the recirculating coil model and is bounded by capacity and by the moisture above the coil outlet humidity
@given(
    temperature=_TEMPERATURES,
    volume=_VOLUMES,
    fill=_FILL,
    flow=_FLOW_RATES,
    dt=_DURATIONS,
    capacity=_CAPACITIES,
    effectiveness=_EFFECTIVENESS,
    subcooling=_SUBCOOLING,
)
def test_property_7_coil_removal_model(
    temperature, volume, fill, flow, dt, capacity, effectiveness, subcooling
):
    room = _room(volume, temperature, fill)
    coil = Coil(temperature - subcooling, capacity, effectiveness)
    removed = coil.removal(flow * dt, room, dt)

    q = room.specific_humidity()
    q_out = coil.outlet_humidity(temperature)
    removable = max(room.volume * (q - q_out), 0.0)

    # Bounds (Requirements 4.1, 4.2): never negative, never above the
    # capacity over the step, never below the coil outlet humidity.
    assert removed >= 0.0
    assert removed <= capacity * dt * (1 + 1e-12)
    assert removed <= removable * (1 + 1e-12) + 1e-12
    if flow == 0.0 or effectiveness == 0.0 or capacity == 0.0 or q <= q_out:
        assert removed == 0.0
        return

    fe = flow * effectiveness
    k_dt = fe / volume * dt
    if k_dt <= 20.0 and capacity * dt < 1e12:
        # Compare with a fine numerical integration of the same ODE.
        event("compared with numerical integration")
        reference = _euler_removal(volume, q, q_out, fe, capacity, dt, 4000)
        assert math.isclose(removed, reference, rel_tol=1e-2, abs_tol=1e-9 * removable + 1e-12)
    # While the coil cannot exhaust the excess within the step, a
    # capacity-limited coil removes exactly capacity * dt.
    if fe * (q - q_out) >= capacity and capacity * dt <= removable - capacity / fe * room.volume:
        event("capacity-limited")
        assert math.isclose(removed, capacity * dt, rel_tol=1e-9)


# Feature: room-humidity-simulator, Property 7: Coil removal follows the recirculating coil model and is bounded by capacity and by the moisture above the coil outlet humidity
@given(
    temperature=_floats(10.0, 40.0),
    volume=_floats(1.0, 1000.0),
    fill=_floats(0.3, 1.0),
    subcooling=_floats(5.0, 40.0),
    flow=_floats(0.01, 10.0),
    dt=_floats(1.0, 3600.0),
    capacity=st.one_of(st.just(math.inf), _floats(0.001, 50.0)),
    effectiveness=_floats(0.05, 1.0),
)
def test_property_7_matches_numerical_integration_when_removing(
    temperature, volume, fill, subcooling, flow, dt, capacity, effectiveness
):
    """Same property, with generators focused on the actively removing regime."""
    room = _room(volume, temperature, fill)
    coil = Coil(temperature - subcooling, capacity, effectiveness)
    q, q_out = room.specific_humidity(), coil.outlet_humidity(temperature)
    fe = flow * effectiveness
    assume(q > q_out and fe / volume * dt <= 20.0)

    removed = coil.removal(flow * dt, room, dt)
    reference = _euler_removal(volume, q, q_out, fe, capacity, dt, 4000)
    assert removed > 0.0
    assert math.isclose(removed, reference, rel_tol=1e-2)


# Feature: room-humidity-simulator, Property 24: Coil removal grows with room humidity and with coil subcooling
@given(
    temperature=_TEMPERATURES,
    volume=_VOLUMES,
    fills=st.tuples(_FILL, _FILL),
    flow=_floats(0.01, 10.0),
    dt=_DURATIONS,
    capacity=_CAPACITIES,
    effectiveness=_floats(0.05, 1.0),
    subcoolings=st.tuples(_SUBCOOLING, _SUBCOOLING),
)
def test_property_24_removal_monotonic_in_humidity_and_coil_temperature(
    temperature, volume, fills, flow, dt, capacity, effectiveness, subcoolings
):
    lo_fill, hi_fill = sorted(fills)
    warm, cold = sorted(subcoolings)  # smaller subcooling = warmer coil
    coil_warm = Coil(temperature - warm, capacity, effectiveness)
    coil_cold = Coil(temperature - cold, capacity, effectiveness)
    dry, humid = _room(volume, temperature, lo_fill), _room(volume, temperature, hi_fill)
    tol = 1e-9

    # More humid room -> at least as much removed, same coil.
    assert coil_warm.removal(flow * dt, humid, dt) >= coil_warm.removal(flow * dt, dry, dt) * (1 - tol) - tol
    # Colder coil -> at least as much removed, same room.
    assert coil_cold.removal(flow * dt, humid, dt) >= coil_warm.removal(flow * dt, humid, dt) * (1 - tol) - tol


def test_removal_tapers_toward_coil_outlet_humidity():
    """Example: removal shrinks as the room dries toward the coil outlet state."""
    coil = Coil(5.0, math.inf, 0.85)
    q_out = coil.outlet_humidity(25.0)  # ~6.3 g/m^3
    removals = []
    for q in (20.0, 15.0, 10.0, 7.0, q_out + 0.1):
        removals.append(coil.removal(18.0, Room(20.0, 25.0, q * 20.0), 60.0))
    assert removals == sorted(removals, reverse=True)
    assert removals[-1] < 0.1 * removals[0]


def test_long_step_never_dries_below_outlet_humidity():
    """A huge flow over an hour removes at most the excess above q_out."""
    room = Room(20.0, 25.0, 20.0 * 20.0)
    coil = Coil(5.0, math.inf, 1.0)
    removed = coil.removal(10.0 * 3600.0, room, 3600.0)
    remaining_q = (room.exact_absolute_humidity_grams - removed) / room.volume
    assert remaining_q >= coil.outlet_humidity(25.0) - 1e-9
    assert math.isclose(remaining_q, coil.outlet_humidity(25.0), rel_tol=1e-6)


def test_capacity_is_a_rate():
    """Capacity is in g/s: the per-step limit scales with dt."""
    room = Room(50.0, 25.0, 20.0 * 50.0)
    coil = Coil(5.0, 0.01, 0.85)
    assert math.isclose(coil.removal(0.5 * 60.0, room, 60.0), 0.6)
    assert math.isclose(coil.removal(0.5 * 600.0, room, 600.0), 6.0)
