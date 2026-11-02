"""Tests for closed-loop control (actuators, protections, sensor model, KPIs)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from controllers.my_controller import MyController
from room_humidity_simulator import (
    Boiling_Water_Disturbance,
    CoilConfig,
    CopTable,
    FanConfig,
    Person_Disturbance,
    RoomConfig,
    Simulator,
    TimeStepConfig,
)
from room_humidity_simulator.control import (
    ClosedLoop,
    Commands,
    ComfortBand,
    CompressorProtection,
    ControllerError,
    FanSpeed,
    SensorModel,
    _Sensor,
)
from room_humidity_simulator.controllers import AlwaysOn, Hysteresis, StagedHysteresis

LUT = CopTable([-10, 0, 10, 20, 25], [1.8, 2.5, 3.4, 4.7, 5.5])


def make_sim(dt=30.0, humidity=14.0, people=2):
    sim = Simulator()
    sim.configure(RoomConfig(20.0, 25.0, humidity), FanConfig(0.0), CoilConfig(25.0, 0.5, 0.85), TimeStepConfig(dt))
    sim.configure_compressor(LUT, 5.0, 400.0)
    for _ in range(people):
        sim.add_disturbance(Person_Disturbance(0.015))
    return sim


class Scripted:
    """Replays a fixed list of commands (then repeats the last one)."""

    def __init__(self, commands):
        self.commands = list(commands)
        self.seen = []

    def step(self, m, dt):
        self.seen.append(m)
        i = min(len(self.seen) - 1, len(self.commands) - 1)
        return self.commands[i]


def test_set_fan_flow_is_validated_and_applies_next_step():
    sim = make_sim()
    assert sim.set_fan_flow(11.0).error.message == "flow rate exceeds maximum"
    assert sim.set_fan_flow(math.nan).error.message == "flow rate invalid"
    assert sim.fan.flow_rate == 0.0
    assert sim.set_fan_flow(0.05).is_ok
    assert sim.fan.flow_rate == 0.0  # not yet
    sim.advance()
    assert sim.fan.flow_rate == 0.05
    assert sim.last_step.moved_air_volume_m3 == pytest.approx(0.05 * 30.0)


def test_closed_loop_requires_a_compressor_and_valid_commands():
    sim = Simulator()
    sim.configure(RoomConfig(20.0, 25.0, 14.0), FanConfig(0.0), CoilConfig(25.0, 0.5), TimeStepConfig(30.0))
    with pytest.raises(ValueError, match="compressor"):
        ClosedLoop(sim, AlwaysOn())
    for bad in (None, (True, 1), Commands(True, 9), Commands(True, -1), Commands(True, 1.0)):
        with pytest.raises(ControllerError):
            ClosedLoop(make_sim(), Scripted([bad])).run(3)


@given(
    commands=st.lists(st.tuples(st.booleans(), st.integers(0, 3)), min_size=1, max_size=60),
    min_on=st.sampled_from([0.0, 60.0, 180.0]),
    min_off=st.sampled_from([0.0, 90.0, 300.0]),
)
def test_compressor_protections_are_never_violated(commands, min_on, min_off):
    """Whatever the controller asks, min on/off times hold and no compressor runs without fan."""
    prot = CompressorProtection(min_on_s=min_on, min_off_s=min_off, require_fan=True)
    loop = ClosedLoop(make_sim(), Scripted([Commands(c, f) for c, f in commands]), protection=prot)
    s = loop.run(len(commands)).series
    on, fan, t = s["compressor_on"][1:], s["fan_speed"][1:], s["t_s"][:-1]
    assert not any(o and f == 0 for o, f in zip(on, fan))
    # Each completed run / rest lasts at least its minimum.
    changes = [i for i in range(1, len(on)) if on[i] != on[i - 1]]
    for last, i in zip(changes, changes[1:]):
        assert t[i] - t[last] >= (min_on if on[last] else min_off) - 1e-9
    # The fan only deviates from the request to keep a running compressor ventilated.
    for (c, f), o, actual in zip(commands, on, fan):
        assert actual == f or (o and f == 0 and actual == 1)
    k = loop.result.kpis
    assert k["compressor_starts"] == sum(1 for i in range(len(on)) if on[i] and (i == 0 or not on[i - 1]))
    assert k["time_in_band_pct"] + k["time_above_band_pct"] + k["time_below_band_pct"] == pytest.approx(100.0)
    assert k["energy_kwh"] == pytest.approx(k["compressor_energy_kwh"] + k["fan_energy_kwh"])
    assert k["energy_kwh"] == pytest.approx(s["energy_kwh"][-1])


def test_blocked_requests_are_counted():
    # Toggle every 30 s step while the protection needs 180 s: most requests are refused.
    toggles = [Commands(i % 2 == 0, 2) for i in range(20)]
    k = ClosedLoop(make_sim(), Scripted(toggles), protection=CompressorProtection(180.0, 180.0)).run(20).kpis
    assert k["blocked_commands"] > 5
    assert k["compressor_starts"] <= 2


def test_fan_energy_and_speeds_follow_the_commands():
    speeds = (FanSpeed("off", 0.0, 0.0), FanSpeed("only", 120.0, 30.0))
    loop = ClosedLoop(make_sim(), Scripted([Commands(False, 1)]), fan_speeds=speeds)
    s = loop.run(10).series
    assert s["fan_flow_m3_per_h"][1:] == [120.0] * 10
    assert loop.result.kpis["fan_energy_kwh"] == pytest.approx(30.0 * 300.0 / 3.6e6)
    assert loop.result.kpis["compressor_energy_kwh"] == 0.0
    assert loop.result.kpis["fan_only_pct"] == 100.0


def test_sensor_model_noise_resolution_and_lag():
    assert _Sensor(SensorModel()).read(52.345, 30.0) == 52.345
    lagged = _Sensor(SensorModel(time_constant_s=60.0))
    lagged.read(40.0, 1.0)
    for _ in range(60):
        value = lagged.read(60.0, 1.0)
    assert value == pytest.approx(40.0 + 20.0 * (1 - math.exp(-1.0)), rel=1e-6)
    quantized = _Sensor(SensorModel(resolution_pct=0.5)).read(52.3, 30.0)
    assert quantized == 52.5
    a = [_Sensor(SensorModel(noise_std_pct=1.0, seed=7)).read(50.0, 30.0) for _ in range(3)]
    assert a[0] == a[1] == a[2] != 50.0  # same seed -> same noise, reproducible


def test_controller_sees_measured_rh_and_protection_state():
    ctrl = Scripted([Commands(True, 2), Commands(False, 2)])
    ClosedLoop(make_sim(), ctrl, sensor=SensorModel(noise_std_pct=2.0, seed=3)).run(4)
    first, second = ctrl.seen[0], ctrl.seen[1]
    assert first.compressor_can_switch and not first.compressor_on
    assert second.compressor_on and not second.compressor_can_switch  # 30 s < 180 s min on
    true_rh = make_sim().room_state().relative_humidity_pct
    assert first.relative_humidity_pct != true_rh  # noisy reading
    assert first.target_rh_pct == 50.0


def test_baselines_behave_as_expected():
    always = ClosedLoop(make_sim(), AlwaysOn()).run(240).kpis
    assert always["compressor_on_pct"] == 100.0 and always["compressor_starts"] == 1
    assert always["time_below_band_pct"] > 50.0  # over-dries the room
    for ctrl in (Hysteresis(), StagedHysteresis(), MyController()):
        k = ClosedLoop(make_sim(), ctrl, band=ComfortBand(50, 45, 55)).run(480).kpis
        assert k["time_in_band_pct"] > 80.0, type(ctrl).__name__
        assert k["energy_kwh"] < always["energy_kwh"] * 2  # 480 vs 240 steps
        assert k["compressor_starts"] < 20


def test_before_step_hook_drives_a_timeline():
    shower = Boiling_Water_Disturbance(0.4)

    def hook(sim, t):
        if t == 600.0:
            sim.add_disturbance(shower)
        if t == 1200.0:
            sim.remove_disturbance(shower)

    s = ClosedLoop(make_sim(), Hysteresis(), before_step=hook).run(60).series
    added = dict(zip(s["t_s"], s["added_g"]))
    assert added[630.0] == pytest.approx((0.03 + 0.4) * 30.0)
    assert added[1230.0] == pytest.approx(0.03 * 30.0)
