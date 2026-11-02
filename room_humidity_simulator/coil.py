"""Coil domain component.

The Coil holds a surface temperature, a condensation capacity (g/s) and an
effectiveness, and implements a physically based moisture-removal model
(design: *Coil* component table and *Coil removal model*).

Physics
-------
Air moved across the coil is cooled toward the coil surface temperature
``T_c``. If the room dew point is above ``T_c`` vapor condenses, and an ideal
coil releases air saturated at ``T_c``, whose specific humidity measured back
at room temperature is ``q_out = 216.7 * es(T_c) / (273.15 + T_room)``
(:func:`~room_humidity_simulator.psychrometrics.coil_outlet_humidity_density`).
A real coil only brings a fraction ``effectiveness`` (the contact factor,
``1 - bypass factor``) of the air to that state, so with a flow ``F`` the
instantaneous removal rate is::

    r(q) = min(capacity, F * effectiveness * (q - q_out))   for q > q_out
    r(q) = 0                                                 otherwise

So removal grows with room humidity and with how far the coil is below the
room dew point, tapers smoothly to zero as the room approaches ``q_out``, and
is limited by the coil's condensation capacity.

The room is a well-mixed volume ``V`` that keeps recirculating through the
coil during the step, so its humidity obeys ``V dq/dt = -r(q)``. This is
solved exactly over ``dt`` (a linear, capacity-limited phase followed by an
exponential approach to ``q_out``), so even very long steps or flows that
move many room volumes per step never dry the room below ``q_out``.

Gating (Requirements 4.3-4.5): no removal when ``T_c >= T_room``, or when the
dew point of the moved air is ``<= T_c``. The coil only reads room state and
returns a mass; the simulator applies the decrement (Requirement 4.6).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

from room_humidity_simulator import psychrometrics

if TYPE_CHECKING:  # pragma: no cover - typing-only import, avoids a cycle.
    from room_humidity_simulator.room import Room

#: Default coil effectiveness (contact factor). Typical multi-row cooling
#: coils have a bypass factor of about 0.1-0.2.
DEFAULT_COIL_EFFECTIVENESS: float = 0.85


def removal_over_step(
    volume_m3: float,
    q_room_gm3: float,
    q_out_gm3: float,
    effective_flow_m3_per_s: float,
    capacity_g_per_s: float,
    dt: float,
) -> float:
    """Exact mass removed by ``V dq/dt = -min(cap, Fe * (q - q_out))`` over ``dt``.

    Args:
        volume_m3: Room volume ``V``.
        q_room_gm3: Room specific humidity at the start of removal.
        q_out_gm3: Coil outlet humidity ``q_out`` (the floor).
        effective_flow_m3_per_s: ``F * effectiveness``.
        capacity_g_per_s: Maximum condensation rate (may be ``inf``).
        dt: Step duration in seconds.

    Returns:
        Grams removed, in ``[0, V * (q_room - q_out)]`` and ``<= cap * dt``.
    """
    excess = q_room_gm3 - q_out_gm3
    if excess <= 0.0 or effective_flow_m3_per_s <= 0.0 or capacity_g_per_s <= 0.0 or dt <= 0.0:
        return 0.0
    removable = volume_m3 * excess  # grams above the coil floor
    k = effective_flow_m3_per_s / volume_m3  # 1/s, recirculation decay rate

    # Humidity excess above which the coil is capacity-limited.
    excess_limit = capacity_g_per_s / effective_flow_m3_per_s
    if excess > excess_limit:
        # Phase 1: constant removal at full capacity until excess_limit.
        t_limited = volume_m3 * (excess - excess_limit) / capacity_g_per_s
        if dt <= t_limited:
            return capacity_g_per_s * dt
        removed = volume_m3 * (excess - excess_limit)
        # Phase 2: exponential approach to q_out for the remaining time.
        removed += volume_m3 * excess_limit * -math.expm1(-k * (dt - t_limited))
    else:
        removed = removable * -math.expm1(-k * dt)
    return min(max(removed, 0.0), removable, capacity_g_per_s * dt)


class Coil:
    """Condensing coil that removes moisture from air moved across it.

    Validation (capacity ``>= 0``, effectiveness in ``[0, 1]``) is performed
    eagerly at the simulator boundary; the Coil trusts the values it is given.

    Args:
        surface_temperature_c: Coil surface temperature in degrees Celsius.
        condensation_capacity_g_per_s: Maximum condensation rate in grams per
            second (``>= 0.0``; ``inf`` means unlimited).
        effectiveness: Fraction of the moved air brought to the coil outlet
            state (contact factor, ``1 - bypass factor``), in ``[0, 1]``.
    """

    def __init__(
        self,
        surface_temperature_c: float,
        condensation_capacity_g_per_s: float,
        effectiveness: float = DEFAULT_COIL_EFFECTIVENESS,
    ) -> None:
        self._surface_temperature_c = surface_temperature_c
        self._condensation_capacity_g_per_s = condensation_capacity_g_per_s
        self._effectiveness = effectiveness
        # Frost on the coil (grams of ice). Only tracked when a compressor
        # with a frost model drives the coil (frost_halving_g is set).
        self.frost_g = 0.0
        self.frost_halving_g: Optional[float] = None

    @property
    def surface_temperature(self) -> float:
        """Coil surface temperature in degrees Celsius (Req 4.3-4.5)."""
        return self._surface_temperature_c

    def set_surface_temperature(self, temperature_c: float) -> None:
        """Set the coil surface temperature for the coming removal.

        Used by the simulator's Phase 2 hook, which drives the surface
        temperature from the Compressor state at the start of each step
        (Requirements 9.1, 9.2). Without a Compressor it is never called, so
        the configured constant is held (Requirement 12.1).

        Args:
            temperature_c: New coil surface temperature in degrees Celsius.
        """
        self._surface_temperature_c = temperature_c

    @property
    def condensation_capacity(self) -> float:
        """Maximum condensation rate in grams per second (Req 4.1)."""
        return self._condensation_capacity_g_per_s

    @property
    def effectiveness(self) -> float:
        """Configured coil effectiveness (contact factor) in ``[0, 1]``."""
        return self._effectiveness

    @property
    def active_effectiveness(self) -> float:
        """Effectiveness reduced by frost: ``eff / (1 + frost / halving)``.

        Ice insulates the fins and narrows the air passages, so a frosted coil
        exchanges less heat and moisture; with ``frost_halving_g`` grams of
        ice it works at half its clean effectiveness.
        """
        if not self.frost_halving_g or self.frost_g <= 0.0:
            return self._effectiveness
        return self._effectiveness / (1.0 + self.frost_g / self.frost_halving_g)

    def outlet_humidity(
        self, room_temperature_c: float, surface_temperature_c: Optional[float] = None
    ) -> float:
        """Lowest specific humidity (g/m^3) the coil can bring room air to.

        Uses the current surface temperature unless ``surface_temperature_c``
        is given.
        """
        t_coil = self._surface_temperature_c if surface_temperature_c is None else surface_temperature_c
        return psychrometrics.coil_outlet_humidity_density(t_coil, room_temperature_c)

    @staticmethod
    def condenses(
        specific_humidity: float, room_temperature_c: float, surface_temperature_c: float
    ) -> bool:
        """Gate of Requirements 4.3-4.5: coil colder than the room and below its dew point."""
        if surface_temperature_c >= room_temperature_c:
            return False
        # With no moisture the dew point is undefined and nothing condenses.
        try:
            dew_point_c = psychrometrics.dew_point(specific_humidity, room_temperature_c)
        except ValueError:
            return False
        return dew_point_c > surface_temperature_c

    def removal_rate(
        self,
        specific_humidity: float,
        room_temperature_c: float,
        flow_rate_m3_per_s: float,
        surface_temperature_c: float,
    ) -> float:
        """Instantaneous condensation rate in g/s at a given coil temperature.

        ``min(capacity, F * effectiveness * (q - q_out))`` when the gate is
        open, else ``0.0``.
        """
        if not self.condenses(specific_humidity, room_temperature_c, surface_temperature_c):
            return 0.0
        excess = specific_humidity - self.outlet_humidity(room_temperature_c, surface_temperature_c)
        rate = flow_rate_m3_per_s * self.active_effectiveness * excess
        return max(min(self._condensation_capacity_g_per_s, rate), 0.0)

    def removal_for(
        self,
        volume_m3: float,
        room_temperature_c: float,
        specific_humidity: float,
        flow_rate_m3_per_s: float,
        dt: float,
        surface_temperature_c: Optional[float] = None,
    ) -> float:
        """Grams removed over ``dt`` from a room in the given state.

        Exact solution of the recirculating coil model at a fixed coil
        temperature (the current one unless ``surface_temperature_c`` is
        given). Pure: reads only its arguments and the coil configuration.
        """
        t_coil = self._surface_temperature_c if surface_temperature_c is None else surface_temperature_c
        if dt <= 0.0 or not self.condenses(specific_humidity, room_temperature_c, t_coil):
            return 0.0
        return removal_over_step(
            volume_m3=volume_m3,
            q_room_gm3=specific_humidity,
            q_out_gm3=self.outlet_humidity(room_temperature_c, t_coil),
            effective_flow_m3_per_s=flow_rate_m3_per_s * self.active_effectiveness,
            capacity_g_per_s=self._condensation_capacity_g_per_s,
            dt=dt,
        )

    def removal(self, moved_air_volume: float, room: "Room", dt: float) -> float:
        """Mass of moisture removed this step, in grams (always ``>= 0``).

        Args:
            moved_air_volume: Volume of air the fan moved across the coil this
                step, in cubic meters (``flow_rate * dt``).
            room: The room whose state is read. The coil does not mutate it.
            dt: Time-step duration in seconds.

        Returns:
            Grams removed, bounded by ``capacity * dt`` and by the moisture
            above the coil outlet humidity (``V * (q - q_out)``).
        """
        if dt <= 0.0:
            return 0.0
        return self.removal_for(
            room.volume,
            room.temperature,
            room.specific_humidity(),
            moved_air_volume / dt,
            dt,
        )
