"""Tests for frost build-up on a sub-zero coil and the defrost cycle."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from room_humidity_simulator import (
    CoilConfig,
    CopTable,
    DefrostSettings,
    FanConfig,
    Person_Disturbance,
    RoomConfig,
    Simulator,
    TimeStepConfig,
    psychrometrics,
)
from room_humidity_simulator.control import ClosedLoop, Commands

LUT = CopTable([-10, -5, 0, 5, 10, 15, 20, 25], [1.8, 2.1, 2.5, 2.9, 3.4, 4.0, 4.7, 5.5])


def cold_room(setpoint=-8.0, room_t=12.0, rh=0.75, power=400.0, defrost=DefrostSettings(), flow=0.05, dt=30.0):
    sim = Simulator()
    sat = psychrometrics.saturation_content_density(room_t)
    sim.configure(RoomConfig(20.0, room_t, rh * sat), FanConfig(flow), CoilConfig(room_t, 0.5), TimeStepConfig(dt))
    sim.add_disturbance(Person_Disturbance(0.03))
    sim.configure_compressor(LUT, setpoint, power, defrost=defrost, mode="modulating")
    sim.set_compressor_state("running")
    return sim


def run(sim, n):
    steps = []
    for _ in range(n):
        sim.advance()
        steps.append(sim.last_step)
    return steps


def test_fusion_heat_is_added_below_zero():
    assert psychrometrics.latent_heat_joules(2.0, 5.0) == 2.0 * 2260.0
    assert psychrometrics.latent_heat_joules(2.0, -0.1) == 2.0 * (2260.0 + 334.0)
    assert psychrometrics.latent_heat_joules(2.0) == 2.0 * 2260.0


@settings(max_examples=40)
@given(setpoint=st.floats(0.0, 20.0), room_t=st.floats(5.0, 35.0), rh=st.floats(0.3, 1.0))
def test_coil_at_or_above_zero_never_frosts(setpoint, room_t, rh):
    steps = run(cold_room(setpoint=setpoint, room_t=room_t, rh=rh), 40)
    assert all(s.frost_g == 0.0 and not s.defrosting for s in steps)


def test_frost_builds_below_zero_and_lowers_effectiveness():
    sim = cold_room(defrost=DefrostSettings(start_delay_s=1e9))  # never defrost
    steps = run(sim, 20)
    frozen = sum(s.removed_grams for s in steps if s.coil_surface_temperature_c < 0.0)
    assert sim.coil.frost_g == pytest.approx(frozen)
    assert sim.coil.frost_g > 0.0
    assert sim.coil.active_effectiveness == pytest.approx(0.85 / (1.0 + sim.coil.frost_g / 300.0))


def test_defrost_cycle_stops_compressor_and_clears_the_coil():
    sim = cold_room()
    steps = run(sim, 120)
    assert sim.compressor.defrost_count >= 2
    for s in steps:
        if s.defrosting:
            assert not s.compressor_running and s.electrical_joules == 0.0 and s.removed_grams == 0.0
    # Every defrost ends with a clear coil, above the end temperature.
    ends = [i for i in range(1, len(steps)) if steps[i - 1].defrosting and not steps[i].defrosting]
    assert ends
    for i in ends:
        assert steps[i - 1].frost_g == 0.0
        assert steps[i - 1].coil_surface_temperature_c >= 3.0
    # It starts only after the coil stayed at or below -1 C for the delay (300 s = 10 steps).
    first = next(i for i, s in enumerate(steps) if s.defrosting)
    cold_run = [s.coil_surface_temperature_c <= -1.0 for s in steps[:first]]
    assert sum(cold_run[-10:]) == 10


def test_defrost_respects_min_and_max_duration_in_a_near_freezing_room():
    # Room at 2 C: the coil can never reach the +3 C end temperature -> max duration ends it.
    settings_ = DefrostSettings(start_delay_s=60.0, min_duration_s=120.0, max_duration_s=300.0)
    steps = run(cold_room(room_t=2.0, rh=0.9, setpoint=-10.0, defrost=settings_), 80)
    runs, current = [], 0
    for s in steps:
        if s.defrosting:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    assert runs and all(r * 30.0 == 300.0 for r in runs)


def test_melt_water_balance_and_reevaporation():
    sim = cold_room(defrost=DefrostSettings(reevaporation_fraction=0.3))
    steps = run(sim, 120)
    frozen = sum(s.removed_grams for s in steps if s.compressor_running and s.coil_surface_temperature_c < 0.0)
    melted = sum(s.melted_g for s in steps)
    assert frozen == pytest.approx(melted + sim.coil.frost_g)
    assert sum(s.reevaporated_g for s in steps) == pytest.approx(0.3 * melted)


def test_without_frost_model_the_coil_goes_below_zero_as_in_phase_2():
    steps = run(cold_room(defrost=None), 60)
    assert min(s.coil_surface_temperature_c for s in steps) < -1.0
    assert all(s.frost_g == 0.0 and not s.defrosting for s in steps)


def test_closed_loop_sees_defrost_and_keeps_the_fan_running():
    class WantsOnFanOff:
        def __init__(self):
            self.seen = []

        def step(self, m, dt):
            self.seen.append(m.defrosting)
            # Fan off whenever the unit defrosts: the board must keep it on.
            return Commands(compressor_on=True, fan_speed=0 if m.defrosting else 2)

    sim = cold_room(flow=0.0)
    ctrl = WantsOnFanOff()
    loop = ClosedLoop(sim, ctrl)
    result = loop.run(120)
    s = result.series
    assert any(ctrl.seen)
    for i in range(1, len(s["t_s"])):
        if s["defrosting"][i]:
            assert not s["compressor_on"][i]
            assert s["fan_speed"][i] >= 1
    k = result.kpis
    assert k["defrost_cycles"] >= 1 and k["defrost_time_pct"] > 0.0 and k["max_frost_g"] > 0.0
    assert k["compressor_starts"] >= k["defrost_cycles"]  # restarts after defrost count


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(frost_halving_g=0.0), "frost halving must be greater than 0"),
        (dict(start_below_c=2.0), "defrost start must be between -50 and 0 °C"),
        (dict(end_above_c=-1.0), "defrost end must be between 0 and 30 °C"),
        (dict(min_duration_s=600.0, max_duration_s=300.0), "defrost maximum must be >= minimum"),
        (dict(reevaporation_fraction=1.5), "re-evaporation must be between 0 and 1"),
        (dict(start_delay_s=math.nan), "defrost delay must be >= 0"),
    ],
)
def test_invalid_defrost_settings_are_rejected(kwargs, message):
    sim = Simulator()
    result = sim.configure_compressor(3.0, 5.0, 400.0, defrost=DefrostSettings(**kwargs), mode="modulating")
    assert result.is_error and result.error.message == message
    assert sim.compressor is None
