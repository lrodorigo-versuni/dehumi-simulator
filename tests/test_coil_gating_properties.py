"""Property-based test for the Coil dew-point and temperature gating.

Implements the design's Property 8 ("Coil dew-point and temperature gating")
using Hypothesis, exercising the real :class:`~room_humidity_simulator.coil.Coil`
against a real :class:`~room_humidity_simulator.room.Room` and the stateless
:mod:`room_humidity_simulator.psychrometrics` physics. The shared ``simulator``
Hypothesis profile (registered in ``conftest.py``) runs a minimum of 100
examples per property test, satisfying the design requirement of at least 100
iterations.

Property 8 (design):
    For any room state, the coil removes 0.0 grams whenever the coil surface
    temperature is >= the room temperature, or the dew point of the moved air
    is <= the coil surface temperature; otherwise (dew point strictly above
    surface temperature, with positive capacity and available moisture) it
    removes a positive mass.

Validates: Requirements 4.3, 4.4, 4.5
"""

from __future__ import annotations

import math

from hypothesis import assume, given
from hypothesis import strategies as st

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.coil import Coil
from room_humidity_simulator.room import Room


# Room temperatures within the permitted room range [-50, 100] deg C
# (Room_Temperature_Bounds). Kept well inside the bounds so the constructed
# saturation capacity and dew points stay finite and well-conditioned.
_ROOM_TEMPERATURES = st.floats(
    min_value=-40.0,
    max_value=90.0,
    allow_nan=False,
    allow_infinity=False,
)

# Room volumes within the permitted range [0.01, 100000] m^3 (Room_Volume_Bounds).
_ROOM_VOLUMES = st.floats(
    min_value=0.01,
    max_value=100000.0,
    allow_nan=False,
    allow_infinity=False,
)

# A surface-temperature offset relative to the room temperature, spanning both
# directions so the generators include coil surface temperatures strictly below
# and at/above the room temperature (design: generators include "surface
# temperature above/below room temperature").
_SURFACE_OFFSETS = st.floats(
    min_value=-60.0,
    max_value=60.0,
    allow_nan=False,
    allow_infinity=False,
)

# Positive moved-air volumes and condensation capacities so the "otherwise"
# branch of Property 8 has both available moisture and capacity to remove it.
_MOVED_AIR_VOLUMES = st.floats(
    min_value=1e-3,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
_CAPACITIES = st.floats(
    min_value=1e-3,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# A fractional fill of the room's saturation capacity, used to drive the room's
# absolute humidity (and hence specific humidity and dew point) across the full
# range from essentially dry up to saturated.
_FILL_FRACTIONS = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

# A fixed time-step duration within (0.0, 3600]; the gating decision does not
# depend on dt, so a constant representative value suffices.
_DT = 1.0


def _specific_humidity_for_dew_point(dew_point_c: float, temperature_c: float) -> float:
    """Specific humidity (g/m^3) whose dew point equals ``dew_point_c``.

    Inverts :func:`room_humidity_simulator.psychrometrics.dew_point`: the
    actual vapor pressure corresponding to a dew point is
    ``e = 6.112 * exp(17.62 * Td / (243.12 + Td))`` and the specific humidity
    recovering that pressure at room temperature ``T`` is
    ``q = e * 216.7 / (273.15 + T)``.
    """
    gamma = (
        psychrometrics.MAGNUS_B
        * dew_point_c
        / (psychrometrics.MAGNUS_C_C + dew_point_c)
    )
    vapor_pressure_hpa = psychrometrics.MAGNUS_A_HPA * math.exp(gamma)
    return (
        vapor_pressure_hpa
        * psychrometrics.IDEAL_GAS_CONSTANT
        / (psychrometrics.KELVIN_OFFSET_C + temperature_c)
    )


def _make_room(volume_m3: float, temperature_c: float, specific_humidity_gm3: float) -> Room:
    """Build a Room at the target specific humidity (clamped to saturation)."""
    room = Room(volume_m3, temperature_c, 0.0)
    room.set_absolute_humidity(specific_humidity_gm3 * volume_m3)
    return room


# Feature: room-humidity-simulator, Property 8: Coil dew-point and temperature gating
@given(
    temperature_c=_ROOM_TEMPERATURES,
    volume_m3=_ROOM_VOLUMES,
    surface_offset=_SURFACE_OFFSETS,
    fill_fraction=_FILL_FRACTIONS,
    moved_air_volume=_MOVED_AIR_VOLUMES,
    capacity=_CAPACITIES,
)
def test_property_8_coil_dew_point_and_temperature_gating(
    temperature_c,
    volume_m3,
    surface_offset,
    fill_fraction,
    moved_air_volume,
    capacity,
):
    """Coil removal is gated by surface vs room temperature and by dew point.

    The coil removes 0.0 g whenever the surface temperature is >= the room
    temperature (Req 4.5) or the dew point of the moved air is <= the surface
    temperature (Req 4.4); otherwise, with positive capacity and available
    moisture, it removes a positive mass (Req 4.3).

    Validates: Requirements 4.3, 4.4, 4.5
    """
    surface_temperature_c = temperature_c + surface_offset

    # Room filled to a fraction of its saturation capacity. Using the clamped
    # setter keeps the absolute humidity physical; read back the actual
    # specific humidity so the dew point comparison uses the true room state.
    saturation_grams = psychrometrics.saturation_content_grams(
        temperature_c, volume_m3
    )
    room = Room(temperature_c=temperature_c, volume_m3=volume_m3, initial_humidity_grams=0.0)
    room.set_absolute_humidity(fill_fraction * saturation_grams)

    coil = Coil(surface_temperature_c, capacity)
    removed = coil.removal(moved_air_volume, room, _DT)

    # Removal is always non-negative (defensive floor in the algorithm).
    assert removed >= 0.0

    specific_humidity = room.specific_humidity()

    # Case A: surface temperature at or above room temperature -> 0.0 (Req 4.5).
    if surface_temperature_c >= temperature_c:
        assert removed == 0.0
        return

    # Below this point the surface is strictly colder than the room. The gate
    # now depends on the dew point of the moved air. With no moisture the dew
    # point is undefined and the coil removes nothing.
    if specific_humidity <= 0.0:
        assert removed == 0.0
        return

    dew_point_c = psychrometrics.dew_point(specific_humidity, temperature_c)

    if dew_point_c <= surface_temperature_c:
        # Dew point at or below the surface -> no condensation (Req 4.4).
        assert removed == 0.0
    elif dew_point_c > surface_temperature_c + 1e-6:
        # Dew point strictly above the surface, with positive capacity and
        # positive available moisture -> a positive mass is removed (Req 4.3).
        # Removal is proportional to the excess above the coil outlet
        # humidity, so within ~1e-6 deg C of the boundary it may round to 0.
        assert removed > 0.0


# Feature: room-humidity-simulator, Property 8: Coil dew-point and temperature gating
@given(
    temperature_c=st.floats(
        min_value=0.0, max_value=90.0, allow_nan=False, allow_infinity=False
    ),
    volume_m3=_ROOM_VOLUMES,
    # Target dew point strictly below the room temperature so a physical
    # specific humidity exists; the coil surface is pinned exactly to it.
    dew_point_offset=st.floats(
        min_value=0.5, max_value=40.0, allow_nan=False, allow_infinity=False
    ),
    capacity=_CAPACITIES,
    moved_air_volume=_MOVED_AIR_VOLUMES,
)
def test_property_8_dew_point_exactly_equal_to_surface_removes_nothing(
    temperature_c, volume_m3, dew_point_offset, capacity, moved_air_volume
):
    """When the dew point exactly equals the surface temperature, removal is 0.0.

    Exercises the boundary folded into the Property 8 generators ("dew point
    exactly equal to surface temperature"): the gate uses ``<=`` so an exact
    match removes 0.0 grams (Req 4.4).

    Validates: Requirements 4.3, 4.4, 4.5
    """
    # Pick a dew point strictly below the room temperature, then build a room
    # whose specific humidity yields exactly that dew point.
    target_dew_point_c = temperature_c - dew_point_offset
    specific_humidity = _specific_humidity_for_dew_point(
        target_dew_point_c, temperature_c
    )
    room = _make_room(volume_m3, temperature_c, specific_humidity)

    # The room clamps to saturation; only proceed where the stored specific
    # humidity still reproduces the target dew point (true whenever it is below
    # saturation, which holds here because the dew point is below the room
    # temperature).
    assume(room.specific_humidity() > 0.0)
    achieved_dew_point_c = psychrometrics.dew_point(
        room.specific_humidity(), temperature_c
    )

    # Pin the coil surface temperature exactly at the achieved dew point.
    coil = Coil(achieved_dew_point_c, capacity)

    removed = coil.removal(moved_air_volume, room, _DT)

    # dew_point <= surface_temperature holds by equality -> no removal (Req 4.4).
    assert removed == 0.0
