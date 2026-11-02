"""Example-based unit tests for Psychrometrics edge cases (Task 2.3).

These complement the Property 4 Hypothesis test (Task 2.2) with concrete,
scenario-focused checks at the boundaries of the modeled input space:

- The saturation functions remain **finite and monotonically increasing**
  across the room temperature bounds ``[-50, 100]`` deg C (Requirement 2.4).
- ``relative_humidity_percent`` reports ``0.0`` on the **zero-capacity**
  reporting path (``S(T, V) == 0``, i.e. zero room volume) and on the
  **zero-humidity** path (``abs_grams == 0``) instead of dividing by zero
  (Requirements 2.6, 2.7).

_Requirements: 2.4, 2.7_
"""

from __future__ import annotations

import math

import pytest

from room_humidity_simulator.config import (
    ROOM_TEMPERATURE_MAX_C,
    ROOM_TEMPERATURE_MIN_C,
)
from room_humidity_simulator.psychrometrics import (
    relative_humidity_percent,
    saturation_content_density,
    saturation_content_grams,
    saturation_vapor_pressure,
)

# A fine sweep across the full room temperature range, including both bounds.
# Step chosen so that -50 and 100 are both hit exactly (150 / 0.5 == 300 steps).
_TEMPERATURE_STEP_C = 0.5
_TEMPERATURE_SWEEP = [
    ROOM_TEMPERATURE_MIN_C + i * _TEMPERATURE_STEP_C
    for i in range(
        int((ROOM_TEMPERATURE_MAX_C - ROOM_TEMPERATURE_MIN_C) / _TEMPERATURE_STEP_C)
        + 1
    )
]


# --- Finiteness across the temperature bounds (Requirement 2.4). ---


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, ROOM_TEMPERATURE_MAX_C])
def test_saturation_vapor_pressure_finite_and_positive_at_bounds(temperature_c):
    """es(T) is finite and strictly positive at both temperature bounds."""
    es = saturation_vapor_pressure(temperature_c)
    assert math.isfinite(es)
    assert es > 0.0


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, ROOM_TEMPERATURE_MAX_C])
def test_saturation_density_finite_and_positive_at_bounds(temperature_c):
    """rho_vs(T) is finite and strictly positive at both temperature bounds."""
    density = saturation_content_density(temperature_c)
    assert math.isfinite(density)
    assert density > 0.0


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, ROOM_TEMPERATURE_MAX_C])
def test_saturation_grams_finite_and_positive_at_bounds(temperature_c):
    """S(T, V) is finite and strictly positive at both bounds for positive V."""
    grams = saturation_content_grams(temperature_c, volume_m3=50.0)
    assert math.isfinite(grams)
    assert grams > 0.0


def test_saturation_functions_finite_across_full_sweep():
    """es, rho_vs, and S(T, V) stay finite across the entire [-50, 100] sweep."""
    for temperature_c in _TEMPERATURE_SWEEP:
        es = saturation_vapor_pressure(temperature_c)
        density = saturation_content_density(temperature_c)
        grams = saturation_content_grams(temperature_c, volume_m3=10.0)
        assert math.isfinite(es) and es > 0.0
        assert math.isfinite(density) and density > 0.0
        assert math.isfinite(grams) and grams > 0.0


# --- Monotonicity across the temperature bounds (Requirement 2.4). ---


def test_saturation_vapor_pressure_strictly_increasing_across_bounds():
    """es(T) strictly increases as temperature rises across [-50, 100]."""
    values = [saturation_vapor_pressure(t) for t in _TEMPERATURE_SWEEP]
    for lower, higher in zip(values, values[1:]):
        assert higher > lower


def test_saturation_density_strictly_increasing_across_bounds():
    """rho_vs(T) strictly increases as temperature rises across [-50, 100]."""
    values = [saturation_content_density(t) for t in _TEMPERATURE_SWEEP]
    for lower, higher in zip(values, values[1:]):
        assert higher > lower


def test_saturation_grams_strictly_increasing_across_bounds():
    """S(T, V) strictly increases with temperature for a fixed positive volume."""
    values = [saturation_content_grams(t, volume_m3=25.0) for t in _TEMPERATURE_SWEEP]
    for lower, higher in zip(values, values[1:]):
        assert higher > lower


def test_saturation_grams_lower_bound_below_upper_bound():
    """Capacity at the minimum bound is far below capacity at the maximum bound."""
    at_min = saturation_content_grams(ROOM_TEMPERATURE_MIN_C, volume_m3=1.0)
    at_max = saturation_content_grams(ROOM_TEMPERATURE_MAX_C, volume_m3=1.0)
    assert at_min < at_max


# --- Zero-capacity reporting path: S(T, V) == 0 (Requirement 2.7). ---


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, 0.0, 20.0, ROOM_TEMPERATURE_MAX_C])
def test_zero_volume_drives_capacity_to_zero(temperature_c):
    """A zero room volume forces S(T, V) to exactly 0.0 at any temperature."""
    assert saturation_content_grams(temperature_c, volume_m3=0.0) == 0.0


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, 0.0, 20.0, ROOM_TEMPERATURE_MAX_C])
def test_relative_humidity_zero_on_zero_capacity_path(temperature_c):
    """RH is reported as 0.0 when S(T, V) == 0 (zero volume), never dividing by zero."""
    rh = relative_humidity_percent(abs_grams=5.0, temperature_c=temperature_c, volume_m3=0.0)
    assert rh == 0.0


# --- Zero-humidity path: abs_grams == 0 (Requirement 2.6). ---


@pytest.mark.parametrize("temperature_c", [ROOM_TEMPERATURE_MIN_C, 0.0, 20.0, ROOM_TEMPERATURE_MAX_C])
def test_relative_humidity_zero_on_zero_humidity_path(temperature_c):
    """RH is reported as 0.0 when there is no moisture (abs_grams == 0)."""
    rh = relative_humidity_percent(abs_grams=0.0, temperature_c=temperature_c, volume_m3=50.0)
    assert rh == 0.0


def test_relative_humidity_zero_when_both_humidity_and_capacity_zero():
    """RH is 0.0 when both the moisture and the capacity are zero."""
    rh = relative_humidity_percent(abs_grams=0.0, temperature_c=20.0, volume_m3=0.0)
    assert rh == 0.0
