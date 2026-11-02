"""Example-based unit tests for the Room domain component (Task 3.2).

These complement the Room property tests by pinning down concrete examples of
the getter and clamping behavior described in the design's *Room* component
table:

- A freshly configured Room exposes its configured volume and temperature
  within their permitted ranges, and reports absolute and relative humidity
  within their reporting ranges (Requirements 1.7, 1.8, 1.9).
- ``set_absolute_humidity`` clamps candidate values below ``0`` up to ``0`` and
  values above the temperature/volume saturation content down to that
  saturation content (Requirements 1.7 via the clamp invariant that keeps the
  reported value in range).

The tests exercise the real :class:`~room_humidity_simulator.room.Room` against
the real :mod:`room_humidity_simulator.psychrometrics` physics; no mocking is
used so the clamp boundary is validated against the actual saturation content.
"""

from __future__ import annotations

import pytest

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import (
    ABSOLUTE_HUMIDITY_REPORTED_MAX_G,
    RELATIVE_HUMIDITY_MAX_PCT,
    RELATIVE_HUMIDITY_MIN_PCT,
    ROOM_TEMPERATURE_MAX_C,
    ROOM_TEMPERATURE_MIN_C,
)
from room_humidity_simulator.room import Room


# --- Getters expose configured values within range (Requirements 1.7-1.9). ---


def test_fresh_room_exposes_configured_volume_and_temperature():
    """Volume and temperature getters return the configured values (Req 1.9)."""
    room = Room(volume_m3=50.0, temperature_c=20.0, initial_humidity_grams=100.0)

    assert room.volume == 50.0
    assert room.temperature == 20.0


def test_fresh_room_temperature_within_permitted_range():
    """Reported temperature stays within the [-50, 100] deg C range (Req 1.9)."""
    for temperature_c in (ROOM_TEMPERATURE_MIN_C, 0.0, 20.0, ROOM_TEMPERATURE_MAX_C):
        room = Room(
            volume_m3=25.0,
            temperature_c=temperature_c,
            initial_humidity_grams=10.0,
        )
        assert ROOM_TEMPERATURE_MIN_C <= room.temperature <= ROOM_TEMPERATURE_MAX_C


def test_fresh_room_absolute_humidity_within_reported_range():
    """Reported absolute humidity stays within [0.0, 999.9] grams (Req 1.7)."""
    # Choose a warm, large room so the saturation ceiling is far above the
    # reporting cap, forcing the [0.0, 999.9] cap (not the saturation clamp)
    # to be the binding constraint on the reported value.
    room = Room(volume_m3=100.0, temperature_c=90.0, initial_humidity_grams=5000.0)

    assert 0.0 <= room.absolute_humidity_grams <= ABSOLUTE_HUMIDITY_REPORTED_MAX_G
    assert room.absolute_humidity_grams == ABSOLUTE_HUMIDITY_REPORTED_MAX_G


def test_fresh_room_absolute_humidity_reports_configured_value_when_in_range():
    """A modest humidity below both ceilings is reported unchanged (Req 1.7)."""
    room = Room(volume_m3=50.0, temperature_c=20.0, initial_humidity_grams=100.0)

    # 100 g in a 50 m^3 room at 20 deg C is well below saturation and below the
    # 999.9 g reporting cap, so it is reported at full value.
    assert room.absolute_humidity_grams == pytest.approx(100.0)


def test_fresh_room_relative_humidity_within_percent_range():
    """Reported relative humidity stays within [0.0, 100.0] percent (Req 1.8)."""
    room = Room(volume_m3=50.0, temperature_c=20.0, initial_humidity_grams=100.0)

    rh = room.relative_humidity()
    assert RELATIVE_HUMIDITY_MIN_PCT <= rh <= RELATIVE_HUMIDITY_MAX_PCT


def test_zero_humidity_room_reports_zero_relative_humidity():
    """With no moisture, relative humidity is reported as 0.0 percent (Req 1.8)."""
    room = Room(volume_m3=50.0, temperature_c=20.0, initial_humidity_grams=0.0)

    assert room.absolute_humidity_grams == 0.0
    assert room.relative_humidity() == 0.0


def test_saturated_room_reports_one_hundred_percent_relative_humidity():
    """A room clamped at saturation reports 100.0 percent RH (Req 1.8)."""
    volume_m3, temperature_c = 50.0, 20.0
    saturation_g = psychrometrics.saturation_content_grams(temperature_c, volume_m3)
    # Request far above saturation; construction clamps to the saturation ceiling.
    room = Room(
        volume_m3=volume_m3,
        temperature_c=temperature_c,
        initial_humidity_grams=saturation_g * 10.0,
    )

    assert room.relative_humidity() == pytest.approx(100.0)


# --- set_absolute_humidity clamping (clamp invariant behind Req 1.7). ---


def test_set_absolute_humidity_clamps_negative_to_zero():
    """A negative candidate humidity is clamped up to 0.0 grams."""
    room = Room(volume_m3=50.0, temperature_c=20.0, initial_humidity_grams=100.0)

    room.set_absolute_humidity(-25.0)

    assert room.absolute_humidity_grams == 0.0
    assert room.specific_humidity() == 0.0
    assert room.relative_humidity() == 0.0


def test_set_absolute_humidity_clamps_above_saturation_down_to_saturation():
    """A candidate above saturation is clamped down to the saturation content."""
    volume_m3, temperature_c = 50.0, 20.0
    saturation_g = psychrometrics.saturation_content_grams(temperature_c, volume_m3)
    room = Room(
        volume_m3=volume_m3,
        temperature_c=temperature_c,
        initial_humidity_grams=10.0,
    )

    room.set_absolute_humidity(saturation_g * 5.0)

    # specific_humidity() exposes the full-precision internal value, which the
    # setter clamps to exactly the saturation content.
    stored_grams = room.specific_humidity() * volume_m3
    assert stored_grams == pytest.approx(saturation_g)
    assert room.relative_humidity() == pytest.approx(100.0)


def test_set_absolute_humidity_keeps_in_range_value_unchanged():
    """A candidate within [0, saturation] is stored unchanged."""
    volume_m3, temperature_c = 50.0, 20.0
    saturation_g = psychrometrics.saturation_content_grams(temperature_c, volume_m3)
    in_range = saturation_g / 2.0
    room = Room(
        volume_m3=volume_m3,
        temperature_c=temperature_c,
        initial_humidity_grams=0.0,
    )

    room.set_absolute_humidity(in_range)

    stored_grams = room.specific_humidity() * volume_m3
    assert stored_grams == pytest.approx(in_range)
