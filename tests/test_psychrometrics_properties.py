"""Property-based tests for the Psychrometrics physics module.

Implements the design's correctness properties for the stateless physics
functions using Hypothesis. The shared ``simulator`` Hypothesis profile
(registered in ``conftest.py``) runs a minimum of 100 examples per property
test, satisfying the design requirement of at least 100 iterations.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.psychrometrics import (
    RELATIVE_HUMIDITY_MAX_PCT,
    RELATIVE_HUMIDITY_MIN_PCT,
    relative_humidity_percent,
    saturation_content_grams,
)

# Room temperature bounds from the requirements (Room_Temperature_Bounds).
_TEMPERATURES = st.floats(
    min_value=-50.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)

# Absolute humidity in grams within the permitted range (Initial_Humidity /
# Absolute_Humidity bounds); includes H = 0 so the zero-moisture edge case
# (Requirement 2.6) is exercised.
_ABS_GRAMS = st.floats(
    min_value=0.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Room volume within the permitted range (Room_Volume_Bounds).
_VOLUMES = st.floats(
    min_value=0.01,
    max_value=100000.0,
    allow_nan=False,
    allow_infinity=False,
)


# Feature: room-humidity-simulator, Property 4: Relative-humidity derivation
@given(
    abs_grams=_ABS_GRAMS,
    temperature_c=_TEMPERATURES,
    # Mix normal volumes with volume = 0.0 so that S(T, V) = 0 is reached and
    # the zero-capacity edge case (Requirement 2.7) is covered; H = 0 is
    # already covered by the _ABS_GRAMS strategy's lower bound.
    volume_m3=st.one_of(_VOLUMES, st.just(0.0)),
)
def test_property_4_relative_humidity_derivation(abs_grams, temperature_c, volume_m3):
    """Reported RH equals clamp(100*H/S, 0, 100) rounded to 1 dp; 0.0 at edges.

    Validates: Requirements 2.1, 2.2, 2.4, 2.6, 2.7
    """
    reported = relative_humidity_percent(abs_grams, temperature_c, volume_m3)

    capacity = saturation_content_grams(temperature_c, volume_m3)

    # Edge cases: no moisture (Req 2.6) or no capacity (Req 2.7) -> 0.0 percent.
    if abs_grams == 0.0 or capacity == 0.0:
        assert reported == 0.0
        return

    # General derivation: clamp(100 * H / S(T, V), 0, 100) rounded to 1 decimal,
    # computed against S at the current temperature (Reqs 2.1, 2.2, 2.4).
    expected_ratio = 100.0 * abs_grams / capacity
    expected = round(
        min(
            RELATIVE_HUMIDITY_MAX_PCT,
            max(RELATIVE_HUMIDITY_MIN_PCT, expected_ratio),
        ),
        1,
    )

    assert reported == expected

    # Reported value is always a valid percentage rounded to one decimal place.
    assert RELATIVE_HUMIDITY_MIN_PCT <= reported <= RELATIVE_HUMIDITY_MAX_PCT
    assert math.isclose(reported, round(reported, 1), rel_tol=0.0, abs_tol=0.0)
