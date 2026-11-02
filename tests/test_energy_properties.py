"""Property-based tests for the Phase 2 energy helpers in Psychrometrics.

Implements the design's correctness Property 19 for the latent/sensible heat
model using Hypothesis. The shared ``simulator`` Hypothesis profile (registered
in ``conftest.py``) runs a minimum of 100 examples per property test,
satisfying the design requirement of at least 100 iterations.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.psychrometrics import (
    latent_heat_joules,
    sensible_heat_joules,
)

# Mass of water condensed in a step, in grams. Always >= 0 from the Phase 1
# coil removal algorithm; includes 0.0 so the no-latent-heat edge is covered.
_REMOVED_GRAMS = st.floats(
    min_value=0.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Volume of air moved across the coil this step, in m^3. Includes 0.0 so the
# zero-sensible-heat edge (Requirement 10.4) is exercised.
_MOVED_AIR_VOLUME = st.one_of(
    st.just(0.0),
    st.floats(
        min_value=0.0,
        max_value=36000.0,  # flow_rate <= 10 m^3/s over dt <= 3600 s
        allow_nan=False,
        allow_infinity=False,
    ),
)

# Room temperature within the permitted bounds (Room_Temperature_Bounds).
_ROOM_TEMP = st.floats(
    min_value=-50.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)

# Coil surface temperature within the permitted temperature bounds. Combined
# with the room temperature strategy this yields setpoints both below and
# at/above the room temperature so the zero-sensible-heat path is exercised.
_COIL_SURFACE_TEMP = st.floats(
    min_value=-50.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)


# Feature: room-humidity-simulator, Property 19: Heat extracted is latent plus sensible heat
@given(
    removed_grams=_REMOVED_GRAMS,
    moved_air_volume=_MOVED_AIR_VOLUME,
    room_temp=_ROOM_TEMP,
    coil_surface_temp=_COIL_SURFACE_TEMP,
)
def test_property_19_heat_extracted_is_latent_plus_sensible(
    removed_grams, moved_air_volume, room_temp, coil_surface_temp
):
    """Heat extracted equals latent plus sensible heat, always >= 0.0 J.

    While the compressor is running, the heat extracted in joules is the latent
    component (``removedGrams * 2260``) plus the sensible component, where the
    sensible component is ``movedAirVolume * airDensity * cp *
    (roomTemp - coilSurfaceTemp)`` only when ``movedAirVolume > 0`` and
    ``roomTemp > coilSurfaceTemp`` and is ``0.0`` otherwise (in particular
    ``0.0`` when ``movedAirVolume = 0``). The resulting heat is never negative.

    Validates: Requirements 10.1, 10.2, 10.3, 10.4
    """
    latent = latent_heat_joules(removed_grams)
    sensible = sensible_heat_joules(moved_air_volume, room_temp, coil_surface_temp)
    heat_extracted = max(latent + sensible, 0.0)

    # Latent term: exactly removedGrams * 2260 (Requirement 10.2).
    assert latent == removed_grams * 2260.0
    assert latent >= 0.0

    # Sensible term: the cooling energy only when there is air to cool and a
    # positive temperature difference; 0.0 otherwise (Requirements 10.3, 10.4).
    if moved_air_volume > 0.0 and room_temp > coil_surface_temp:
        expected_sensible = (
            moved_air_volume * 1.2 * 1005.0 * (room_temp - coil_surface_temp)
        )
        assert math.isclose(sensible, expected_sensible, rel_tol=1e-9, abs_tol=0.0)
        # Subnormal inputs can underflow the product to 0.0; only require a
        # strictly positive term when the exact product is representable.
        assert sensible >= 0.0
        if expected_sensible > 0.0:
            assert sensible > 0.0
    else:
        # In particular 0.0 when moved_air_volume == 0 (Requirement 10.4).
        assert sensible == 0.0

    # Heat extracted is the sum of the two components (Requirement 10.1) and is
    # always non-negative.
    assert math.isclose(heat_extracted, latent + sensible, rel_tol=0.0, abs_tol=0.0)
    assert heat_extracted >= 0.0
