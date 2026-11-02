"""Room domain component.

The Room is a state container holding volume, temperature, and full-precision
absolute humidity, with derived accessors for specific and relative humidity.
It applies no physics of its own beyond delegating to
:mod:`room_humidity_simulator.psychrometrics`; it is a state container with
derived accessors (design: *Room* component table).

Key behaviors (design: *Room* table and *Units and rounding*):

- ``volume`` and ``temperature`` are fixed configuration reported through
  getters within their permitted ranges (Requirements 1.9).
- ``absolute_humidity_grams`` is retained at full precision internally and
  only rounded for reporting, so repeated steps do not accumulate rounding
  bias. The reported value is constrained to ``[0.0, 999.9]`` grams
  (Requirements 1.7, 2.5).
- ``specific_humidity()`` is ``absolute_humidity_grams / volume``
  (Requirements 5.2, 4.3).
- ``relative_humidity()`` is derived through ``Psychrometrics`` and reported
  in ``[0.0, 100.0]`` percent (Requirements 1.8, 2.1, 2.6, 2.7).
- ``set_absolute_humidity(g)`` is the internal setter used by the simulator;
  it always clamps to ``[0, saturation_content_grams(T, V)]`` (Requirements
  4.7, 5.9, 7.4).

Implemented in Task 3.1.
"""

from __future__ import annotations

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import ABSOLUTE_HUMIDITY_REPORTED_MAX_G


class Room:
    """Mutable moisture state for a single well-mixed room.

    The Room stores its fixed physical configuration (``volume`` and
    ``temperature``) and its mutable absolute-humidity state at full
    precision. Absolute humidity is kept within the physical invariant
    ``0.0 <= absolute_humidity_grams <= saturation_content_grams(T, V)`` by the
    :meth:`set_absolute_humidity` setter.

    Args:
        volume_m3: Fixed room volume in cubic meters.
        temperature_c: Room temperature in degrees Celsius.
        initial_humidity_grams: Initial absolute humidity (mass of water
            vapor) in grams. It is clamped to
            ``[0, saturation_content_grams(T, V)]`` on construction so the Room
            never starts above saturation or below zero (Requirements 2.3,
            5.9, 4.7).
    """

    def __init__(
        self,
        volume_m3: float,
        temperature_c: float,
        initial_humidity_grams: float,
    ) -> None:
        self._volume_m3 = volume_m3
        self._temperature_c = temperature_c
        # Full-precision absolute humidity; clamped to the physical range.
        self._absolute_humidity_grams: float = 0.0
        self.set_absolute_humidity(initial_humidity_grams)

    @property
    def volume(self) -> float:
        """Room volume in cubic meters (Requirement 1.9)."""
        return self._volume_m3

    @property
    def temperature(self) -> float:
        """Room temperature in degrees Celsius (Requirement 1.9)."""
        return self._temperature_c

    @property
    def absolute_humidity_grams(self) -> float:
        """Reported absolute humidity in grams, within ``[0.0, 999.9]``.

        The value is retained internally at full precision; this accessor
        reports it capped to the ``[0.0, 999.9]`` grams reporting range
        (design: *Units and rounding*; Requirements 1.7, 2.5). The internal
        full-precision value is already ``>= 0.0`` by the invariant maintained
        in :meth:`set_absolute_humidity`.

        Returns:
            Absolute humidity in grams within ``[0.0, 999.9]``.
        """
        return min(self._absolute_humidity_grams, ABSOLUTE_HUMIDITY_REPORTED_MAX_G)

    @property
    def exact_absolute_humidity_grams(self) -> float:
        """Full-precision absolute humidity in grams, not capped for reporting.

        Used by the simulator's mass balance so repeated steps do not
        accumulate rounding or reporting-cap bias (design: *Units and
        rounding*).
        """
        return self._absolute_humidity_grams

    def specific_humidity(self) -> float:
        """Specific humidity in g/m^3 as ``absolute_humidity_grams / volume``.

        Uses the full-precision internal absolute humidity divided by the room
        volume (design: *Room* table; Requirements 5.2, 4.3).

        Returns:
            Specific humidity in grams per cubic meter (g/m^3).
        """
        return self._absolute_humidity_grams / self._volume_m3

    def relative_humidity(self) -> float:
        """Relative humidity in percent within ``[0.0, 100.0]``.

        Delegates to
        :func:`room_humidity_simulator.psychrometrics.relative_humidity_percent`,
        which derives the value from the full-precision absolute humidity, the
        room temperature, and the room volume, rounds it to one decimal place,
        and caps it to ``[0.0, 100.0]`` -- reporting ``0.0`` when there is no
        moisture or no capacity (design: *Room* table; Requirements 1.8, 2.1,
        2.6, 2.7).

        Returns:
            Relative humidity as a percentage in ``[0.0, 100.0]``.
        """
        return psychrometrics.relative_humidity_percent(
            self._absolute_humidity_grams, self._temperature_c, self._volume_m3
        )

    def set_absolute_humidity(self, grams: float) -> None:
        """Set the absolute humidity, clamped to ``[0, saturation]``.

        This is the internal setter used by the simulator. It always clamps
        the supplied value to ``[0, saturation_content_grams(T, V)]`` so the
        Room never holds a negative mass (Requirements 4.7, 7.4) nor exceeds
        the temperature-dependent saturation capacity (Requirements 2.3, 5.9).
        The clamped value is retained at full precision.

        Args:
            grams: Candidate absolute humidity in grams. Values below ``0`` are
                clamped to ``0``; values above the saturation content at the
                room temperature and volume are clamped to that saturation
                content.
        """
        saturation_grams = psychrometrics.saturation_content_grams(
            self._temperature_c, self._volume_m3
        )
        clamped = min(max(grams, 0.0), saturation_grams)
        self._absolute_humidity_grams = clamped
