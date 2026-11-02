"""Tests for optional-compressor injection and backward compatibility (Task 18).

Covers Property 22 (adding a compressor preserves state and defers its
effect) and Property 23 (without a compressor, behavior matches Phase 1).
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.coil import removal_over_step
from room_humidity_simulator.compressor import CompressorState
from tests.strategies import (
    configurations,
    configured_simulator,
    cops,
    disturbance_sets,
    assert_running_coil,
    durations,
    max_powers,
    setpoints,
)


def phase1_reference(config, ds, advances):
    """Independent Phase 1 engine straight from the design's mass balance.

    Yields ``(H, RH)`` after each step, using only the Phase 1 formulas, the
    stateless physics module and the coil's step-integration function (whose
    physics is checked separately against numerical integration); no Room,
    Coil or Simulator orchestration code.
    """
    room_cfg, fan_cfg, coil_cfg, step_cfg = config
    volume, temp = room_cfg.volume_m3, room_cfg.temperature_c
    cap = psychrometrics.saturation_content_grams(temp, volume)
    h = min(max(room_cfg.initial_humidity_gm3 * volume, 0.0), cap)
    for dt, n in advances:
        dt = step_cfg.duration_s if dt is None else dt
        for _ in range(n):
            added = math.fsum(d.contribution(dt) for d in ds)
            h = min(h + added, cap)
            removed = 0.0
            q = h / volume
            t_coil = coil_cfg.surface_temperature_c
            if t_coil < temp and q > 0.0 and psychrometrics.dew_point(q, temp) > t_coil:
                removed = removal_over_step(
                    volume,
                    q,
                    psychrometrics.coil_outlet_humidity_density(t_coil, temp),
                    fan_cfg.flow_rate_m3_per_s * coil_cfg.effectiveness,
                    coil_cfg.condensation_capacity_g_per_s,
                    dt,
                )
            h = max(h - removed, 0.0)
            yield h, psychrometrics.relative_humidity_percent(h, temp, volume)


# Feature: room-humidity-simulator, Property 23: Without a compressor, behavior and outputs match Phase 1
@given(
    config=configurations(),
    ds=disturbance_sets(100),
    advances=st.lists(
        st.tuples(st.one_of(st.none(), durations), st.integers(1, 5)), min_size=1, max_size=4
    ),
)
def test_no_compressor_matches_phase1(config, ds, advances):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    reference = phase1_reference(config, ds, advances)

    for dt, n in advances:
        for _ in range(n):
            sim.advance(dt=dt)
            ref_h, ref_rh = next(reference)
            state = sim.room_state()
            assert abs(sim.room.exact_absolute_humidity_grams - ref_h) <= 0.1
            assert abs(state.relative_humidity_pct - ref_rh) <= 0.1
            assert sim.coil.surface_temperature == config[2].surface_temperature_c
            assert sim.energy_consumed_joules == 0.0
            assert sim.power_draw_watts == 0.0


# Feature: room-humidity-simulator, Property 22: Adding a compressor preserves state and defers its effect
@given(
    config=configurations(),
    ds=disturbance_sets(10),
    steps_before=st.integers(0, 10),
    cop=cops,
    setpoint=setpoints,
    max_power=max_powers,
)
def test_adding_compressor_preserves_state_and_defers_effect(
    config, ds, steps_before, cop, setpoint, max_power
):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    if steps_before:
        sim.advance(n=steps_before)
    before = (
        sim.room.exact_absolute_humidity_grams,
        sim.room.temperature,
        sim.simulation_time,
        sim.energy_consumed_joules,
        sim.coil.surface_temperature,
    )

    assert sim.configure_compressor(cop, setpoint, max_power, defrost=None, mode="modulating").is_ok
    sim.set_compressor_state("running")
    # Nothing changes at the moment of addition.
    assert (
        sim.room.exact_absolute_humidity_grams,
        sim.room.temperature,
        sim.simulation_time,
        sim.energy_consumed_joules,
        sim.coil.surface_temperature,
    ) == before

    sim.advance()
    step = sim.last_step
    assert step.compressor_state is CompressorState.RUNNING
    assert_running_coil(step, sim.compressor, sim.room.temperature)
    assert sim.energy_consumed_joules == before[3] + step.electrical_joules
