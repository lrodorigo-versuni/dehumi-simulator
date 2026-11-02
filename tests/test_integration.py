"""End-to-end integration tests for the Phase 1 pipeline (Task 12.1).

configure -> add disturbances -> advance(dt, n) -> query, asserting the
non-negative and saturation-capped invariants throughout and that the
simulation time accumulates to ``n * dt``.
"""

from __future__ import annotations

import math

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import (
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.disturbances import (
    Boiling_Water_Disturbance,
    Person_Disturbance,
)
from room_humidity_simulator.simulator import Simulator


def _run(sim: Simulator, n: int):
    cap = psychrometrics.saturation_content_grams(sim.room.temperature, sim.room.volume)
    trajectory = []
    for _ in range(n):
        state = sim.advance().value
        h = sim.room.exact_absolute_humidity_grams
        assert 0.0 <= h <= cap
        assert 0.0 <= state.relative_humidity_pct <= 100.0
        trajectory.append(h)
    return trajectory


def test_dehumidification_trajectory():
    """A humid room with a cold coil dries monotonically toward the coil dew point."""
    sim = Simulator()
    sim.configure(
        RoomConfig(volume_m3=30.0, temperature_c=25.0, initial_humidity_gm3=18.0),
        FanConfig(flow_rate_m3_per_s=0.2),
        CoilConfig(surface_temperature_c=8.0, condensation_capacity_g_per_s=1.0),
        TimeStepConfig(duration_s=60.0),
    )
    start = sim.room.exact_absolute_humidity_grams
    trajectory = _run(sim, 600)

    assert all(b <= a for a, b in zip(trajectory, trajectory[1:]))
    assert trajectory[-1] < start
    # Removal stops once the room dew point reaches the coil temperature.
    final_dew_point = psychrometrics.dew_point(sim.room.specific_humidity(), 25.0)
    assert final_dew_point >= 8.0 - 0.5
    assert math.isclose(sim.simulation_time, 600 * 60.0)


def test_moisture_loading_saturates_room():
    """Occupants plus boiling water with no fan drive the room to saturation."""
    sim = Simulator()
    sim.configure(
        RoomConfig(20.0, 22.0, 8.0),
        FanConfig(0.0),
        CoilConfig(5.0, 2.0),
        TimeStepConfig(30.0),
    )
    sim.add_disturbance(Person_Disturbance(0.015))
    sim.add_disturbance(Person_Disturbance(0.015))
    kettle = Boiling_Water_Disturbance(0.5)
    sim.add_disturbance(kettle)

    _run(sim, 10)
    # 10 steps of (0.03 + 0.5) g/s * 30 s added with nothing removed.
    assert math.isclose(sim.room.exact_absolute_humidity_grams, 160.0 + 10 * 0.53 * 30.0)

    _run(sim, 200)
    assert sim.room_state().relative_humidity_pct == 100.0

    sim.remove_disturbance(kettle)
    assert sim.active_disturbances[-1] is not kettle
    assert math.isclose(sim.simulation_time, 210 * 30.0)


def test_balanced_load_and_removal():
    """With both a load and a working coil the room settles below saturation.

    In steady state the coil cycles: it removes moisture while the room dew
    point is above the coil temperature and pauses once it drops below.
    """
    sim = Simulator()
    sim.configure(
        RoomConfig(50.0, 26.0, 14.0),
        FanConfig(0.5),
        CoilConfig(6.0, 3.0),
        TimeStepConfig(60.0),
    )
    sim.add_disturbance(Person_Disturbance(0.02))
    _run(sim, 990)
    removals = []
    for _ in range(10):
        sim.advance()
        removals.append(sim.last_step.removed_grams)
    assert any(r > 0.0 for r in removals)
    dew_point = psychrometrics.dew_point(sim.room.specific_humidity(), 26.0)
    assert abs(dew_point - 6.0) < 1.0
    assert sim.room_state().relative_humidity_pct < 100.0
