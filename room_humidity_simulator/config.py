"""Configuration data models and permitted-range constants.

These dataclasses mirror the "Configuration inputs" section of the design.
They are plain data carriers: construction does not validate. Validation is
performed eagerly at the simulator boundary (Task 8) using the range
constants defined here, returning a structured
:class:`~room_humidity_simulator.results.ValidationResult` rather than raising.

Permitted ranges (from the design Data Models and requirements):

- Room volume:            ``[0.01, 100000]`` m^3        (Req 1.1-1.3)
- Room temperature:       ``[-50, 100]`` deg C          (Req 1.1, 1.4)
- Initial humidity:       ``[0.0, 1000.0]`` g/m^3        (Req 1.1, 1.5, 1.6)
- Fan flow rate:          ``[0.0, 10.0]`` m^3/s          (Req 3.1-3.4)
- Coil capacity:          ``>= 0.0`` g/s                 (Req 4.8)
- Coil effectiveness:     ``[0.0, 1.0]``
- Time-step duration:     ``(0.0, 3600.0]`` s            (Req 7.3)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --- Room volume bounds (m^3), inclusive. Requirements 1.1-1.3. ---
ROOM_VOLUME_MIN_M3: float = 0.01
ROOM_VOLUME_MAX_M3: float = 100000.0

# --- Room temperature bounds (deg C), inclusive. Requirements 1.1, 1.4. ---
ROOM_TEMPERATURE_MIN_C: float = -50.0
ROOM_TEMPERATURE_MAX_C: float = 100.0

# --- Initial absolute humidity bounds (g/m^3), inclusive. Req 1.1, 1.5, 1.6. ---
INITIAL_HUMIDITY_MIN_GM3: float = 0.0
INITIAL_HUMIDITY_MAX_GM3: float = 1000.0

# --- Fan volumetric flow-rate bounds (m^3/s), inclusive. Requirements 3.1-3.4. ---
FLOW_RATE_MIN_M3_PER_S: float = 0.0
FLOW_RATE_MAX_M3_PER_S: float = 10.0

# --- Coil condensation-capacity minimum (g/s). Requirement 4.8. ---
CONDENSATION_CAPACITY_MIN_G: float = 0.0

# --- Coil effectiveness (contact factor) bounds, inclusive. ---
COIL_EFFECTIVENESS_MIN: float = 0.0
COIL_EFFECTIVENESS_MAX: float = 1.0

# --- Time-step duration bounds (s): exclusive lower, inclusive upper. Req 7.3. ---
TIME_STEP_DURATION_MIN_S: float = 0.0  # exclusive: duration must be > this
TIME_STEP_DURATION_MAX_S: float = 3600.0  # inclusive

# --- Disturbance moisture-rate bounds (g/s), inclusive. Requirements 5.1, 5.2, 5.7. ---
MOISTURE_RATE_MIN_G_PER_S: float = 0.0
MOISTURE_RATE_MAX_G_PER_S: float = 1000.0

# --- Maximum simultaneously active disturbances. Requirements 5.6, 6.5. ---
MAX_ACTIVE_DISTURBANCES: int = 100

# --- Reported absolute-humidity cap (g). Requirement 2.5. ---
ABSOLUTE_HUMIDITY_REPORTED_MAX_G: float = 999.9

# --- Reported relative-humidity bounds (%). Requirements 2.1, 2.5. ---
RELATIVE_HUMIDITY_MIN_PCT: float = 0.0
RELATIVE_HUMIDITY_MAX_PCT: float = 100.0


@dataclass(frozen=True)
class RoomConfig:
    """Room configuration input.

    Attributes:
        volume_m3: Fixed room volume in cubic meters. Permitted range
            ``[ROOM_VOLUME_MIN_M3, ROOM_VOLUME_MAX_M3]`` (Req 1.1-1.3).
        temperature_c: Room temperature in degrees Celsius. Permitted range
            ``[ROOM_TEMPERATURE_MIN_C, ROOM_TEMPERATURE_MAX_C]`` (Req 1.1, 1.4).
        initial_humidity_gm3: Initial absolute humidity in grams per cubic
            meter. Permitted range
            ``[INITIAL_HUMIDITY_MIN_GM3, INITIAL_HUMIDITY_MAX_GM3]``
            (Req 1.1, 1.5, 1.6).
    """

    volume_m3: float
    temperature_c: float
    initial_humidity_gm3: float


@dataclass(frozen=True)
class FanConfig:
    """Fan configuration input.

    Attributes:
        flow_rate_m3_per_s: Volumetric flow rate in cubic meters per second.
            Permitted range ``[FLOW_RATE_MIN_M3_PER_S, FLOW_RATE_MAX_M3_PER_S]``
            and must be a finite number (Req 3.1-3.4).
    """

    flow_rate_m3_per_s: float


@dataclass(frozen=True)
class CoilConfig:
    """Coil configuration input.

    Attributes:
        surface_temperature_c: Coil surface temperature in degrees Celsius.
        condensation_capacity_g_per_s: Maximum condensation rate in grams per
            second, constrained to ``>= CONDENSATION_CAPACITY_MIN_G`` (Req 4.8).
        effectiveness: Coil contact factor (``1 - bypass factor``) in
            ``[COIL_EFFECTIVENESS_MIN, COIL_EFFECTIVENESS_MAX]``: the fraction
            of the moved air brought to saturation at the coil temperature.
    """

    surface_temperature_c: float
    condensation_capacity_g_per_s: float
    effectiveness: float = 0.85


@dataclass(frozen=True)
class TimeStepConfig:
    """Time-step configuration input.

    Attributes:
        duration_s: Fixed time-step duration in seconds. Permitted range
            ``(TIME_STEP_DURATION_MIN_S, TIME_STEP_DURATION_MAX_S]`` — strictly
            greater than ``0.0`` and at most ``3600.0`` (Req 7.3).
    """

    duration_s: float

# --- Phase 2: Compressor COP bounds (dimensionless): exclusive lower, ---
# --- inclusive upper. Requirements 8.1-8.4. ---
COP_MIN: float = 0.0  # exclusive: COP must be > this
COP_MAX: float = 20.0  # inclusive

# --- Phase 2: Compressor setpoint-temperature bounds (deg C), inclusive. ---
# --- Requirements 8.1, 8.5. ---
SETPOINT_TEMPERATURE_MIN_C: float = -50.0
SETPOINT_TEMPERATURE_MAX_C: float = 100.0

# --- Phase 2: Compressor maximum electrical power (W): exclusive lower ---
# --- bound, no upper bound (``inf`` = unlimited). Requirement 8.10. ---
MAX_POWER_MIN_W: float = 0.0  # exclusive: power must be > this


def is_real_number(value: object) -> bool:
    """Whether ``value`` is a numeric, non-NaN int or float (not a bool).

    Used by boundary validation to tell "non-numeric or undefined" inputs
    (``None``, strings, ``NaN``, booleans) apart from numeric values that are
    merely out of range. Infinities count as numeric so they fall through to
    the below-minimum / exceeds-maximum checks.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not math.isnan(value)


@dataclass(frozen=True)
class CompressorConfig:
    """Compressor configuration input (Phase 2).

    Attributes:
        cop: Coefficient of performance, dimensionless, permitted range
            ``(COP_MIN, COP_MAX]`` (Req 8.1-8.4).
        setpoint_temperature_c: Coil surface temperature held while running,
            in degrees Celsius, permitted range
            ``[SETPOINT_TEMPERATURE_MIN_C, SETPOINT_TEMPERATURE_MAX_C]``
            (Req 8.1, 8.5, 8.6).
        max_power_w: Maximum electrical power absorbed by the compressor, in
            watts, ``> MAX_POWER_MIN_W`` (Req 8.10). Cooling is limited to
            ``cop * max_power_w``.
    """

    cop: float
    setpoint_temperature_c: float
    max_power_w: float = 400.0
