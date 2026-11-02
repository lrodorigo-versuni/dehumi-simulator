"""Property-based tests for the built-in Disturbance implementations.

Implements the design's Property 5 for the two built-in disturbance types
(``Person_Disturbance`` and ``Boiling_Water_Disturbance``) using Hypothesis.
The shared ``simulator`` Hypothesis profile (registered in ``conftest.py``)
runs a minimum of 100 examples per property test, satisfying the design
requirement of at least 100 iterations.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from room_humidity_simulator.disturbances import (
    Boiling_Water_Disturbance,
    Person_Disturbance,
)

# Moisture rate within the permitted range [0.0, 1000.0] grams per second
# (Moisture_Rate bounds). Includes both endpoints so the 0.0 and 1000.0 edges
# are reachable.
_MOISTURE_RATES = st.floats(
    min_value=0.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Time-step durations with dt > 0. The permitted simulator time-step range is
# (0.0, 3600.0]; the lower exclusive bound models the design's dt > 0 condition
# for a disturbance contribution.
_DURATIONS = st.floats(
    min_value=0.0,
    max_value=3600.0,
    exclude_min=True,
    allow_nan=False,
    allow_infinity=False,
)

# The two built-in disturbance types share the moisture_rate * dt contribution
# formula (Requirements 5.1, 5.2); exercise both in the same property.
_DISTURBANCE_TYPES = st.sampled_from(
    [Person_Disturbance, Boiling_Water_Disturbance]
)


# Feature: room-humidity-simulator, Property 5: Disturbance contribution is rate times duration
@given(
    disturbance_type=_DISTURBANCE_TYPES,
    moisture_rate=_MOISTURE_RATES,
    dt=_DURATIONS,
)
def test_property_5_disturbance_contribution_is_rate_times_duration(
    disturbance_type, moisture_rate, dt
):
    """A built-in disturbance contributes exactly moisture_rate * dt grams.

    Validates: Requirements 5.1, 5.2, 6.2
    """
    disturbance = disturbance_type(moisture_rate)

    contributed = disturbance.contribution(dt)

    assert contributed == moisture_rate * dt
