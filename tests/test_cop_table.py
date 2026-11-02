"""Tests for the COP lookup table and its use by the compressor (Task 24).

Covers the LUT interpolation (Property 26), simulation with a LUT-driven
compressor (Property 27) and the LUT validation errors (Requirement 8.11).
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.config import CoilConfig, FanConfig, RoomConfig, TimeStepConfig
from room_humidity_simulator.cop_table import CopTable, validate_cop_table
from room_humidity_simulator.disturbances import Person_Disturbance
from room_humidity_simulator.simulator import Simulator
from tests.strategies import (
    configurations,
    configured_simulator,
    disturbance_sets,
    max_powers,
    setpoints,
)

_LUT_1D = CopTable([-10.0, 0.0, 10.0, 20.0], [2.0, 2.5, 3.5, 5.0])
_LUT_2D = CopTable(
    coil_temperatures_c=[0.0, 10.0],
    room_temperatures_c=[20.0, 30.0],
    cops=[[3.0, 4.0], [2.0, 3.0]],
)


def test_1d_interpolation_points_between_and_clamped():
    assert _LUT_1D.cop(0.0, 25.0) == 2.5
    assert _LUT_1D.cop(5.0, 25.0) == pytest.approx(3.0)
    assert _LUT_1D.cop(15.0, 99.0) == pytest.approx(4.25)
    # Clamped, never extrapolated.
    assert _LUT_1D.cop(-40.0, 25.0) == 2.0
    assert _LUT_1D.cop(60.0, 25.0) == 5.0


def test_2d_bilinear_interpolation():
    # Coil 5 C: 3.5 at room 20, 2.5 at room 30 -> 3.0 at room 25.
    assert _LUT_2D.cop(5.0, 25.0) == pytest.approx(3.0)
    assert _LUT_2D.cop(0.0, 30.0) == 2.0
    assert _LUT_2D.cop(10.0, 10.0) == 4.0  # room clamped to 20
    assert _LUT_2D.cop(2.5, 22.5) == pytest.approx(3.0)


def test_single_point_table_is_constant():
    table = CopTable([5.0], [3.2])
    assert validate_cop_table(table).is_ok
    assert table.cop(-50.0, 0.0) == table.cop(100.0, 100.0) == 3.2


@pytest.mark.parametrize(
    "table, reason",
    [
        (CopTable([], []), "coil temperatures is empty"),
        (CopTable([0.0, 0.0], [2.0, 3.0]), "coil temperatures must be strictly increasing"),
        (CopTable([10.0, 0.0], [2.0, 3.0]), "coil temperatures must be strictly increasing"),
        (CopTable([0.0, math.nan], [2.0, 3.0]), "coil temperatures must contain finite numbers"),
        (CopTable("0,10", [2.0, 3.0]), "coil temperatures must be a sequence"),
        (CopTable([0.0, 10.0], [2.0]), "needs one COP per coil temperature"),
        (CopTable([0.0, 10.0], [2.0, 0.0]), "every COP must be greater than 0.0 and at most 20.0"),
        (CopTable([0.0, 10.0], [2.0, 25.0]), "every COP must be greater than 0.0 and at most 20.0"),
        (CopTable([0.0, 10.0], [2.0, None]), "every COP must be greater than 0.0 and at most 20.0"),
        (CopTable([0.0, 10.0], [[2.0, 3.0]], [20.0, 30.0]), "needs one COP row per room temperature"),
        (CopTable([0.0, 10.0], [[2.0, 3.0], [2.0]], [20.0, 30.0]), "needs one COP per coil temperature"),
        (CopTable([0.0, 10.0], [[2.0, 3.0], [2.0, 3.0]], [30.0, 20.0]), "room temperatures must be strictly increasing"),
    ],
)
def test_invalid_tables_are_rejected_with_reason(table, reason):
    result = validate_cop_table(table)
    assert result.is_error
    assert result.error.field == "copTable"
    assert result.error.message == "COP table invalid: " + reason


def test_invalid_table_keeps_previous_compressor():
    sim = Simulator()
    assert sim.configure_compressor(_LUT_1D, 5.0, mode="modulating").is_ok
    previous = sim.compressor
    result = sim.configure_compressor(CopTable([0.0], [-1.0]), 5.0, mode="modulating")
    assert result.is_error and result.error.field == "copTable"
    assert sim.compressor is previous


@st.composite
def cop_tables(draw, monotone=False):
    """Random valid 1D or 2D COP tables (optionally non-decreasing in coil temperature)."""
    n_coil = draw(st.integers(1, 6))
    coil = sorted(draw(st.lists(st.floats(-40.0, 40.0), min_size=n_coil, max_size=n_coil, unique=True)))
    two_d = draw(st.booleans())
    n_rows = draw(st.integers(1, 4)) if two_d else 1
    rows = []
    for _ in range(n_rows):
        row = draw(st.lists(st.floats(0.5, 20.0), min_size=len(coil), max_size=len(coil)))
        rows.append(sorted(row) if monotone else row)
    if not two_d:
        return CopTable(coil, rows[0])
    room = sorted(draw(st.lists(st.floats(-20.0, 60.0), min_size=n_rows, max_size=n_rows, unique=True)))
    return CopTable(coil, rows, room)


# Feature: room-humidity-simulator, Property 26: COP interpolation stays within the table and follows its trend
@given(
    table=cop_tables(monotone=True),
    coil_temps=st.tuples(st.floats(-60.0, 60.0), st.floats(-60.0, 60.0)),
    room=st.floats(-30.0, 70.0),
)
def test_cop_interpolation_bounded_and_monotone(table, coil_temps, room):
    assert validate_cop_table(table).is_ok
    lo, hi = sorted(coil_temps)
    cop_lo, cop_hi = table.cop(lo, room), table.cop(hi, room)
    for value in (cop_lo, cop_hi):
        assert table.min_cop - 1e-9 <= value <= table.max_cop + 1e-9
    # A table non-decreasing in coil temperature interpolates non-decreasingly.
    assert cop_hi >= cop_lo - 1e-9


# Feature: room-humidity-simulator, Property 27: A LUT-driven compressor stays within its power and table and charges heat over COP
@given(
    config=configurations(),
    table=cop_tables(),
    setpoint=setpoints,
    max_power=max_powers,
    ds=disturbance_sets(5),
    n=st.integers(1, 5),
)
def test_lut_compressor_power_cop_and_coil(config, table, setpoint, max_power, ds, n):
    sim = configured_simulator(config)
    for d in ds:
        sim.add_disturbance(d)
    assert sim.configure_compressor(table, setpoint, max_power, defrost=None, mode="modulating").is_ok
    sim.set_compressor_state("running")
    room_temp = sim.room.temperature
    for _ in range(n):
        before = sim.energy_consumed_joules
        sim.advance()
        step = sim.last_step
        assert 0.0 <= sim.power_draw_watts <= max_power * (1 + 1e-9)
        assert sim.energy_consumed_joules == before + step.electrical_joules
        # The effective COP of the step is one the table can produce.
        assert table.min_cop * (1 - 1e-9) <= step.cop <= table.max_cop * (1 + 1e-9)
        if step.electrical_joules > 0.0:
            assert math.isclose(step.heat_extracted_joules / step.electrical_joules, step.cop, rel_tol=1e-9)
        if step.power_limited:
            assert setpoint <= step.coil_surface_temperature_c <= room_temp
        else:
            assert step.coil_surface_temperature_c == setpoint
            assert math.isclose(
                step.electrical_joules,
                step.heat_extracted_joules / table.cop(setpoint, room_temp),
                rel_tol=1e-9,
                abs_tol=1e-12,
            )


def test_higher_cop_at_warm_coil_makes_warm_setpoints_cheaper():
    """With a rising COP, a warmer setpoint uses less energy per gram removed."""
    lut = CopTable([-10.0, 0.0, 10.0, 20.0], [1.8, 2.5, 3.4, 4.7])

    def grams_per_kwh(setpoint):
        sim = Simulator()
        sim.configure(RoomConfig(20.0, 25.0, 18.0), FanConfig(0.05), CoilConfig(25.0, 0.5), TimeStepConfig(60.0))
        sim.add_disturbance(Person_Disturbance(0.03))
        sim.configure_compressor(lut, setpoint, 400.0, mode="modulating")
        sim.set_compressor_state("running")
        removed = 0.0
        for _ in range(60):
            sim.advance()
            removed += sim.last_step.removed_grams
        return removed / (sim.energy_consumed_joules / 3.6e6)

    assert grams_per_kwh(12.0) > grams_per_kwh(2.0)
