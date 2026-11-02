"""Shared Hypothesis strategies and helpers for the Simulator test suites."""

from __future__ import annotations

import math

from hypothesis import strategies as st

from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.disturbances import (
    Boiling_Water_Disturbance,
    Disturbance,
    Person_Disturbance,
)
from room_humidity_simulator.simulator import Simulator


def _floats(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


volumes = st.one_of(st.sampled_from([0.01, 100000.0]), _floats(0.01, 100000.0))
temperatures = st.one_of(st.sampled_from([-50.0, 100.0]), _floats(-50.0, 100.0))
initial_humidities = st.one_of(st.sampled_from([0.0, 1000.0]), _floats(0.0, 1000.0))
flow_rates = st.one_of(st.sampled_from([0.0, 10.0]), _floats(0.0, 10.0))
capacities = st.one_of(st.just(0.0), _floats(0.0, 1000.0))
effectivenesses = st.one_of(st.sampled_from([0.0, 1.0]), _floats(0.0, 1.0))
coil_temperatures = _floats(-50.0, 100.0)
durations = st.one_of(st.just(3600.0), _floats(0.001, 3600.0))
moisture_rates = st.one_of(st.sampled_from([0.0, 1000.0]), _floats(0.0, 1000.0))
cops = st.one_of(st.just(20.0), _floats(0.01, 20.0))
setpoints = st.one_of(st.sampled_from([-50.0, 100.0]), _floats(-50.0, 100.0))
max_powers = st.one_of(st.sampled_from([400.0, math.inf]), _floats(1.0, 5000.0))

room_configs = st.builds(RoomConfig, volumes, temperatures, initial_humidities)
fan_configs = st.builds(FanConfig, flow_rates)
coil_configs = st.builds(CoilConfig, coil_temperatures, capacities, effectivenesses)
time_step_configs = st.builds(TimeStepConfig, durations)


@st.composite
def configurations(draw):
    """A valid (room, fan, coil, time_step) configuration tuple."""
    return (
        draw(room_configs),
        draw(fan_configs),
        draw(coil_configs),
        draw(time_step_configs),
    )


@st.composite
def disturbances(draw) -> Disturbance:
    """A built-in disturbance with an in-range moisture rate."""
    kind = draw(st.sampled_from([Person_Disturbance, Boiling_Water_Disturbance]))
    return kind(draw(moisture_rates))


def disturbance_sets(max_size: int = 100):
    return st.lists(disturbances(), min_size=0, max_size=max_size)


def configured_simulator(config) -> Simulator:
    """Build a Simulator configured with ``config``, asserting success."""
    sim = Simulator()
    result = sim.configure(*config)
    assert result.is_ok, result.error
    return sim


def assert_running_coil(step, compressor, room_temperature_c) -> None:
    """Coil temperature and power while running with a power-limited compressor.

    Not power-limited: the coil is at the setpoint. Power-limited: the coil
    is warmer than the setpoint (but not above the room) and the compressor
    absorbs no more than its maximum power.
    """
    setpoint = compressor.setpoint_temperature
    if step.power_limited:
        # Limited at some point in the step; by the end the coil may be back at the setpoint.
        assert setpoint <= step.coil_surface_temperature_c <= room_temperature_c
    else:
        assert step.coil_surface_temperature_c == setpoint
    assert step.electrical_joules <= compressor.max_power_w * step.dt * (1 + 1e-9)


class FixedDisturbance(Disturbance):
    """Custom disturbance returning a fixed (possibly invalid) value."""

    def __init__(self, value: object) -> None:
        self.value = value

    def is_active(self) -> bool:
        return True

    def contribution(self, dt: float) -> object:
        return self.value
