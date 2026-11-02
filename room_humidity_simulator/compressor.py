"""Compressor domain component (Phase 2).

The optional Compressor cools the Coil while running. Its electrical input is
limited to ``max_power_w`` (for example 400 W absorbed), so the cooling it can
deliver is limited to ``cop * max_power_w`` (design: *Compressor* and
*Power-limited coil*).

Key behaviors:

- ``cop`` in ``(0.0, 20.0]``, ``setpoint_temperature`` in ``[-50, 100]`` and
  ``max_power_w > 0`` are validated eagerly by :meth:`Compressor.create`,
  which returns a structured
  :class:`~room_humidity_simulator.results.ValidationResult` instead of
  raising (Requirements 8.1-8.6, 8.10).
- A newly created Compressor starts ``off`` (Requirement 8.8) and exposes its
  state as exactly one of ``running`` / ``off`` (Requirement 8.9).
- State changes are deferred: :meth:`Compressor.set_state` records a pending
  state that the simulator promotes at the start of the next step
  (Requirement 9.5).
- While running, :meth:`Compressor.run_step` computes what the coil removes:
  the coil is held at the setpoint when the heat load of the moved air there
  fits within ``cop * max_power_w`` (the compressor modulates below its
  maximum power); otherwise the coil cannot reach the setpoint and settles at
  the warmer temperature where the heat load equals ``cop * max_power_w``,
  with the compressor at full power (Requirements 9.1, 9.4, 9.6).

- ``cop`` may be a :class:`~room_humidity_simulator.cop_table.CopTable`
  (LUT over coil and optionally room temperature); the available cooling is
  then ``COP(T_coil) * max_power_w`` (Requirement 8.11).

Implemented in Task 15.1; power limit added in Task 23, COP LUT in Task 24.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional, Union

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.config import (
    COP_MAX,
    COP_MIN,
    MAX_POWER_MIN_W,
    SETPOINT_TEMPERATURE_MAX_C,
    SETPOINT_TEMPERATURE_MIN_C,
    is_real_number,
)
from room_humidity_simulator.cop_table import CopTable, validate_cop_table
from room_humidity_simulator.results import ConfigError, ValidationResult

if TYPE_CHECKING:  # pragma: no cover - typing-only import.
    from room_humidity_simulator.coil import Coil

#: Default maximum electrical power absorbed by the compressor, in watts.
DEFAULT_MAX_POWER_W: float = 400.0

#: Compressor types. ``on_off``: fixed speed, always at full power while
#: running; the coil temperature follows from the heat balance. ``modulating``:
#: variable capacity holding the coil at the setpoint when it can.
COMPRESSOR_MODES = ("on_off", "modulating")

#: Heat capacity of the evaporator (aluminium fins, copper tubes, refrigerant
#: and water film) for a small dehumidifier, J/K: about 1 kg of metal.
DEFAULT_COIL_HEAT_CAPACITY_J_PER_K: float = 1000.0
#: Heat exchanged between coil and room by natural convection with the fan
#: off, W/K.
DEFAULT_STILL_AIR_W_PER_K: float = 3.0
#: Longest internal sub-step of the coil thermal model, s.
_COIL_SUBSTEP_S: float = 5.0
_MAX_COIL_SUBSTEPS: int = 60

#: Sub-steps are sized so the room humidity excess above the coil outlet
#: humidity changes by at most about this fraction per sub-step.
_SUBSTEP_EXCESS_FRACTION: float = 0.05
_MAX_SUBSTEPS: int = 100
#: Coil-temperature resolution of the power-limited equilibrium search (K).
_TEMPERATURE_TOLERANCE_K: float = 1e-4


@dataclass(frozen=True)
class DefrostSettings:
    """Frost build-up on a sub-zero coil and the unit's defrost cycle.

    Below 0 deg C the water removed from the air freezes on the coil. The
    ice lowers the coil effectiveness (``frost_halving_g`` grams halve it).
    Like a dehumidifier control board, the unit starts an off-cycle defrost
    when the coil stays at or below ``start_below_c`` for
    ``start_delay_s``: the compressor stops, the fan keeps running and the
    room air melts the ice while the coil sits at 0 deg C. Defrost ends when
    the coil is back above ``end_above_c`` (ice gone) after at least
    ``min_duration_s``, or after ``max_duration_s`` in any case. A fraction
    ``reevaporation_fraction`` of the melted water re-evaporates into the
    room instead of draining.

    The defaults are typical of small refrigerant dehumidifiers (coil sensor
    trigger around -1 deg C, defrost end around +3 deg C, a few minutes per
    cycle); tune them to your unit.
    """

    frost_halving_g: float = 300.0
    start_below_c: float = -1.0
    start_delay_s: float = 300.0
    end_above_c: float = 3.0
    min_duration_s: float = 120.0
    max_duration_s: float = 900.0
    reevaporation_fraction: float = 0.3


def validate_defrost(settings: Optional[DefrostSettings]) -> ValidationResult[None]:
    """Check the defrost settings (``None`` = no frost model)."""
    if settings is None:
        return ValidationResult.ok()
    s = settings
    checks = (
        ("frostHalvingG", s.frost_halving_g, lambda v: v > 0.0, "frost halving must be greater than 0"),
        ("defrostStartBelowC", s.start_below_c, lambda v: -50.0 <= v <= 0.0, "defrost start must be between -50 and 0 °C"),
        ("defrostStartDelayS", s.start_delay_s, lambda v: v >= 0.0, "defrost delay must be >= 0"),
        ("defrostEndAboveC", s.end_above_c, lambda v: 0.0 <= v <= 30.0, "defrost end must be between 0 and 30 °C"),
        ("defrostMinS", s.min_duration_s, lambda v: v >= 0.0, "defrost minimum must be >= 0"),
        ("defrostMaxS", s.max_duration_s, lambda v: v > 0.0, "defrost maximum must be greater than 0"),
        ("reevaporationFraction", s.reevaporation_fraction, lambda v: 0.0 <= v <= 1.0, "re-evaporation must be between 0 and 1"),
    )
    for field_name, value, ok, message in checks:
        if not is_real_number(value) or not ok(value):
            return ValidationResult.fail(ConfigError(field_name, message, field_name))
    if s.max_duration_s < s.min_duration_s:
        return ValidationResult.fail(
            ConfigError("defrostMaxS", "defrost maximum must be >= minimum", "defrostMaxS")
        )
    return ValidationResult.ok()


class CompressorState(str, Enum):
    """Operational condition of the Compressor during a step (Req 8.9)."""

    RUNNING = "running"
    OFF = "off"


@dataclass(frozen=True)
class CompressorStepResult:
    """What the coil did during one running step.

    Attributes:
        removed_grams: Moisture condensed over the step.
        heat_extracted_joules: Latent plus sensible heat removed from the air.
        electrical_joules: Electrical energy absorbed (``heat / cop``).
        coil_surface_temperature_c: Coil temperature at the end of the step.
        power_limited: Whether the coil was held above the setpoint because
            the compressor was at maximum power at some point in the step.
        cop: Effective COP of the step (heat / electrical; the COP at the
            final coil temperature when no energy was used).
    """

    removed_grams: float
    heat_extracted_joules: float
    electrical_joules: float
    coil_surface_temperature_c: float
    power_limited: bool
    cop: float


@dataclass(frozen=True)
class OnOffStepResult:
    """One step of an on/off compressor and the coil's thermal balance.

    Attributes:
        removed_grams: Moisture condensed (or frozen) on the coil.
        frozen_grams: Part of ``removed_grams`` deposited as frost (coil < 0).
        melted_grams: Frost melted on the coil.
        heat_extracted_joules: Cooling delivered by the compressor.
        electrical_joules: Electrical energy (full power while running).
        coil_surface_temperature_c: Coil temperature at the end of the step.
        cop: Average COP over the running part of the step (or the COP at
            the coil temperature when off).
    """

    removed_grams: float
    frozen_grams: float
    melted_grams: float
    heat_extracted_joules: float
    electrical_joules: float
    coil_surface_temperature_c: float
    cop: float

def validate_compressor_config(
    cop: object,
    setpoint_temperature_c: object,
    max_power_w: object = DEFAULT_MAX_POWER_W,
    defrost: Optional[DefrostSettings] = None,
    mode: object = "on_off",
    coil_heat_capacity_j_per_k: object = DEFAULT_COIL_HEAT_CAPACITY_J_PER_K,
    still_air_w_per_k: object = DEFAULT_STILL_AIR_W_PER_K,
) -> ValidationResult[None]:
    """Validate COP, setpoint and maximum power against the Phase 2 bounds.

    ``cop`` is either a number or a :class:`CopTable`. Returns the first
    field-specific error found, matching the *Phase 2 Error Handling* table
    (Requirements 8.2-8.6, 8.10, 8.11), or a success.
    """
    if isinstance(cop, CopTable):
        table_check = validate_cop_table(cop)
        if table_check.is_error:
            return table_check
    elif not is_real_number(cop):
        return ValidationResult.fail(
            ConfigError("cop", "COP invalid", "cop_invalid")
        )
    elif cop <= COP_MIN:  # type: ignore[operator]
        return ValidationResult.fail(
            ConfigError(
                "cop", "COP must be greater than 0.0", "cop_not_positive"
            )
        )
    elif cop > COP_MAX:  # type: ignore[operator]
        return ValidationResult.fail(
            ConfigError("cop", "COP exceeds maximum", "cop_exceeds_maximum")
        )
    if not is_real_number(setpoint_temperature_c):
        return ValidationResult.fail(
            ConfigError(
                "setpointTemperatureC",
                "setpoint temperature invalid",
                "setpoint_temperature_invalid",
            )
        )
    if not (
        SETPOINT_TEMPERATURE_MIN_C
        <= setpoint_temperature_c  # type: ignore[operator]
        <= SETPOINT_TEMPERATURE_MAX_C
    ):
        return ValidationResult.fail(
            ConfigError(
                "setpointTemperatureC",
                "setpoint temperature out of range",
                "setpoint_temperature_out_of_range",
            )
        )
    if not is_real_number(max_power_w):
        return ValidationResult.fail(
            ConfigError("maxPowerW", "max power invalid", "max_power_invalid")
        )
    if max_power_w <= MAX_POWER_MIN_W:  # type: ignore[operator]
        return ValidationResult.fail(
            ConfigError(
                "maxPowerW",
                "max power must be greater than 0.0",
                "max_power_not_positive",
            )
        )
    if mode not in COMPRESSOR_MODES:
        return ValidationResult.fail(
            ConfigError("mode", "compressor mode must be 'on_off' or 'modulating'", "compressor_mode_invalid")
        )
    if mode == "on_off" and math.isinf(max_power_w):  # type: ignore[arg-type]
        return ValidationResult.fail(
            ConfigError("maxPowerW", "an on/off compressor needs a finite max power", "max_power_infinite")
        )
    if not is_real_number(coil_heat_capacity_j_per_k) or not coil_heat_capacity_j_per_k > 0.0:  # type: ignore[operator]
        return ValidationResult.fail(
            ConfigError("coilHeatCapacityJPerK", "coil heat capacity must be greater than 0", "coil_heat_capacity_not_positive")
        )
    if not is_real_number(still_air_w_per_k) or not still_air_w_per_k >= 0.0:  # type: ignore[operator]
        return ValidationResult.fail(
            ConfigError("stillAirWPerK", "still-air coupling must be >= 0", "still_air_negative")
        )
    return validate_defrost(defrost)


class Compressor:
    """Optional, power-limited compressor that cools the Coil while running.

    Build instances through :meth:`create`, which validates the inputs; the
    constructor trusts the values it is given, like the Phase 1 components.

    Args:
        cop: Coefficient of performance (cooling delivered per unit of
            electrical energy): a fixed number in ``(0.0, 20.0]``, or a
            :class:`CopTable` giving it as a function of the coil (and
            optionally room) temperature.
        setpoint_temperature_c: Coil surface temperature the compressor aims
            for while running, in degrees Celsius, in ``[-50, 100]``.
        max_power_w: Maximum electrical power absorbed, in watts (``> 0``;
            ``inf`` means unlimited).
    """

    def __init__(
        self,
        cop: Union[float, CopTable],
        setpoint_temperature_c: float,
        max_power_w: float = DEFAULT_MAX_POWER_W,
        defrost: Optional[DefrostSettings] = None,
        mode: str = "on_off",
        coil_heat_capacity_j_per_k: float = DEFAULT_COIL_HEAT_CAPACITY_J_PER_K,
        still_air_w_per_k: float = DEFAULT_STILL_AIR_W_PER_K,
    ) -> None:
        self._cop: Union[float, CopTable] = cop if isinstance(cop, CopTable) else float(cop)
        self._setpoint_temperature_c = float(setpoint_temperature_c)
        self._max_power_w = float(max_power_w)
        # A new Compressor always starts off (Requirement 8.8).
        self._state = CompressorState.OFF
        self._pending_state = CompressorState.OFF
        self.mode = mode
        self.coil_heat_capacity_j_per_k = float(coil_heat_capacity_j_per_k)
        self.still_air_w_per_k = float(still_air_w_per_k)
        # Frost model and defrost cycle (None = coil never frosts).
        self.defrost = defrost
        self.defrosting = False
        self.defrost_count = 0
        self._cold_s = 0.0
        self._defrost_elapsed_s = 0.0

    @staticmethod
    def create(
        cop: object,
        setpoint_temperature_c: object,
        max_power_w: object = DEFAULT_MAX_POWER_W,
        defrost: Optional[DefrostSettings] = None,
        mode: object = "on_off",
        coil_heat_capacity_j_per_k: object = DEFAULT_COIL_HEAT_CAPACITY_J_PER_K,
        still_air_w_per_k: object = DEFAULT_STILL_AIR_W_PER_K,
    ) -> ValidationResult["Compressor"]:
        """Validate the inputs and build a Compressor (Req 8.1-8.6, 8.10).

        Returns:
            A successful result carrying the new Compressor, or a failed
            result carrying the field-specific error.
        """
        validation = validate_compressor_config(
            cop, setpoint_temperature_c, max_power_w, defrost, mode, coil_heat_capacity_j_per_k, still_air_w_per_k
        )
        if validation.is_error:
            return ValidationResult.fail(validation.error)  # type: ignore[arg-type]
        return ValidationResult.ok(
            Compressor(
                cop, setpoint_temperature_c, max_power_w, defrost,  # type: ignore[arg-type]
                mode, coil_heat_capacity_j_per_k, still_air_w_per_k,  # type: ignore[arg-type]
            )
        )

    @property
    def running(self) -> bool:
        """Whether the compressor actually runs this step (on and not defrosting)."""
        return self._state is CompressorState.RUNNING and not self.defrosting

    def update_defrost(self, ran: bool, coil_temperature_c: float, dt: float) -> None:
        """Advance the defrost cycle after a step (Requirement 17.3).

        Called by the simulator with whether the compressor ran and the coil
        temperature at the end of the step.
        """
        settings = self.defrost
        if settings is None:
            return
        if self.defrosting:
            self._defrost_elapsed_s += dt
            done = coil_temperature_c >= settings.end_above_c
            if self._defrost_elapsed_s >= settings.min_duration_s and (
                done or self._defrost_elapsed_s >= settings.max_duration_s
            ):
                self.defrosting = False
            return
        if ran and coil_temperature_c <= settings.start_below_c:
            self._cold_s += dt
        else:
            self._cold_s = 0.0
        if self._cold_s >= settings.start_delay_s and self._cold_s > 0.0:
            self.defrosting = True
            self.defrost_count += 1
            self._defrost_elapsed_s = 0.0
            self._cold_s = 0.0

    @property
    def cop(self) -> Union[float, CopTable]:
        """Fixed COP in ``(0.0, 20.0]``, or the COP table (Req 8.7, 8.11)."""
        return self._cop

    @property
    def cop_table(self) -> Optional[CopTable]:
        """The COP table, or ``None`` when the COP is fixed."""
        return self._cop if isinstance(self._cop, CopTable) else None

    def cop_at(self, coil_temperature_c: float, room_temperature_c: float) -> float:
        """COP at the given coil and room temperatures (fixed or from the LUT)."""
        if isinstance(self._cop, CopTable):
            return self._cop.cop(coil_temperature_c, room_temperature_c)
        return self._cop

    @property
    def setpoint_temperature(self) -> float:
        """Setpoint temperature in degrees Celsius, in ``[-50, 100]`` (Req 8.7)."""
        return self._setpoint_temperature_c

    @property
    def max_power_w(self) -> float:
        """Maximum electrical power absorbed, in watts (Req 8.10)."""
        return self._max_power_w

    @property
    def max_cooling_w(self) -> float:
        """Maximum cooling: ``cop * max_power_w`` (with a LUT, its highest COP)."""
        best = self._cop.max_cop if isinstance(self._cop, CopTable) else self._cop
        return best * self._max_power_w

    def max_cooling_w_at(self, coil_temperature_c: float, room_temperature_c: float) -> float:
        """Cooling available at a coil temperature: ``COP(T) * max_power_w``."""
        return self.cop_at(coil_temperature_c, room_temperature_c) * self._max_power_w

    @property
    def state(self) -> CompressorState:
        """State governing the current step: ``running`` or ``off`` (Req 8.9)."""
        return self._state

    @property
    def pending_state(self) -> CompressorState:
        """State that takes effect at the start of the next step (Req 9.5)."""
        return self._pending_state

    def set_state(self, state: CompressorState) -> None:
        """Request ``state`` starting with the next step (Requirement 9.5)."""
        self._pending_state = state

    def promote_pending_state(self) -> None:
        """Apply the pending state; called by the simulator at step start."""
        self._state = self._pending_state

    def coil_surface_temperature(self, room_temperature_c: float) -> float:
        """Target coil surface temperature for a step (Requirements 9.1, 9.2).

        Returns the setpoint while running and the room temperature while off
        (Coil_Drift_Behavior), so the Phase 1 gating removes nothing while
        off (Requirement 9.3). While running, the temperature actually reached
        may be warmer when the compressor is power-limited (see
        :meth:`run_step`).
        """
        if self.running:  # on and not defrosting
            return self._setpoint_temperature_c
        return room_temperature_c

    def on_off_step(
        self,
        coil: "Coil",
        volume_m3: float,
        room_temperature_c: float,
        specific_humidity: float,
        flow_rate_m3_per_s: float,
        dt: float,
        running: bool,
    ) -> "OnOffStepResult":
        """Advance the coil temperature of an on/off unit over ``dt`` (Req 18).

        The coil (heat capacity ``C``) exchanges heat with the moved air
        (sensible to the effective air fraction, plus the latent heat of the
        water condensing or freezing on it) and with still room air, and is
        cooled by the compressor at ``COP(T) * P_max`` while running::

            C dT/dt = Q_air(T) + still_air * (T_room - T) - Q_compressor(T)

        The equation is integrated in short sub-steps, each solved exactly for
        the linearized balance, so it is stable whatever the time step. With
        frost on the coil the temperature cannot pass 0 deg C while warming:
        the surplus heat melts the ice first (334 J/g). Moisture is removed at
        the coil temperature of each sub-step; below 0 deg C it freezes.
        """
        capacity_c = self.coil_heat_capacity_j_per_k
        frost_model = self.defrost is not None
        T = coil.surface_temperature
        q = specific_humidity
        removed_total = frozen = melted = heat_total = 0.0

        def net_w(t: float, q_now: float) -> float:
            # Signed sensible exchange: a coil warmer than the air gives heat
            # back to it, so the balance stays smooth around T = T_room.
            sensible = (
                flow_rate_m3_per_s * psychrometrics.AIR_DENSITY_KG_PER_M3 * psychrometrics.AIR_SPECIFIC_HEAT_J_PER_KG_K
                * coil.active_effectiveness * (room_temperature_c - t)
            )
            latent = psychrometrics.latent_heat_joules(
                coil.removal_rate(q_now, room_temperature_c, flow_rate_m3_per_s, t), t
            )
            air = sensible + latent
            still = self.still_air_w_per_k * (room_temperature_c - t)
            cooling = self.cop_at(t, room_temperature_c) * self._max_power_w if running else 0.0
            return air + still - cooling

        # Each sub-step is solved exactly for the linearized balance, so it only
        # needs to be short compared with the coil time constant C/k for the
        # non-linear terms: about two per time constant, at most _MAX_COIL_SUBSTEPS.
        delta = 0.05
        k0 = max((net_w(T - delta, q) - net_w(T + delta, q)) / (2 * delta), 1e-6)
        h_target = max(_COIL_SUBSTEP_S, 0.5 * capacity_c / k0)
        steps = min(_MAX_COIL_SUBSTEPS, max(1, math.ceil(dt / h_target)))
        h = dt / steps

        for _ in range(steps):
            net = net_w(T, q)
            if frost_model and coil.frost_g > 0.0 and T >= 0.0 and net > 0.0:
                # Ice on the coil at 0 deg C: the surplus heat melts it first.
                T = 0.0
                melt = min(coil.frost_g, net * h / psychrometrics.LATENT_HEAT_OF_FUSION_J_PER_G)
                coil.frost_g -= melt
                melted += melt
                if coil.frost_g < 1e-9:
                    coil.frost_g = 0.0
                T_next = 0.0
            else:
                # Exact solution of C dT/dt = net(T0) - k (T - T0) over h.
                k = (net_w(T - delta, q) - net_w(T + delta, q)) / (2 * delta)
                k = max(k, 1e-6)
                T_next = T + net / k * -math.expm1(-k * h / capacity_c)
                if frost_model and coil.frost_g > 0.0 and T < 0.0 < T_next:
                    T_next = 0.0  # warming stops at the melting point while ice remains
            t_mid = 0.5 * (T + T_next)
            removed = coil.removal_for(volume_m3, room_temperature_c, q, flow_rate_m3_per_s, h, t_mid)
            if frost_model and t_mid < 0.0:
                coil.frost_g += removed
                frozen += removed
            removed_total += removed
            q = max(q - removed / volume_m3, 0.0)
            if running:
                heat_total += self.cop_at(t_mid, room_temperature_c) * self._max_power_w * h
            T = T_next

        electrical = self._max_power_w * dt if running else 0.0
        cop = heat_total / electrical if electrical > 0.0 else self.cop_at(T, room_temperature_c)
        return OnOffStepResult(removed_total, frozen, melted, heat_total, electrical, T, cop)

    # ------------------------------------------------------------------
    # Power-limited operation (Task 23)
    # ------------------------------------------------------------------

    @staticmethod
    def heat_load_w(
        coil: "Coil",
        surface_temperature_c: float,
        specific_humidity: float,
        room_temperature_c: float,
        flow_rate_m3_per_s: float,
    ) -> float:
        """Heat the moved air would give the coil at ``surface_temperature_c``.

        Sensible (cooling the effective air fraction to the coil temperature)
        plus latent (condensation at the instantaneous removal rate), in W.
        Decreases as the coil temperature rises and is ``0`` at room
        temperature.
        """
        sensible = psychrometrics.sensible_heat_joules(
            flow_rate_m3_per_s, room_temperature_c, surface_temperature_c, coil.active_effectiveness
        )
        latent = psychrometrics.latent_heat_joules(
            coil.removal_rate(
                specific_humidity, room_temperature_c, flow_rate_m3_per_s, surface_temperature_c
            ),
            surface_temperature_c,
        )
        return sensible + latent

    def equilibrium_coil_temperature(
        self,
        coil: "Coil",
        specific_humidity: float,
        room_temperature_c: float,
        flow_rate_m3_per_s: float,
    ) -> "tuple[float, bool]":
        """Coil temperature the compressor can sustain, and whether power-limited.

        The setpoint when its heat load fits within the cooling available there
        (``COP(T) * max_power_w``); otherwise the temperature between setpoint
        and room where the heat load equals the available cooling (found by
        bisection, rounded toward the warmer side so the power limit is never
        exceeded). A COP that rises with coil temperature, as in real
        compressors, pushes this balance toward warmer coils.
        """
        setpoint = self._setpoint_temperature_c
        if setpoint >= room_temperature_c:
            return setpoint, False

        def excess(t: float) -> float:
            load = self.heat_load_w(coil, t, specific_humidity, room_temperature_c, flow_rate_m3_per_s)
            return load - self.max_cooling_w_at(t, room_temperature_c)

        if excess(setpoint) <= 0.0:
            return setpoint, False
        # excess(cold) > 0 and excess(warm) < 0 (no load at room temperature).
        cold, warm = setpoint, room_temperature_c
        while warm - cold > _TEMPERATURE_TOLERANCE_K:
            mid = 0.5 * (cold + warm)
            if excess(mid) > 0.0:
                cold = mid
            else:
                warm = mid
        return warm, True

    def run_step(
        self,
        coil: "Coil",
        volume_m3: float,
        room_temperature_c: float,
        specific_humidity: float,
        flow_rate_m3_per_s: float,
        dt: float,
    ) -> CompressorStepResult:
        """Removal, heat and electrical energy for one running step.

        The step is split into sub-steps short enough that the room humidity
        changes little within each. In every sub-step the coil temperature is
        set from :meth:`equilibrium_coil_temperature` for the current room
        humidity, removal is integrated exactly at that temperature, and the
        electrical energy is ``heat / COP(T_coil)``, which never exceeds
        ``max_power_w`` times the sub-step (Requirements 9.6, 10.5, 11.9).
        """
        # The room only dries during removal, so the heat load only falls
        # within the step. If the compressor is not power-limited at the start
        # it never is, and if it is limited without condensing the coil
        # temperature does not change: one exact interval suffices in both
        # cases. Otherwise sub-steps track the coil warming/cooling with the
        # changing humidity.
        t_start, limited_start = self.equilibrium_coil_temperature(
            coil, specific_humidity, room_temperature_c, flow_rate_m3_per_s
        )
        effective_flow = flow_rate_m3_per_s * coil.active_effectiveness
        steps = 1
        if (
            limited_start
            and effective_flow > 0.0
            and coil.condenses(specific_humidity, room_temperature_c, t_start)
        ):
            decay = effective_flow / volume_m3 * dt
            steps = min(_MAX_SUBSTEPS, max(1, math.ceil(decay / _SUBSTEP_EXCESS_FRACTION)))
        h = dt / steps

        q = specific_humidity
        removed_total = heat_total = electrical_total = 0.0
        limited = False
        t_coil = self._setpoint_temperature_c
        for i in range(steps):
            if i == 0:
                t_coil, limited_now = t_start, limited_start
            else:
                t_coil, limited_now = self.equilibrium_coil_temperature(
                    coil, q, room_temperature_c, flow_rate_m3_per_s
                )
            limited = limited or limited_now
            removed = coil.removal_for(
                volume_m3, room_temperature_c, q, flow_rate_m3_per_s, h, t_coil
            )
            sensible = psychrometrics.sensible_heat_joules(
                flow_rate_m3_per_s * h, room_temperature_c, t_coil, coil.active_effectiveness
            )
            heat = max(psychrometrics.latent_heat_joules(removed, t_coil) + sensible, 0.0)
            cop = self.cop_at(t_coil, room_temperature_c)
            # The load only falls during a sub-step (the room dries), so its
            # average stays within the cooling capacity; guard rounding.
            heat = min(heat, cop * self._max_power_w * h)
            removed_total += removed
            heat_total += heat
            electrical_total += heat / cop
            q = max(q - removed / volume_m3, 0.0)
        if not isinstance(self._cop, CopTable):
            # Fixed COP: exactly heat / COP (Requirement 10.5).
            electrical_total = heat_total / self._cop
        effective_cop = (
            heat_total / electrical_total
            if electrical_total > 0.0
            else self.cop_at(t_coil, room_temperature_c)
        )
        return CompressorStepResult(
            removed_grams=removed_total,
            heat_extracted_joules=heat_total,
            electrical_joules=electrical_total,
            coil_surface_temperature_c=t_coil,
            power_limited=limited,
            cop=effective_cop,
        )

