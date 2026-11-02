"""Fan domain component.

The Fan holds a volumetric flow rate and computes the volume of air moved
across the coil during a time step (design: *Fan* component table).

Key behaviors (design: *Fan* table):

- ``flow_rate`` is the fixed volumetric flow rate in m^3/s, reported through a
  getter (Requirement 3.7).
- ``moved_air_volume(dt)`` is ``flow_rate * dt``, returning ``0.0`` when the
  flow rate is ``0.0`` (Requirements 3.5, 3.6).

Implemented in Task 4.1.
"""

from __future__ import annotations


class Fan:
    """Moves air across the coil at a configured volumetric flow rate.

    The Fan is a small state container: it stores its volumetric flow rate and
    computes the volume of air it moves across the coil over a time-step
    duration. Flow-rate validation is performed eagerly at the simulator
    boundary (Task 8); the Fan itself trusts the value it is given.

    Args:
        flow_rate_m3_per_s: Volumetric flow rate in cubic meters per second.
    """

    def __init__(self, flow_rate_m3_per_s: float) -> None:
        self._flow_rate_m3_per_s = flow_rate_m3_per_s

    @property
    def flow_rate(self) -> float:
        """Current volumetric flow rate in cubic meters per second (Req 3.7)."""
        return self._flow_rate_m3_per_s

    def moved_air_volume(self, dt: float) -> float:
        """Volume of air moved across the coil over ``dt`` seconds.

        Computes ``flow_rate * dt`` (design: *Fan* table; Requirement 3.5).
        When the flow rate is ``0.0`` the Fan moves ``0.0`` cubic meters of air
        regardless of ``dt`` (Requirement 3.6).

        Args:
            dt: Time-step duration in seconds.

        Returns:
            The volume of air moved across the coil in cubic meters.
        """
        if self._flow_rate_m3_per_s == 0.0:
            return 0.0
        return self._flow_rate_m3_per_s * dt
