"""Tests for Simulator configuration and validation (Task 8).

Covers Property 11 (valid configuration round-trips), Property 12 (invalid
configuration is rejected and prior state is retained) and the exact error
indications of Requirements 3.4 and 2.7.
"""

from __future__ import annotations

import math
from unittest import mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.disturbances import Person_Disturbance
from room_humidity_simulator.simulator import Simulator
from tests.strategies import (
    configurations,
    configured_simulator,
    disturbances,
)


def _snapshot(sim: Simulator):
    """Everything Property 12 requires to be retained on error."""
    room, fan, coil = sim.room, sim.fan, sim.coil
    return (
        room,
        None if room is None else room.exact_absolute_humidity_grams,
        fan,
        None if fan is None else fan.flow_rate,
        coil,
        None if coil is None else (coil.surface_temperature, coil.condensation_capacity, coil.effectiveness),
        sim.time_step,
        sim.simulation_time,
        sim.active_disturbances,
    )


# Feature: room-humidity-simulator, Property 11: Valid configuration round-trips
@given(config=configurations())
def test_valid_configuration_round_trips(config):
    room_cfg, fan_cfg, coil_cfg, step_cfg = config
    sim = Simulator()
    result = sim.configure(room_cfg, fan_cfg, coil_cfg, step_cfg)

    assert result.is_ok
    assert sim.room.volume == room_cfg.volume_m3
    assert sim.room.temperature == room_cfg.temperature_c
    assert -50.0 <= sim.room.temperature <= 100.0
    assert sim.fan.flow_rate == fan_cfg.flow_rate_m3_per_s
    assert sim.coil.surface_temperature == coil_cfg.surface_temperature_c
    assert sim.coil.condensation_capacity == coil_cfg.condensation_capacity_g_per_s
    assert sim.coil.effectiveness == coil_cfg.effectiveness
    assert sim.time_step == step_cfg.duration_s
    assert sim.simulation_time == 0.0
    state = sim.room_state()
    assert 0.0 <= state.absolute_humidity_grams <= 999.9
    assert 0.0 <= state.relative_humidity_pct <= 100.0


_NON_NUMERIC = st.sampled_from([None, "1.0", math.nan, True, [1.0]])


def _bad(lo, hi, lo_inclusive=True):
    """Out-of-range floats around ``[lo, hi]`` (or ``(lo, hi]``)."""
    below = st.floats(max_value=lo, exclude_max=lo_inclusive, allow_nan=False)
    above = st.floats(min_value=hi, exclude_min=True, allow_nan=False)
    return st.one_of(below, above)


_INVALID_CASES = st.one_of(
    st.tuples(st.just("volume"), st.one_of(_bad(0.01, 100000.0), _NON_NUMERIC)),
    st.tuples(st.just("temperature"), st.one_of(_bad(-50.0, 100.0), _NON_NUMERIC)),
    st.tuples(st.just("humidity"), st.one_of(_bad(0.0, 1000.0), _NON_NUMERIC)),
    st.tuples(st.just("flow"), st.one_of(_bad(0.0, 10.0), _NON_NUMERIC)),
    st.tuples(
        st.just("capacity"),
        st.one_of(st.floats(max_value=0.0, exclude_max=True, allow_nan=False), _NON_NUMERIC),
    ),
    st.tuples(st.just("effectiveness"), st.one_of(_bad(0.0, 1.0), _NON_NUMERIC)),
    st.tuples(st.just("duration"), st.one_of(_bad(0.0, 3600.0, lo_inclusive=False), _NON_NUMERIC)),
    st.tuples(st.just("count"), st.one_of(st.integers(max_value=0), st.sampled_from([1.0, None, True]))),
    st.tuples(st.just("moisture_rate"), st.one_of(_bad(0.0, 1000.0), _NON_NUMERIC)),
    st.tuples(st.just("max_disturbances"), st.none()),
)

_EXPECTED_FIELD = {
    "volume": "volumeM3",
    "temperature": "temperatureC",
    "humidity": "initialHumidityGm3",
    "flow": "flowRateM3PerS",
    "capacity": "condensationCapacityGPerS",
    "effectiveness": "effectiveness",
    "duration": "durationS",
    "count": "advanceCount",
    "moisture_rate": "moistureRate",
    "max_disturbances": "disturbance",
}


# Feature: room-humidity-simulator, Property 12: Invalid configuration is rejected and prior state is retained
@given(
    config=configurations(),
    prior=st.lists(disturbances(), max_size=5),
    case=_INVALID_CASES,
    configured=st.booleans(),
)
def test_invalid_configuration_rejected_and_state_retained(config, prior, case, configured):
    room_cfg, fan_cfg, coil_cfg, step_cfg = config
    kind, bad = case
    sim = Simulator()
    if configured or kind in ("count", "max_disturbances"):
        sim = configured_simulator(config)
        sim.advance()
    for d in prior:
        assert sim.add_disturbance(d).is_ok
    if kind == "max_disturbances":
        while len(sim.active_disturbances) < 100:
            sim.add_disturbance(Person_Disturbance(1.0))
    before = _snapshot(sim)

    if kind == "volume":
        result = sim.configure(RoomConfig(bad, room_cfg.temperature_c, room_cfg.initial_humidity_gm3), fan_cfg, coil_cfg, step_cfg)
    elif kind == "temperature":
        result = sim.configure(RoomConfig(room_cfg.volume_m3, bad, room_cfg.initial_humidity_gm3), fan_cfg, coil_cfg, step_cfg)
    elif kind == "humidity":
        result = sim.configure(RoomConfig(room_cfg.volume_m3, room_cfg.temperature_c, bad), fan_cfg, coil_cfg, step_cfg)
    elif kind == "flow":
        result = sim.configure(room_cfg, FanConfig(bad), coil_cfg, step_cfg)
    elif kind == "capacity":
        result = sim.configure(room_cfg, fan_cfg, CoilConfig(coil_cfg.surface_temperature_c, bad), step_cfg)
    elif kind == "effectiveness":
        result = sim.configure(room_cfg, fan_cfg, CoilConfig(coil_cfg.surface_temperature_c, coil_cfg.condensation_capacity_g_per_s, bad), step_cfg)
    elif kind == "duration":
        result = sim.configure(room_cfg, fan_cfg, coil_cfg, TimeStepConfig(bad))
    elif kind == "count":
        result = sim.advance(n=bad)
    elif kind == "moisture_rate":
        result = sim.add_disturbance(Person_Disturbance(bad))
    else:
        result = sim.add_disturbance(Person_Disturbance(1.0))

    assert result.is_error
    assert result.error.field == _EXPECTED_FIELD[kind]
    assert result.error.message
    assert _snapshot(sim) == before


@pytest.mark.parametrize(
    "condition, expected",
    [
        ("volume_low", "room volume below minimum"),
        ("volume_high", "room volume exceeds maximum"),
        ("temperature", "room temperature out of range"),
        ("humidity_low", "initial humidity below minimum"),
        ("humidity_high", "initial humidity exceeds maximum"),
        ("flow_low", "flow rate below minimum"),
        ("flow_high", "flow rate exceeds maximum"),
        ("capacity", "condensation capacity below minimum"),
        ("duration", "time step duration out of range"),
    ],
)
def test_error_table_messages(condition, expected):
    room = RoomConfig(50.0, 20.0, 10.0)
    fan = FanConfig(1.0)
    coil = CoilConfig(5.0, 2.0)
    step = TimeStepConfig(60.0)
    overrides = {
        "volume_low": dict(room=RoomConfig(0.001, 20.0, 10.0)),
        "volume_high": dict(room=RoomConfig(100001.0, 20.0, 10.0)),
        "temperature": dict(room=RoomConfig(50.0, -51.0, 10.0)),
        "humidity_low": dict(room=RoomConfig(50.0, 20.0, -0.1)),
        "humidity_high": dict(room=RoomConfig(50.0, 20.0, 1000.1)),
        "flow_low": dict(fan=FanConfig(-0.1)),
        "flow_high": dict(fan=FanConfig(10.1)),
        "capacity": dict(coil=CoilConfig(5.0, -1.0)),
        "duration": dict(step=TimeStepConfig(0.0)),
    }[condition]
    args = dict(room=room, fan=fan, coil=coil, step=step)
    args.update(overrides)
    sim = Simulator()
    result = sim.configure(args["room"], args["fan"], args["coil"], args["step"])
    assert result.is_error
    assert result.error.message == expected
    assert sim.room is None  # no Room created


@pytest.mark.parametrize("bad", [None, "fast", math.nan, True])
def test_non_numeric_flow_rate_is_invalid(bad):
    """Requirement 3.4: non-numeric/undefined flow rate -> "flow rate invalid"."""
    sim = configured_simulator(
        (RoomConfig(50.0, 20.0, 10.0), FanConfig(1.5), CoilConfig(5.0, 2.0), TimeStepConfig(60.0))
    )
    prior_fan = sim.fan
    result = sim.configure(
        RoomConfig(50.0, 20.0, 10.0), FanConfig(bad), CoilConfig(5.0, 2.0), TimeStepConfig(60.0)
    )
    assert result.is_error
    assert result.error.message == "flow rate invalid"
    assert sim.fan is prior_fan and sim.fan.flow_rate == 1.5


def test_zero_saturation_capacity_reports_not_derivable():
    """Requirement 2.7: zero capacity -> RH 0.0 plus a not-derivable flag."""
    sim = configured_simulator(
        (RoomConfig(50.0, 20.0, 10.0), FanConfig(1.0), CoilConfig(5.0, 2.0), TimeStepConfig(60.0))
    )
    with mock.patch(
        "room_humidity_simulator.psychrometrics.saturation_content_grams",
        return_value=0.0,
    ):
        state = sim.room_state()
    assert state.relative_humidity_pct == 0.0
    assert state.relative_humidity_derivable is False


def test_advance_before_configure_is_rejected():
    result = Simulator().advance()
    assert result.is_error
    assert result.error.message == "simulator not configured"
