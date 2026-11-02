"""Phase 2 end-to-end and example tests (Task 20).

configure -> configure compressor -> set state -> advance(dt, n) -> query
energy/power, asserting monotonic energy, power tracking the latest step,
coil coupling switching dehumidification on/off, and the Phase 1 humidity
invariants throughout.
"""

from __future__ import annotations

import pytest

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.compressor import CompressorState
from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.disturbances import Boiling_Water_Disturbance, Person_Disturbance
from room_humidity_simulator.simulator import Simulator

_CONFIG = (
    RoomConfig(45.0, 24.0, 14.0),
    # Low airflow, as in real dehumidifiers: with 400 W the coil gets below
    # the room dew point instead of spending its cooling on sensible heat.
    FanConfig(0.05),
    # Coil configured at room temperature: without a compressor it is idle.
    CoilConfig(24.0, 2.5),
    TimeStepConfig(60.0),
)


def _run(sim: Simulator, n: int):
    cap = psychrometrics.saturation_content_grams(sim.room.temperature, sim.room.volume)
    records = []
    for _ in range(n):
        energy_before = sim.energy_consumed_joules
        sim.advance()
        step = sim.last_step
        assert 0.0 <= sim.room.exact_absolute_humidity_grams <= cap
        assert sim.energy_consumed_joules >= energy_before
        assert sim.power_draw_watts == pytest.approx(step.electrical_joules / step.dt)
        records.append(step)
    return records


def test_compressor_cycles_dehumidification():
    sim = Simulator()
    sim.configure(*_CONFIG)
    sim.add_disturbance(Person_Disturbance(0.02))
    sim.configure_compressor(3.5, 6.0, mode="modulating")

    off = _run(sim, 5)  # starts off
    assert all(r.removed_grams == 0.0 and r.electrical_joules == 0.0 for r in off)

    sim.set_compressor_state("running")
    running = _run(sim, 30)
    assert all(r.removed_grams > 0.0 for r in running)
    assert sim.power_draw_watts > 0.0
    energy_after_running = sim.energy_consumed_joules

    sim.set_compressor_state(CompressorState.OFF)
    _run(sim, 10)
    assert sim.energy_consumed_joules == energy_after_running
    assert sim.power_draw_watts == 0.0


def test_heavy_load_with_compressor_keeps_invariants():
    sim = Simulator()
    sim.configure(RoomConfig(15.0, 30.0, 20.0), FanConfig(1.0), CoilConfig(30.0, 5.0), TimeStepConfig(120.0))
    for _ in range(5):
        sim.add_disturbance(Boiling_Water_Disturbance(0.4))
    sim.configure_compressor(2.5, 4.0, mode="modulating")
    sim.set_compressor_state("running")
    _run(sim, 200)
    assert sim.room_state().relative_humidity_pct <= 100.0
    assert sim.energy_consumed_joules > 0.0


def test_setpoint_above_room_runs_without_consuming_energy():
    """Running with a setpoint above room temperature removes and costs nothing."""
    sim = Simulator()
    sim.configure(*_CONFIG)
    sim.configure_compressor(3.0, 40.0, mode="modulating")
    sim.set_compressor_state("running")
    records = _run(sim, 5)
    assert all(r.removed_grams == 0.0 and r.heat_extracted_joules == 0.0 for r in records)
    assert sim.energy_consumed_joules == 0.0
    assert sim.power_draw_watts == 0.0


def test_no_compressor_parity_example():
    """Requirement 12.4: a Phase 1 scenario is unchanged without a compressor."""
    a = Simulator()
    a.configure(RoomConfig(45.0, 24.0, 14.0), FanConfig(0.4), CoilConfig(6.0, 0.04), TimeStepConfig(60.0))
    # 14 g/m^3 is far above the coil outlet humidity at 6 deg C (~6.8 g/m^3):
    # the coil removes its full 0.04 g/s * 60 s = 2.4 g every step.
    for _ in range(20):
        a.advance()
    assert a.energy_consumed_joules == 0.0 and a.power_draw_watts == 0.0
    assert a.coil.surface_temperature == 6.0
    expected_h = 14.0 * 45.0 - 20 * 2.4
    assert abs(a.room.exact_absolute_humidity_grams - expected_h) <= 0.1
