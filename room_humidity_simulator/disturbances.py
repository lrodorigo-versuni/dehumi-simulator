"""Disturbance interface and built-in implementations.

Defines the ``Disturbance`` interface (``is_active()``, ``contribution(dt)``)
and the two built-in types ``Person_Disturbance`` and
``Boiling_Water_Disturbance`` (design: *Disturbance interface and
implementations* class diagram and table).

A ``Disturbance`` is a source of moisture added to the Room, external to the
Coil and Fan (for example a person or boiling water). The ``Simulator``
depends only on this interface, so a caller-supplied custom disturbance is
incorporated without changes to the Coil or Fan (Requirements 6.1, 6.3).

For the two built-in types, the moisture mass contributed over a time-step
duration ``dt > 0`` is ``moisture_rate * dt`` grams (Requirements 5.1, 5.2,
6.2). Each stores its ``moisture_rate`` in grams per second.

``Open_Window_Disturbance`` is different: it exchanges air with outdoors,
so it can add or remove moisture. It exposes ``exchange_grams(room, dt)``,
which the simulator uses (signed) instead of ``contribution(dt)``.

Implemented in Task 7.1; open window in Task 26.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import is_real_number
from room_humidity_simulator.results import ConfigError, ValidationResult

if TYPE_CHECKING:  # pragma: no cover - typing-only import, avoids a cycle.
    from room_humidity_simulator.room import Room


class Disturbance(ABC):
    """Common interface for moisture-injecting disturbances.

    A Disturbance participates in the simulation step by reporting whether it
    is active and, when asked, the moisture mass in grams it contributes over
    a time-step duration. The ``Simulator`` depends only on this abstract
    interface, keeping it open to new disturbance types without changes to the
    Coil, Fan, or orchestration internals (Requirements 6.1, 6.3).
    """

    @abstractmethod
    def is_active(self) -> bool:
        """Whether this disturbance participates in the current step.

        Returns:
            ``True`` when the disturbance should contribute moisture during the
            current time step, ``False`` otherwise (Requirements 5.3, 5.4).
        """
        raise NotImplementedError

    @abstractmethod
    def contribution(self, dt: float) -> float:
        """Moisture mass in grams contributed over ``dt`` seconds.

        Args:
            dt: Time-step duration in seconds. The simulator requests a
                contribution only for ``dt > 0`` (Requirement 6.2).

        Returns:
            The moisture mass in grams this disturbance adds over ``dt``
            (Requirement 6.2).
        """
        raise NotImplementedError


class Person_Disturbance(Disturbance):
    """A human occupant that adds moisture at a fixed rate while present.

    The contribution over a time-step duration ``dt > 0`` is
    ``moisture_rate * dt`` grams (Requirements 5.1, 6.2).

    Args:
        moisture_rate: Mass of water the person adds per unit time, in grams
            per second. Validation of its permitted range ``[0.0, 1000.0]`` is
            performed at the simulator boundary when the disturbance is added
            (Requirement 5.7); this constructor stores the value as given.
        active: Whether the disturbance starts active. Defaults to ``True``.
    """

    def __init__(self, moisture_rate: float, active: bool = True) -> None:
        self.moisture_rate = moisture_rate
        self._active = active

    def is_active(self) -> bool:
        """Whether this person disturbance participates in the current step."""
        return self._active

    def contribution(self, dt: float) -> float:
        """Return ``moisture_rate * dt`` grams for ``dt > 0`` (Req 5.1, 6.2)."""
        return self.moisture_rate * dt


class Boiling_Water_Disturbance(Disturbance):
    """Boiling water that adds moisture vapor at a fixed rate while active.

    The contribution over a time-step duration ``dt > 0`` is
    ``moisture_rate * dt`` grams (Requirements 5.2, 6.2).

    Args:
        moisture_rate: Mass of water vapor added per unit time, in grams per
            second. Validation of its permitted range ``[0.0, 1000.0]`` is
            performed at the simulator boundary when the disturbance is added
            (Requirement 5.7); this constructor stores the value as given.
        active: Whether the disturbance starts active. Defaults to ``True``.
    """

    def __init__(self, moisture_rate: float, active: bool = True) -> None:
        self.moisture_rate = moisture_rate
        self._active = active

    def is_active(self) -> bool:
        """Whether this boiling-water disturbance participates in the step."""
        return self._active

    def contribution(self, dt: float) -> float:
        """Return ``moisture_rate * dt`` grams for ``dt > 0`` (Req 5.2, 6.2)."""
        return self.moisture_rate * dt


class Open_Window_Disturbance(Disturbance):
    """An open window exchanging room air with outdoor air, with wind gusts.

    Unlike a person or a kettle, a window can add *or remove* moisture: the
    room moves toward the outdoor humidity. Over a step of ``dt`` seconds
    with an airflow ``Q`` through the window the well-mixed room exchanges
    exactly::

        grams = V * (q_out - q_room) * (1 - exp(-Q * dt / V))

    where ``q_out`` is the outdoor vapor expressed at room temperature (the
    vapor pressure ``RH_out * es(T_out)`` is conserved when outdoor air warms
    or cools to room temperature). The room never overshoots the outdoor
    humidity.

    **Gusts (the noise).** The airflow through a window follows the wind. It
    is modeled as ``Q = mean * max(0, 1 + x)`` where ``x`` is an
    Ornstein-Uhlenbeck process with standard deviation ``gustiness`` and
    correlation time ``gust_time_s``: random but smooth gusts, reproducible
    from ``seed``. The random sequence advances once per step and does not
    depend on the room state, so two runs with the same seed and time step
    see exactly the same wind (fair A/B comparisons).

    The room temperature stays constant (as in the rest of the model): only
    moisture is exchanged.

    Args:
        airflow_m3_per_h: Mean airflow through the window (typical: tilted
            30-100 m^3/h, wide open 200-1000+ m^3/h depending on wind).
        outdoor_temperature_c: Outdoor air temperature.
        outdoor_rh_pct: Outdoor relative humidity in percent.
        gustiness: Relative standard deviation of the airflow (0 = steady).
        gust_time_s: Correlation time of the gusts in seconds.
        seed: Seed of the gust sequence.
        active: Whether the window starts open.
    """

    def __init__(
        self,
        airflow_m3_per_h: float,
        outdoor_temperature_c: float,
        outdoor_rh_pct: float,
        gustiness: float = 0.0,
        gust_time_s: float = 60.0,
        seed: int = 1,
        active: bool = True,
    ) -> None:
        self.airflow_m3_per_h = airflow_m3_per_h
        self.outdoor_temperature_c = outdoor_temperature_c
        self.outdoor_rh_pct = outdoor_rh_pct
        self.gustiness = gustiness
        self.gust_time_s = gust_time_s
        self.seed = seed
        self._active = active
        self._rng = random.Random(seed)
        self._x = 0.0
        self.last_airflow_m3_per_h = 0.0

    def validate(self) -> "ValidationResult[None]":
        """Check the window parameters (used by ``Simulator.add_disturbance``)."""
        checks = (
            ("airflowM3PerH", self.airflow_m3_per_h, 0.0, 10000.0, "window airflow out of range"),
            ("outdoorTemperatureC", self.outdoor_temperature_c, -50.0, 60.0, "outdoor temperature out of range"),
            ("outdoorRhPct", self.outdoor_rh_pct, 0.0, 100.0, "outdoor humidity out of range"),
            ("gustiness", self.gustiness, 0.0, 2.0, "gustiness out of range"),
        )
        for field, value, lo, hi, message in checks:
            if not is_real_number(value) or not lo <= value <= hi:
                return ValidationResult.fail(ConfigError(field, message, message.replace(" ", "_")))
        if not is_real_number(self.gust_time_s) or not self.gust_time_s > 0.0:
            return ValidationResult.fail(
                ConfigError("gustTimeS", "gust time must be greater than 0", "gust_time_not_positive")
            )
        return ValidationResult.ok()

    def is_active(self) -> bool:
        """Whether the window is open."""
        return self._active

    def contribution(self, dt: float) -> float:
        """Not used: the window's exchange depends on the room (see ``exchange_grams``)."""
        return 0.0

    def outdoor_humidity_gm3(self, room_temperature_c: float) -> float:
        """Outdoor vapor expressed as g/m^3 of room air at room temperature."""
        vapor_pressure = self.outdoor_rh_pct / 100.0 * psychrometrics.saturation_vapor_pressure(
            self.outdoor_temperature_c
        )
        return psychrometrics.IDEAL_GAS_CONSTANT * vapor_pressure / (
            psychrometrics.KELVIN_OFFSET_C + room_temperature_c
        )

    def next_airflow_m3_per_h(self, dt: float) -> float:
        """Advance the gust process by ``dt`` and return this step's airflow."""
        if self.gustiness > 0.0:
            a = math.exp(-dt / self.gust_time_s)
            self._x = a * self._x + self.gustiness * math.sqrt(1.0 - a * a) * self._rng.gauss(0.0, 1.0)
        self.last_airflow_m3_per_h = self.airflow_m3_per_h * max(0.0, 1.0 + self._x)
        return self.last_airflow_m3_per_h

    def exchange_grams(self, room: "Room", dt: float) -> float:
        """Signed grams entering the room this step (negative = drying)."""
        airflow = self.next_airflow_m3_per_h(dt) / 3600.0
        q_out = self.outdoor_humidity_gm3(room.temperature)
        q_room = room.specific_humidity()
        return room.volume * (q_out - q_room) * -math.expm1(-airflow * dt / room.volume)
