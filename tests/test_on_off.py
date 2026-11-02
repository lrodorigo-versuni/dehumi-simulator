"""Tests for the on/off compressor: full power, coil temperature from the heat balance."""

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
RHO_CP = 1.2 * 1005.0


def unit(room_t=20.0, rh=0.6, flow_h=150.0, cop=LUT, power=400.0, setpoint=5.0, dt=30.0,
         defrost=DefrostSettings(), capacity=1000.0, still=3.0, people=1):
    sim = Simulator()
    sat = psychrometrics.saturation_content_density(room_t)
    sim.configure(RoomConfig(20.0, room_t, rh * sat), FanConfig(flow_h / 3600.0), CoilConfig(room_t, 0.5, 0.85), TimeStepConfig(dt))
    for _ in range(people):
        sim.add_disturbance(Person_Disturbance(0.015))
    assert sim.configure_compressor(cop, setpoint, power, defrost=defrost,
                                    coil_heat_capacity_j_per_k=capacity, still_air_w_per_k=still).is_ok
    return sim


def test_on_off_is_the_default_and_runs_at_full_power():
    sim = unit()
    assert sim.compressor.mode == "on_off"
    sim.set_compressor_state("running")
    for _ in range(20):
        sim.advance()
        step = sim.last_step
        assert step.electrical_joules == pytest.approx(400.0 * 30.0)
        assert sim.power_draw_watts == pytest.approx(400.0)
        assert step.heat_extracted_joules == pytest.approx(step.cop * step.electrical_joules)


def test_setpoint_does_not_affect_an_on_off_unit():
    runs = []
    for setpoint in (-20.0, 1.0, 15.0):
        sim = unit(setpoint=setpoint, room_t=15.0)
        sim.set_compressor_state("running")
        runs.append([(sim.advance(), sim.last_step.coil_surface_temperature_c, sim.room.exact_absolute_humidity_grams)[1:] for _ in range(40)])
    assert runs[0] == runs[1] == runs[2]


def test_pull_down_and_warm_up_follow_the_exact_thermal_response():
    """Dry room (no condensation), fixed COP: C dT/dt = k (T_room - T) - COP*P is linear."""
    room_t, flow_h, cop, power, c = 22.0, 150.0, 3.0, 400.0, 1000.0
    sim = unit(room_t=room_t, rh=0.0, flow_h=flow_h, cop=cop, power=power, defrost=None, capacity=c, people=0)
    k = flow_h / 3600.0 * RHO_CP * 0.85 + 3.0
    t_inf = room_t - cop * power / k
    sim.set_compressor_state("running")
    for n in range(1, 11):
        sim.advance()
        expected = t_inf + (room_t - t_inf) * math.exp(-k * 30.0 * n / c)
        assert sim.coil.surface_temperature == pytest.approx(expected, abs=1e-6)
    t_off = sim.coil.surface_temperature
    sim.set_compressor_state("off")
    for n in range(1, 6):
        sim.advance()
        expected = room_t + (t_off - room_t) * math.exp(-k * 30.0 * n / c)
        assert sim.coil.surface_temperature == pytest.approx(expected, abs=1e-6)
        assert sim.last_step.electrical_joules == 0.0


def test_less_airflow_means_a_colder_coil():
    temps = []
    for flow_h in (90.0, 150.0, 220.0):
        sim = unit(flow_h=flow_h, defrost=None)
        sim.set_compressor_state("running")
        sim.advance(n=20)
        temps.append(sim.coil.surface_temperature)
    assert temps[0] < temps[1] < temps[2]


def test_cool_room_frosts_and_defrosts_by_itself():
    """No setpoint involved: the full-power coil goes below 0, frosts, and the unit defrosts."""
    sim = unit(room_t=15.0, rh=0.7, setpoint=30.0)
    sim.set_compressor_state("running")
    steps = []
    for _ in range(120):
        sim.advance()
        steps.append(sim.last_step)
    assert min(s.coil_surface_temperature_c for s in steps) < -1.0
    assert max(s.frost_g for s in steps) > 10.0
    assert sim.compressor.defrost_count >= 2
    for s in steps:
        if s.defrosting:
            assert not s.compressor_running and s.electrical_joules == 0.0
    # While ice remains during a defrost the coil cannot warm above 0 C.
    for prev, s in zip(steps, steps[1:]):
        if s.defrosting and s.frost_g > 0.0:
            assert s.coil_surface_temperature_c <= 0.0 + 1e-9
    frozen = sum(s.removed_grams for s in steps if s.coil_surface_temperature_c < 0.0 and s.compressor_running)
    melted = sum(s.melted_g for s in steps)
    assert melted <= frozen + 1e-6 or melted == pytest.approx(frozen, rel=0.2)
    assert sum(s.reevaporated_g for s in steps) == pytest.approx(0.3 * melted)


def test_warm_room_never_frosts():
    sim = unit(room_t=26.0, rh=0.6, flow_h=180.0)
    sim.set_compressor_state("running")
    for _ in range(120):
        sim.advance()
        assert sim.last_step.frost_g == 0.0 and not sim.last_step.defrosting
    assert sim.coil.surface_temperature > 0.0


@settings(max_examples=40)
@given(
    room_t=st.floats(5.0, 35.0),
    rh=st.floats(0.0, 1.0),
    flow_h=st.floats(0.0, 400.0),
    dt=st.floats(1.0, 600.0),
    toggles=st.lists(st.booleans(), min_size=1, max_size=20),
)
def test_coil_never_warmer_than_the_room(room_t, rh, flow_h, dt, toggles):
    """The coil only receives heat from the room: it can never exceed room temperature."""
    sim = unit(room_t=room_t, rh=rh, flow_h=flow_h, dt=dt)
    for on in toggles:
        sim.set_compressor_state("running" if on else "off")
        sim.advance()
        assert sim.coil.surface_temperature <= room_t + 1e-9
        assert sim.coil.frost_g >= 0.0
        assert 0.0 <= sim.room_state().relative_humidity_pct <= 100.0


def test_closed_loop_sees_the_real_coil_temperature():
    seen = []

    class Watch:
        def step(self, m, dt):
            seen.append(m.coil_temperature_c)
            return Commands(compressor_on=True, fan_speed=1)

    ClosedLoop(unit(room_t=15.0, rh=0.7, dt=30.0), Watch()).run(40)
    assert seen[0] == pytest.approx(15.0)
    assert min(seen) < 0.0  # the controller can watch the coil approach freezing


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(max_power_w=math.inf), "an on/off compressor needs a finite max power"),
        (dict(mode="turbo"), "compressor mode must be 'on_off' or 'modulating'"),
        (dict(coil_heat_capacity_j_per_k=0.0), "coil heat capacity must be greater than 0"),
        (dict(still_air_w_per_k=-1.0), "still-air coupling must be >= 0"),
    ],
)
def test_invalid_on_off_settings(kwargs, message):
    sim = Simulator()
    params = dict(max_power_w=400.0)
    params.update(kwargs)
    result = sim.configure_compressor(3.0, 5.0, **params)
    assert result.is_error and result.error.message == message
