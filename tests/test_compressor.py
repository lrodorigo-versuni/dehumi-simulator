"""Tests for the Compressor component and its configuration (Task 15).

Covers Property 15 (configuration round-trips and starts off), Property 16
(invalid configuration is rejected and prior state is retained) and the exact
error messages and state enum of Requirements 8.4, 8.6 and 8.9.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.compressor import Compressor, CompressorState
from room_humidity_simulator.disturbances import Person_Disturbance
from room_humidity_simulator.simulator import Simulator
from tests.strategies import configurations, configured_simulator, cops, setpoints


# Feature: room-humidity-simulator, Property 15: Compressor configuration round-trips and starts off
@given(cop=cops, setpoint=setpoints)
def test_compressor_config_round_trips_and_starts_off(cop, setpoint):
    sim = Simulator()
    result = sim.configure_compressor(cop, setpoint, mode="modulating")

    assert result.is_ok
    compressor = sim.compressor
    assert compressor is result.value
    assert compressor.cop == cop
    assert 0.0 < compressor.cop <= 20.0
    assert compressor.setpoint_temperature == setpoint
    assert compressor.state is CompressorState.OFF
    assert compressor.max_power_w == 400.0
    assert compressor.max_cooling_w == 400.0 * cop


_NON_NUMERIC = st.sampled_from([None, "3.0", math.nan, True])
_BAD_COPS = st.one_of(
    st.floats(max_value=0.0, allow_nan=False),
    st.floats(min_value=20.0, exclude_min=True, allow_nan=False),
    _NON_NUMERIC,
)
_BAD_POWERS = st.one_of(st.floats(max_value=0.0, allow_nan=False), _NON_NUMERIC)
_BAD_SETPOINTS = st.one_of(
    st.floats(max_value=-50.0, exclude_max=True, allow_nan=False),
    st.floats(min_value=100.0, exclude_min=True, allow_nan=False),
    _NON_NUMERIC,
)


# Feature: room-humidity-simulator, Property 16: Invalid compressor configuration is rejected and prior state is retained
@given(
    config=configurations(),
    prior=st.one_of(st.none(), st.tuples(cops, setpoints, st.booleans())),
    bad=st.one_of(
        st.tuples(st.just("cop"), _BAD_COPS, setpoints, st.just(400.0)),
        st.tuples(st.just("setpointTemperatureC"), cops, _BAD_SETPOINTS, st.just(400.0)),
        st.tuples(st.just("maxPowerW"), cops, setpoints, _BAD_POWERS),
    ),
    steps=st.integers(0, 3),
)
def test_invalid_compressor_config_rejected_and_state_retained(config, prior, bad, steps):
    sim = configured_simulator(config)
    sim.add_disturbance(Person_Disturbance(0.5))
    if prior is not None:
        cop, setpoint, running = prior
        sim.configure_compressor(cop, setpoint, mode="modulating")
        sim.set_compressor_state("running" if running else "off")
    if steps:
        sim.advance(n=steps)
    existing = sim.compressor
    snapshot = (
        existing,
        None if existing is None else (existing.cop, existing.setpoint_temperature, existing.max_power_w, existing.state, existing.pending_state),
        sim.room,
        sim.room.exact_absolute_humidity_grams,
        sim.fan,
        sim.coil,
        sim.active_disturbances,
        sim.time_step,
        sim.simulation_time,
        sim.energy_consumed_joules,
        sim.power_draw_watts,
    )

    field, cop, setpoint, max_power = bad
    result = sim.configure_compressor(cop, setpoint, max_power, mode="modulating")

    assert result.is_error
    assert result.error.field == field
    assert result.error.message
    current = sim.compressor
    assert (
        current,
        None if current is None else (current.cop, current.setpoint_temperature, current.max_power_w, current.state, current.pending_state),
        sim.room,
        sim.room.exact_absolute_humidity_grams,
        sim.fan,
        sim.coil,
        sim.active_disturbances,
        sim.time_step,
        sim.simulation_time,
        sim.energy_consumed_joules,
        sim.power_draw_watts,
    ) == snapshot


@pytest.mark.parametrize(
    "cop, setpoint, expected, max_power",
    [
        (0.0, 5.0, "COP must be greater than 0.0", 400.0),
        (-1.0, 5.0, "COP must be greater than 0.0", 400.0),
        (20.01, 5.0, "COP exceeds maximum", 400.0),
        (None, 5.0, "COP invalid", 400.0),
        ("high", 5.0, "COP invalid", 400.0),
        (math.nan, 5.0, "COP invalid", 400.0),
        (3.0, -50.1, "setpoint temperature out of range", 400.0),
        (3.0, 100.1, "setpoint temperature out of range", 400.0),
        (3.0, None, "setpoint temperature invalid", 400.0),
        (3.0, "cold", "setpoint temperature invalid", 400.0),
        (3.0, math.nan, "setpoint temperature invalid", 400.0),
        (3.0, 5.0, "max power must be greater than 0.0", 0.0),
        (3.0, 5.0, "max power must be greater than 0.0", -400.0),
        (3.0, 5.0, "max power invalid", None),
        (3.0, 5.0, "max power invalid", "400 W"),
    ],
)
def test_exact_compressor_error_messages(cop, setpoint, expected, max_power):
    """Requirements 8.2-8.6 and 8.10, in particular the exact 8.4 and 8.6 messages."""
    result = Compressor.create(cop, setpoint, max_power)
    assert result.is_error
    assert result.error.message == expected


def test_state_is_exactly_running_or_off():
    """Requirement 8.9."""
    compressor = Compressor.create(3.0, 5.0).value
    assert compressor.state in (CompressorState.RUNNING, CompressorState.OFF)
    assert {s.value for s in CompressorState} == {"running", "off"}
    compressor.set_state(CompressorState.RUNNING)
    compressor.promote_pending_state()
    assert compressor.state == "running"


def test_set_state_errors():
    sim = Simulator()
    assert sim.set_compressor_state("running").error.message == "compressor not configured"
    sim.configure_compressor(3.0, 5.0, mode="modulating")
    assert sim.set_compressor_state("idle").error.message == "compressor state invalid"
    assert sim.compressor.pending_state is CompressorState.OFF
