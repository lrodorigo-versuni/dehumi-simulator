"""Stateless psychrometric physics functions.

This module holds the pure saturation and dew-point functions described in
the design's *Psychrometrics* table and the *Core physics and mass balance*
section:

- ``saturation_vapor_pressure(T)`` -- August-Roche-Magnus saturation pressure
  in hPa.
- ``saturation_content_density(T)`` -- saturation moisture density in g/m^3.
- ``saturation_content_grams(T, volume)`` -- total saturation capacity in g.
- ``dew_point(specific_humidity, T)`` -- dew point in deg C via the inverted
  Magnus formula.
- ``relative_humidity_percent(abs_grams, T, volume)`` -- relative humidity in
  percent, rounded to one decimal and capped to ``[0, 100]``.

All functions are pure and stateless: the same inputs always produce the same
outputs. Temperatures are in degrees Celsius, pressures in hPa, densities in
g/m^3, volumes in m^3, and water masses in grams.

Implemented in Task 2.1. The Phase 2 latent/sensible heat helpers are added
later in Task 14.
"""

from __future__ import annotations

import math

# --- August-Roche-Magnus coefficients (design: Core physics and mass balance). ---
# es(T) = MAGNUS_A * exp(MAGNUS_B * T / (MAGNUS_C + T)), es in hPa, T in deg C.
MAGNUS_A_HPA: float = 6.112
MAGNUS_B: float = 17.62
MAGNUS_C_C: float = 243.12

# --- Ideal-gas conversion constant: hPa saturation pressure -> g/m^3 density. ---
# rho_vs(T) = IDEAL_GAS_CONSTANT * es(T) / (KELVIN_OFFSET + T).
IDEAL_GAS_CONSTANT: float = 216.7
KELVIN_OFFSET_C: float = 273.15

# --- Reported relative-humidity bounds (%). Requirements 2.1, 2.5. ---
RELATIVE_HUMIDITY_MIN_PCT: float = 0.0
RELATIVE_HUMIDITY_MAX_PCT: float = 100.0
RELATIVE_HUMIDITY_DECIMALS: int = 1


def saturation_vapor_pressure(temperature_c: float) -> float:
    """Saturation vapor pressure of air in hPa at ``temperature_c`` (deg C).

    Uses the August-Roche-Magnus form
    ``es = 6.112 * exp(17.62 * T / (243.12 + T))`` (design: Core physics and
    mass balance). The function is monotonic and finite across the room
    temperature bounds of -50 to 100 deg C (Requirement 2.4).

    Args:
        temperature_c: Air temperature in degrees Celsius.

    Returns:
        Saturation vapor pressure in hectopascals (hPa).
    """
    return MAGNUS_A_HPA * math.exp(
        MAGNUS_B * temperature_c / (MAGNUS_C_C + temperature_c)
    )


def saturation_content_density(temperature_c: float) -> float:
    """Saturation moisture density in g/m^3 at ``temperature_c`` (deg C).

    Converts the saturation vapor pressure to a vapor density through the
    ideal-gas relationship
    ``rho_vs(T) = 216.7 * es(T) / (273.15 + T)`` (design: Core physics and
    mass balance; Requirement 2.4, 2.1).

    Args:
        temperature_c: Air temperature in degrees Celsius.

    Returns:
        Saturation moisture density in grams per cubic meter (g/m^3).
    """
    return (
        IDEAL_GAS_CONSTANT
        * saturation_vapor_pressure(temperature_c)
        / (KELVIN_OFFSET_C + temperature_c)
    )


def saturation_content_grams(temperature_c: float, volume_m3: float) -> float:
    """Total saturation moisture capacity in grams for a room.

    ``S(T, V) = rho_vs(T) * V`` -- the maximum mass of water vapor the room air
    can hold at ``temperature_c`` before condensation (design: Core physics
    and mass balance; Requirements 2.1, 2.3, 5.9).

    Args:
        temperature_c: Room temperature in degrees Celsius.
        volume_m3: Room volume in cubic meters.

    Returns:
        Saturation moisture content in grams of water.
    """
    return saturation_content_density(temperature_c) * volume_m3


def dew_point(specific_humidity_gm3: float, temperature_c: float) -> float:
    """Dew point in deg C of air with the given specific humidity.

    Recovers the actual vapor pressure of the moved air from its specific
    humidity (``e = q * (273.15 + T) / 216.7``) and inverts the Magnus formula
    to obtain the dew point
    ``Td = 243.12 * gamma / (17.62 - gamma)`` where ``gamma = ln(e / 6.112)``
    (design: Core physics and mass balance; Requirements 4.3, 4.4).

    Args:
        specific_humidity_gm3: Specific humidity of the air in g/m^3
            (``absolute_humidity_grams / volume``).
        temperature_c: Air temperature in degrees Celsius, used to convert
            the specific humidity into an actual vapor pressure.

    Returns:
        Dew point temperature in degrees Celsius.

    Raises:
        ValueError: If the specific humidity is non-positive, in which case
            there is no physically meaningful dew point (zero vapor pressure).
    """
    if specific_humidity_gm3 <= 0.0:
        raise ValueError(
            "dew_point is undefined for non-positive specific humidity"
        )
    # Actual vapor pressure of the moved air (hPa), inverting the density
    # relationship rho = 216.7 * e / (273.15 + T).
    actual_vapor_pressure_hpa = (
        specific_humidity_gm3 * (KELVIN_OFFSET_C + temperature_c) / IDEAL_GAS_CONSTANT
    )
    gamma = math.log(actual_vapor_pressure_hpa / MAGNUS_A_HPA)
    return MAGNUS_C_C * gamma / (MAGNUS_B - gamma)


def coil_outlet_humidity_density(
    coil_surface_temperature_c: float, room_temperature_c: float
) -> float:
    """Vapor density (g/m^3, at room temperature) of air saturated at the coil.

    Air leaving an ideal coil is saturated at the coil surface temperature, so
    its vapor pressure is ``es(T_coil)``. Vapor pressure is conserved when that
    air re-mixes into the room, where it occupies the room-temperature volume:
    ``q_out = 216.7 * es(T_coil) / (273.15 + T_room)``.

    This is the lowest specific humidity the coil can bring room air down to.
    ``q_room > q_out`` holds exactly when the room dew point is above the coil
    temperature (Requirements 4.3, 4.4).
    """
    return (
        IDEAL_GAS_CONSTANT
        * saturation_vapor_pressure(coil_surface_temperature_c)
        / (KELVIN_OFFSET_C + room_temperature_c)
    )


def relative_humidity_percent(
    abs_grams: float, temperature_c: float, volume_m3: float
) -> float:
    """Relative humidity in percent, rounded to one decimal and capped.

    ``RH = clamp(100 * abs_grams / S(T, V), 0.0, 100.0)`` rounded to one
    decimal place (design: Core physics and mass balance; Requirements 2.1,
    2.5, 2.6). Returns ``0.0`` when there is no moisture (``abs_grams == 0``)
    or no capacity (``S(T, V) == 0``), avoiding a division by zero and
    realizing Requirements 2.6 and 2.7.

    Args:
        abs_grams: Absolute humidity (mass of water vapor) in grams.
        temperature_c: Room temperature in degrees Celsius.
        volume_m3: Room volume in cubic meters.

    Returns:
        Relative humidity as a percentage in ``[0.0, 100.0]`` rounded to one
        decimal place.
    """
    if abs_grams == 0.0:
        return 0.0
    capacity = saturation_content_grams(temperature_c, volume_m3)
    if capacity == 0.0:
        return 0.0
    ratio_pct = 100.0 * abs_grams / capacity
    clamped = min(
        RELATIVE_HUMIDITY_MAX_PCT, max(RELATIVE_HUMIDITY_MIN_PCT, ratio_pct)
    )
    return round(clamped, RELATIVE_HUMIDITY_DECIMALS)


# --- Phase 2 energy-model constants (design: Physical constants for the ---
# --- sensible-heat term; Energy model and per-step formulas). ---
# Latent heat released per gram of water vapor condensed on the coil, in J/g
# (Requirement 10.2).
LATENT_HEAT_OF_CONDENSATION_J_PER_G: float = 2260.0

# Standard engineering reference values for air at approximately 20 deg C and
# sea-level pressure (design: Physical constants for the sensible-heat term;
# Requirement 10.3): density ~= 1.2 kg/m^3 and specific heat at constant
# pressure ~= 1005 J/(kg.K). Content was rephrased for compliance with
# licensing restrictions.
AIR_DENSITY_KG_PER_M3: float = 1.2
AIR_SPECIFIC_HEAT_J_PER_KG_K: float = 1005.0


# Heat released when liquid water freezes, J/g. Water condensing on a coil
# below 0 deg C deposits as frost and releases this on top of condensation.
LATENT_HEAT_OF_FUSION_J_PER_G: float = 334.0


def latent_heat_joules(removed_grams: float, surface_temperature_c: "float | None" = None) -> float:
    """Latent heat released by condensing ``removed_grams`` of water, in joules.

    ``latentJoules = removedGrams * 2260`` -- 2260 J is released per gram of
    water vapor condensed on the coil (design: Energy model and per-step
    formulas; Requirement 10.2). This term always applies while the compressor
    is running.

    Args:
        removed_grams: Mass of water condensed in the step, in grams
            (always ``>= 0`` from the Phase 1 coil removal algorithm).

        surface_temperature_c: Coil surface temperature. Below 0 deg C the
            water freezes on the coil, adding the heat of fusion (334 J/g).

    Returns:
        Latent heat in joules (J).
    """
    per_gram = LATENT_HEAT_OF_CONDENSATION_J_PER_G
    if surface_temperature_c is not None and surface_temperature_c < 0.0:
        per_gram += LATENT_HEAT_OF_FUSION_J_PER_G
    return removed_grams * per_gram


def sensible_heat_joules(
    moved_air_volume_m3: float,
    room_temperature_c: float,
    coil_surface_temperature_c: float,
    effectiveness: float = 1.0,
) -> float:
    """Sensible heat to cool the moved air to the coil surface, in joules.

    ``sensibleJoules = movedAirVolume * airDensity * cp *
    (roomTemp - coilSurfaceTemp)`` -- the energy to cool the mass of air the
    fan moves across the coil from the room temperature down to the coil
    surface temperature (design: Energy model and per-step formulas;
    Requirements 10.3, 10.4). A temperature difference in deg C equals the
    difference in kelvin, so the factor is used directly with ``cp`` in
    ``J/(kg.K)``.

    The term is ``0.0`` unless there is both air to cool
    (``moved_air_volume_m3 > 0``) and a positive temperature difference
    (``room_temperature_c > coil_surface_temperature_c``). In particular it is
    ``0.0`` when ``moved_air_volume_m3 == 0`` (Requirement 10.4) and when
    ``room_temperature_c <= coil_surface_temperature_c`` (no cooling occurs).

    Args:
        moved_air_volume_m3: Volume of air moved across the coil this step, in
            cubic meters (Phase 1 ``flow_rate * dt``).
        room_temperature_c: Room temperature in degrees Celsius.
        coil_surface_temperature_c: Coil surface temperature in degrees Celsius.
        effectiveness: Coil contact factor in ``[0, 1]``. The moved air leaves
            the coil at ``T_room - effectiveness * (T_room - T_coil)``, so the
            sensible term scales with it, consistent with the moisture model.
            ``1.0`` is an ideal coil.

    Returns:
        Sensible heat in joules (J), ``0.0`` when no cooling occurs.
    """
    if moved_air_volume_m3 > 0.0 and room_temperature_c > coil_surface_temperature_c:
        air_mass_kg = moved_air_volume_m3 * AIR_DENSITY_KG_PER_M3
        return (
            air_mass_kg
            * AIR_SPECIFIC_HEAT_J_PER_KG_K
            * effectiveness
            * (room_temperature_c - coil_surface_temperature_c)
        )
    return 0.0
