"""Property tests for Compressor-to-Coil coupling (Task 16).

Covers Property 17 (the compressor couples the coil surface temperature to
its state) and Property 18 (state changes take effect on the next step).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.coil import Coil
from room_humidity_simulator.compressor import CompressorState
from room_humidity_simulator.room import Room
from tests.strategies import (
    assert_running_coil,
    configurations,
    configured_simulator,
    cops,
    disturbance_sets,
    max_powers,
    setpoints,
)

_STATES = st.sampled_from([CompressorState.RUNNING, CompressorState.OFF])


# Feature: room-humidity-simulator, Property 17: Compressor couples the coil surface temperature to its state
@given(
    config=configurations(),
    cop=cops,
    setpoint=st.one_of(setpoints, st.floats(-50.0, 30.0)),
    state=_STATES,
    ds=disturbance_sets(10),
    max_power=max_powers,
)
def test_compressor_couples_coil_temperature(config, cop, setpoint, state, ds, max_power):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    sim.configure_compressor(cop, setpoint, max_power, defrost=None, mode="modulating")
    sim.set_compressor_state(state)
    sim.advance()

    step = sim.last_step
    room_temp = sim.room.temperature
    assert step.compressor_state is state
    if state is CompressorState.RUNNING:
        assert_running_coil(step, sim.compressor, room_temp)
        # Removal proceeds per Requirement 4 at the setpoint, and a power limit
        # can only reduce it.
        probe_room = Room(sim.room.volume, room_temp, step.humidity_after_additions_grams)
        probe_coil = Coil(setpoint, sim.coil.condensation_capacity, sim.coil.effectiveness)
        at_setpoint = probe_coil.removal(step.moved_air_volume_m3, probe_room, step.dt)
        if step.power_limited:
            assert 0.0 <= step.removed_grams <= at_setpoint * (1 + 1e-9) + 1e-12
        else:
            assert step.removed_grams == at_setpoint
    else:
        assert step.coil_surface_temperature_c == room_temp
        assert step.removed_grams == 0.0


# Feature: room-humidity-simulator, Property 18: Compressor state changes take effect on the next time step
@given(
    config=configurations(),
    cop=cops,
    setpoint=setpoints,
    toggles=st.lists(_STATES, min_size=1, max_size=10),
    max_power=max_powers,
)
def test_state_changes_take_effect_next_step(config, cop, setpoint, toggles, max_power):
    sim = configured_simulator(config)
    sim.configure_compressor(cop, setpoint, max_power, defrost=None, mode="modulating")
    governing = CompressorState.OFF
    for requested in toggles:
        sim.set_compressor_state(requested)
        # The current state is untouched by the request...
        assert sim.compressor.state is governing
        sim.advance()
        # ...and the new one governs coupling and energy from the next step.
        governing = requested
        step = sim.last_step
        assert step.compressor_state is governing
        if governing is CompressorState.OFF:
            assert step.coil_surface_temperature_c == sim.room.temperature
            assert step.electrical_joules == 0.0
        else:
            assert_running_coil(step, sim.compressor, sim.room.temperature)
