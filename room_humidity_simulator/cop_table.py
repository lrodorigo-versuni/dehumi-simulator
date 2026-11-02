"""Lookup table (LUT) for a temperature-dependent compressor COP.

A real compressor's COP falls as the coil (evaporator) gets colder and as the
room (where the condenser rejects heat) gets warmer: the refrigerant has to
be lifted across a larger temperature difference. :class:`CopTable` holds
that characteristic as measured or datasheet points:

- **1D**: ``cops[j]`` at ``coil_temperatures_c[j]``; linear interpolation.
- **2D**: ``cops[i][j]`` at ``room_temperatures_c[i]`` and
  ``coil_temperatures_c[j]``; bilinear interpolation.

Above the warmest coil temperature and outside the room axis the value at the
nearest edge is used (clamped). Below the coldest coil temperature the COP
keeps falling: ``COP_FALLOFF_PER_K`` of the edge value per kelvin, never below
``COP_FLOOR_FRACTION`` of it. With a fixed electrical power this is the loss
of cooling capacity a real compressor shows as its evaporating temperature
drops (suction gas gets thinner); holding the edge COP instead would let a
power-limited coil run away to unrealistic temperatures (-25 deg C or lower)
at low airflow.

Validation never raises: :func:`validate_cop_table` returns a structured
:class:`~room_humidity_simulator.results.ValidationResult` (Req 8.11).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from room_humidity_simulator.config import COP_MAX, COP_MIN, is_real_number
from room_humidity_simulator.results import ConfigError, ValidationResult


#: Relative COP (and so cooling) loss per kelvin below the coldest coil
#: temperature of the table. Compressor cooling capacity typically drops by
#: 3-5 % per kelvin of evaporating temperature.
COP_FALLOFF_PER_K: float = 0.04

#: Lowest COP below the table, as a fraction of the coldest tabulated value.
COP_FLOOR_FRACTION: float = 0.1


@dataclass(frozen=True)
class CopTable:
    """COP as a function of coil temperature (and optionally room temperature).

    Attributes:
        coil_temperatures_c: Strictly increasing coil temperatures (deg C).
        cops: 1D: one COP per coil temperature. 2D: one row per room
            temperature, each with one COP per coil temperature.
        room_temperatures_c: Strictly increasing room temperatures (deg C)
            for a 2D table, or ``None`` for a 1D table.
    """

    coil_temperatures_c: Sequence[float]
    cops: Sequence
    room_temperatures_c: Optional[Sequence[float]] = None

    @property
    def is_2d(self) -> bool:
        return self.room_temperatures_c is not None

    def rows(self) -> Tuple[Tuple[float, ...], ...]:
        """COP rows (a single row for a 1D table)."""
        if self.is_2d:
            return tuple(tuple(row) for row in self.cops)
        return (tuple(self.cops),)

    def cop(self, coil_temperature_c: float, room_temperature_c: float) -> float:
        """Interpolated COP at the given coil and room temperatures."""
        coil_axis = tuple(self.coil_temperatures_c)
        rows = self.rows()
        below_k = coil_axis[0] - coil_temperature_c
        if below_k > 0.0:
            # Colder than the table: evaluate at its edge, then apply the falloff.
            factor = max(1.0 - COP_FALLOFF_PER_K * below_k, COP_FLOOR_FRACTION)
            return factor * self.cop(coil_axis[0], room_temperature_c)
        if not self.is_2d:
            return _interp(coil_axis, rows[0], coil_temperature_c)
        room_axis = tuple(self.room_temperatures_c)  # type: ignore[arg-type]
        per_room = tuple(_interp(coil_axis, row, coil_temperature_c) for row in rows)
        return _interp(room_axis, per_room, room_temperature_c)

    @property
    def min_cop(self) -> float:
        """Lowest tabulated COP (the falloff below the table can go lower)."""
        return min(min(row) for row in self.rows())

    @property
    def max_cop(self) -> float:
        return max(max(row) for row in self.rows())


def _interp(axis: Sequence[float], values: Sequence[float], x: float) -> float:
    """Piecewise-linear interpolation, clamped to the end values."""
    if len(axis) == 1 or x <= axis[0]:
        return values[0]
    if x >= axis[-1]:
        return values[-1]
    j = bisect.bisect_right(axis, x)
    x0, x1 = axis[j - 1], axis[j]
    y0, y1 = values[j - 1], values[j]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _fail(reason: str) -> ValidationResult:
    return ValidationResult.fail(
        ConfigError("copTable", "COP table invalid: " + reason, "cop_table_invalid")
    )


def _check_axis(axis: object, name: str) -> Optional[ValidationResult]:
    if isinstance(axis, (str, bytes)) or not hasattr(axis, "__len__"):
        return _fail(name + " must be a sequence")
    if len(axis) == 0:  # type: ignore[arg-type]
        return _fail(name + " is empty")
    for value in axis:  # type: ignore[attr-defined]
        if not is_real_number(value) or math.isinf(value):
            return _fail(name + " must contain finite numbers")
    if any(b <= a for a, b in zip(axis, list(axis)[1:])):  # type: ignore[call-overload]
        return _fail(name + " must be strictly increasing")
    return None


def validate_cop_table(table: CopTable) -> ValidationResult[None]:
    """Check axes are finite and strictly increasing and every COP is in ``(0, 20]``."""
    error = _check_axis(table.coil_temperatures_c, "coil temperatures")
    if error is not None:
        return error
    n_coil = len(table.coil_temperatures_c)
    if table.is_2d:
        error = _check_axis(table.room_temperatures_c, "room temperatures")
        if error is not None:
            return error
        cops = table.cops
        if isinstance(cops, (str, bytes)) or not hasattr(cops, "__len__") or len(cops) != len(
            table.room_temperatures_c  # type: ignore[arg-type]
        ):
            return _fail("needs one COP row per room temperature")
        rows = cops
    else:
        rows = (table.cops,)
    for row in rows:
        if isinstance(row, (str, bytes)) or not hasattr(row, "__len__") or len(row) != n_coil:
            return _fail("needs one COP per coil temperature")
        for value in row:
            if not is_real_number(value) or not (COP_MIN < value <= COP_MAX):
                return _fail("every COP must be greater than 0.0 and at most 20.0")
    return ValidationResult.ok()
