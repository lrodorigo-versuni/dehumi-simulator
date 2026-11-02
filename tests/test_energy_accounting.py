"""Tests for energy accounting in the step engine (Task 17).

Covers Property 20 (electrical energy per step equals heat divided by COP),
Property 21 (cumulative energy is monotonic and power is energy per unit
time) and the initialization / known-trajectory example (Requirement 11.6).
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.compressor import CompressorState
from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.psychrometrics import latent_heat_joules, sensible_heat_joules
from room_humidity_simulator.simulator import Simulator
from tests.strategies import (
    configurations,
    configured_simulator,
    cops,
    disturbance_sets,
    max_powers,
    setpoints,
)

_STATES = st.sampled_from([CompressorState.RUNNING, CompressorState.OFF])


# Feature: room-humidity-simulator, Property 20: Electrical energy per step equals heat divided by COP
@given(
    config=configurations(),
    cop=cops,
    setpoint=st.one_of(setpoints, st.floats(-50.0, 30.0)),
    state=_STATES,
    ds=disturbance_sets(10),
    max_power=max_powers,
)
def test_electrical_energy_is_heat_over_cop(config, cop, setpoint, state, ds, max_power):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    sim.configure_compressor(cop, setpoint, max_power, defrost=None, mode="modulating")
    sim.set_compressor_state(state)
    sim.advance()

    step = sim.last_step
    assert step.electrical_joules >= 0.0
    if state is CompressorState.OFF:
        assert step.electrical_joules == 0.0
        return
    assert step.electrical_joules == step.heat_extracted_joules / cop
    # The heat extracted never exceeds the cooling the compressor can deliver.
    assert step.heat_extracted_joules <= cop * max_power * step.dt * (1 + 1e-9)
    if not step.power_limited:
        # Coil held at the setpoint: heat is latent plus sensible there.
        sensible = sensible_heat_joules(
            step.moved_air_volume_m3, sim.room.temperature, setpoint, sim.coil.effectiveness
        )
        heat = max(latent_heat_joules(step.removed_grams, setpoint) + sensible, 0.0)
        assert math.isclose(step.heat_extracted_joules, heat, rel_tol=1e-9, abs_tol=1e-9)
        if step.removed_grams == 0.0 and sensible == 0.0:
            assert step.electrical_joules == 0.0


# Feature: room-humidity-simulator, Property 21: Cumulative energy is monotonic and power is energy per unit time
@given(
    config=configurations(),
    cop=cops,
    setpoint=setpoints,
    toggles=st.lists(_STATES, min_size=1, max_size=10),
    ds=disturbance_sets(5),
    max_power=max_powers,
)
def test_cumulative_energy_and_power(config, cop, setpoint, toggles, ds, max_power):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    sim.configure_compressor(cop, setpoint, max_power, defrost=None, mode="modulating")
    for requested in toggles:
        sim.set_compressor_state(requested)
        before = sim.energy_consumed_joules
        sim.advance()
        step = sim.last_step

        assert sim.energy_consumed_joules == before + step.electrical_joules
        assert sim.energy_consumed_joules >= before >= 0.0
        assert sim.power_draw_watts == step.electrical_joules / step.dt
        assert 0.0 <= sim.power_draw_watts <= max_power * (1 + 1e-9)
        if step.compressor_state is CompressorState.OFF or step.electrical_joules == 0.0:
            assert sim.power_draw_watts == 0.0
        if step.compressor_state is CompressorState.OFF:
            assert sim.energy_consumed_joules == before


def test_energy_and_power_zero_before_first_step():
    """Requirement 11.6."""
    sim = Simulator()
    assert sim.energy_consumed_joules == 0.0
    assert sim.power_draw_watts == 0.0
    sim.configure(RoomConfig(40.0, 25.0, 15.0), FanConfig(0.5), CoilConfig(25.0, 0.02), TimeStepConfig(60.0))
    sim.configure_compressor(4.0, 5.0, mode="modulating")
    sim.set_compressor_state("running")
    assert sim.energy_consumed_joules == 0.0
    assert sim.power_draw_watts == 0.0


def test_running_then_off_known_trajectory():
    """Hand-computed energy and power for a running-then-off scenario.

    Room 40 m^3 at 25 deg C with 15 g/m^3 (dew point ~17.5 deg C), fan
    0.5 m^3/s, coil effectiveness 0.85, dt 60 s -> 30 m^3 moved per step.
    Running at 5 deg C the room is far above the coil outlet humidity
    (~6.3 g/m^3), so the coil is capacity-limited at 0.02 g/s:

    - removed  = 0.02 g/s * 60 s                         = 1.2 g
    - latent   = 1.2 g * 2260 J/g                        = 2712 J
    - sensible = 30 m^3 * 1.2 * 1005 * 0.85 * (25 - 5)   = 615060 J
    - electrical = (2712 + 615060) / COP 4               = 154443 J -> 2574.05 W

    The compressor is given unlimited power so the coil holds the setpoint.
    """
    sim = Simulator()
    sim.configure(RoomConfig(40.0, 25.0, 15.0), FanConfig(0.5), CoilConfig(25.0, 0.02), TimeStepConfig(60.0))
    sim.configure_compressor(4.0, 5.0, max_power_w=math.inf, mode="modulating")
    sim.set_compressor_state("running")

    sim.advance(n=3)
    assert sim.last_step.removed_grams == pytest.approx(1.2)
    assert sim.energy_consumed_joules == pytest.approx(3 * 154443.0)
    assert sim.power_draw_watts == pytest.approx(154443.0 / 60.0)

    sim.set_compressor_state("off")
    sim.advance(n=2)
    assert sim.last_step.removed_grams == 0.0
    assert sim.energy_consumed_joules == pytest.approx(3 * 154443.0)
    assert sim.power_draw_watts == 0.0
    assert math.isclose(sim.room.exact_absolute_humidity_grams, 600.0 - 3 * 1.2)


def test_power_limited_dry_room_known_values():
    """Hand-computed power-limited case with sensible heat only.

    A dry room (no condensation) at 25 deg C, fan 0.5 m^3/s, effectiveness
    0.85: the sensible load is 0.5 * 1.2 * 1005 * 0.85 = 512.55 W per K of
    coil subcooling. At COP 3 and 400 W the compressor delivers 1200 W, so the
    coil can only be held 1200 / 512.55 = 2.341 K below the room, far from the
    5 deg C setpoint, with the compressor at full power.
    """
    sim = Simulator()
    sim.configure(RoomConfig(40.0, 25.0, 0.0), FanConfig(0.5), CoilConfig(25.0, 1.0), TimeStepConfig(60.0))
    sim.configure_compressor(3.0, 5.0, max_power_w=400.0, mode="modulating")
    sim.set_compressor_state("running")
    sim.advance(n=2)

    step = sim.last_step
    assert step.power_limited
    assert step.coil_surface_temperature_c == pytest.approx(25.0 - 1200.0 / 512.55, abs=1e-3)
    assert sim.power_draw_watts == pytest.approx(400.0, rel=1e-4)
    assert sim.energy_consumed_joules == pytest.approx(2 * 400.0 * 60.0, rel=1e-4)
    assert step.removed_grams == 0.0


def test_power_limit_coil_balances_heat_load():
    """When power-limited, the heat load at the reached coil temperature equals COP * P_max."""
    from room_humidity_simulator.compressor import Compressor

    sim = Simulator()
    sim.configure(RoomConfig(20.0, 25.0, 20.0), FanConfig(0.05), CoilConfig(25.0, 0.5), TimeStepConfig(1.0))
    sim.configure_compressor(3.0, 5.0, max_power_w=400.0, mode="modulating")
    sim.set_compressor_state("running")
    sim.advance()
    step = sim.last_step
    assert step.power_limited
    assert 5.0 < step.coil_surface_temperature_c < 25.0
    load = Compressor.heat_load_w(
        sim.coil, step.coil_surface_temperature_c, sim.room.specific_humidity(), 25.0, 0.05
    )
    assert load == pytest.approx(1200.0, rel=5e-3)
    assert step.removed_grams > 0.0


# Feature: room-humidity-simulator, Property 25: More compressor power never removes less moisture
@given(
    config=configurations(),
    cop=cops,
    setpoint=setpoints,
    powers=st.tuples(max_powers, max_powers),
    ds=disturbance_sets(5),
)
def test_more_power_never_removes_less(config, cop, setpoint, powers, ds):
    low, high = sorted(powers)
    removed = []
    for power in (low, high):
        sim = configured_simulator(config)
        for d in ds:
            sim.add_disturbance(d)
        sim.configure_compressor(cop, setpoint, power, defrost=None, mode="modulating")
        sim.set_compressor_state("running")
        sim.advance()
        removed.append(sim.last_step.removed_grams)
        assert sim.power_draw_watts <= power * (1 + 1e-9)
    assert removed[1] >= removed[0] * (1 - 1e-6) - 1e-9
