"""Tests for the open-window disturbance (signed air exchange with gusts)."""

from __future__ import annotations

import copy
import math
import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator import (
    CoilConfig,
    FanConfig,
    Open_Window_Disturbance,
    Person_Disturbance,
    RoomConfig,
    Simulator,
    TimeStepConfig,
    psychrometrics,
)
from room_humidity_simulator.control import ClosedLoop
from room_humidity_simulator.controllers import AlwaysOn, Hysteresis
from tests.strategies import FixedDisturbance


def room_sim(volume=30.0, temperature=22.0, humidity_gm3=11.6, dt=60.0):
    sim = Simulator()
    sim.configure(RoomConfig(volume, temperature, humidity_gm3), FanConfig(0.0), CoilConfig(temperature, 0.0), TimeStepConfig(dt))
    return sim


def test_outdoor_humidity_conserves_vapor_pressure():
    w = Open_Window_Disturbance(300.0, 5.0, 80.0)
    e = 0.8 * psychrometrics.saturation_vapor_pressure(5.0)
    assert w.outdoor_humidity_gm3(22.0) == pytest.approx(216.7 * e / (273.15 + 22.0))
    # At the outdoor temperature it is simply RH x saturation density.
    assert w.outdoor_humidity_gm3(5.0) == pytest.approx(0.8 * psychrometrics.saturation_content_density(5.0))


def test_steady_window_follows_the_exact_exponential():
    sim = room_sim()
    w = Open_Window_Disturbance(300.0, 5.0, 80.0)  # steady: no gusts
    assert sim.add_disturbance(w).is_ok
    q0 = sim.room.specific_humidity()
    q_out = w.outdoor_humidity_gm3(22.0)
    sim.advance(n=10)
    expected = q_out + (q0 - q_out) * math.exp(-(300.0 / 3600.0) * 600.0 / 30.0)
    assert sim.room.specific_humidity() == pytest.approx(expected, rel=1e-9)
    assert sim.last_step.disturbance_errors == ()


@given(
    airflow=st.floats(0.0, 10000.0),
    t_out=st.floats(-50.0, 60.0),
    rh_out=st.floats(0.0, 100.0),
    rh_room=st.floats(0.0, 100.0),
    volume=st.floats(1.0, 1000.0),
    dt=st.floats(1.0, 3600.0),
    gust=st.floats(0.0, 2.0),
)
def test_window_moves_toward_outdoor_humidity_without_overshoot(airflow, t_out, rh_out, rh_room, volume, dt, gust):
    """Property: one step brings the room toward q_out and never past it."""
    sim = room_sim(volume=volume, humidity_gm3=rh_room / 100.0 * psychrometrics.saturation_content_density(22.0), dt=dt)
    w = Open_Window_Disturbance(airflow, t_out, rh_out, gustiness=gust, seed=3)
    assert sim.add_disturbance(w).is_ok
    q0 = sim.room.specific_humidity()
    q_out = min(w.outdoor_humidity_gm3(22.0), psychrometrics.saturation_content_density(22.0))
    sim.advance()
    q1 = sim.room.specific_humidity()
    lo, hi = sorted((q0, q_out))
    assert lo - 1e-9 <= q1 <= hi + 1e-9
    assert w.last_airflow_m3_per_h >= 0.0


def test_gusts_are_reproducible_bounded_and_average_out():
    a = Open_Window_Disturbance(300.0, 5.0, 80.0, gustiness=0.4, gust_time_s=60.0, seed=7)
    b = Open_Window_Disturbance(300.0, 5.0, 80.0, gustiness=0.4, gust_time_s=60.0, seed=7)
    flows_a = [a.next_airflow_m3_per_h(10.0) for _ in range(20000)]
    flows_b = [b.next_airflow_m3_per_h(10.0) for _ in range(20000)]
    assert flows_a == flows_b
    assert min(flows_a) >= 0.0
    assert statistics.mean(flows_a) == pytest.approx(300.0, rel=0.05)
    # Correlated gusts: consecutive 10 s samples are close (correlation exp(-10/60) ~ 0.85).
    x, y = flows_a[:-1], flows_a[1:]
    mx, my = statistics.mean(x), statistics.mean(y)
    cov = sum((u - mx) * (v - my) for u, v in zip(x, y))
    lag = cov / math.sqrt(sum((u - mx) ** 2 for u in x) * sum((v - my) ** 2 for v in y))
    assert 0.75 < lag < 0.95
    steady = Open_Window_Disturbance(300.0, 5.0, 80.0, gustiness=0.0)
    assert {steady.next_airflow_m3_per_h(10.0) for _ in range(5)} == {300.0}


def test_window_sees_the_same_wind_whatever_the_controller():
    """A/B fairness: the gust sequence does not depend on what the controller does."""
    flows = []
    for controller in (AlwaysOn(), Hysteresis()):
        sim = room_sim(humidity_gm3=14.0, dt=30.0)
        sim.configure_compressor(3.0, 5.0, 400.0)
        w = Open_Window_Disturbance(250.0, 8.0, 85.0, gustiness=0.4, seed=2)
        sim.add_disturbance(w)
        seen = []
        ClosedLoop(sim, controller, before_step=lambda s, t: seen.append(w.last_airflow_m3_per_h)).run(120)
        flows.append(seen)
    assert flows[0] == flows[1]


def test_negative_contributions_still_rejected_for_plain_disturbances():
    sim = room_sim()
    sim.add_disturbance(FixedDisturbance(-1.0))
    sim.add_disturbance(Open_Window_Disturbance(300.0, 5.0, 80.0))
    sim.add_disturbance(Person_Disturbance(0.015))
    sim.advance()
    errors = sim.last_step.disturbance_errors
    assert len(errors) == 1 and isinstance(errors[0].disturbance, FixedDisturbance)
    assert sim.last_step.added_grams < 0.0  # the window dried the room


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(airflow_m3_per_h=-1.0), "window airflow out of range"),
        (dict(outdoor_temperature_c=70.0), "outdoor temperature out of range"),
        (dict(outdoor_rh_pct=120.0), "outdoor humidity out of range"),
        (dict(outdoor_rh_pct=math.nan), "outdoor humidity out of range"),
        (dict(gustiness=3.0), "gustiness out of range"),
        (dict(gust_time_s=0.0), "gust time must be greater than 0"),
    ],
)
def test_invalid_window_is_rejected(kwargs, message):
    params = dict(airflow_m3_per_h=300.0, outdoor_temperature_c=5.0, outdoor_rh_pct=80.0)
    params.update(kwargs)
    sim = room_sim()
    result = sim.add_disturbance(Open_Window_Disturbance(**params))
    assert result.is_error and result.error.message == message
    assert sim.active_disturbances == ()


def test_web_scenario_with_window():
    from webapp.scenario import DEFAULT_SCENARIO, run_scenario

    s = copy.deepcopy(DEFAULT_SCENARIO)
    s["disturbances"].append({
        "kind": "window", "airflow_m3_per_h": 250, "gustiness_pct": 40, "gust_time_s": 60,
        "outdoor_temperature_c": 8, "outdoor_rh_pct": 85, "start_min": 40, "end_min": 60,
    })
    series = run_scenario(s)["series"]
    at = {round(t): i for i, t in enumerate(series["t_min"])}
    assert series["added_g_per_min"][at[42]] < 0.0  # window drying the room
    assert series["added_g_per_min"][at[65]] == pytest.approx(1.8)  # only the two people again
    assert run_scenario(s)["series"] == series  # same seed, same wind
    s["disturbances"][-1]["gustiness_pct"] = 500
    assert run_scenario(s)["error"]["field"] == "disturbances[3].gustiness_pct"
