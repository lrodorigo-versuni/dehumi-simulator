"""Simulator orchestrator.

The Simulator validates configuration, manages disturbances, and advances the
simulation in discrete time steps following the strict per-step update order
(design: *Per-Time-Step Data Flow* and *Mass balance per time step*):

1. Phase 2 hook (only with a Compressor): promote the pending compressor
   state and set the coil surface temperature from it (Req 9.1, 9.2, 9.5).
2. Sum the valid contributions of the active disturbances, excluding and
   reporting invalid returns (Req 5.5, 6.4, 6.6).
3. Add them to the room and cap at saturation (Req 5.9).
4. Compute the moved-air volume and the coil removal against the capped
   state (Req 3.5, 4.1-4.5).
5. Subtract the removed mass and clamp at ``0.0`` (Req 4.6, 4.7, 7.4).
6. Phase 2 hook (only with a Compressor): compute heat extracted, electrical
   energy, cumulative energy and power draw (Req 10, 11).
7. Increase the simulation time by ``dt`` (Req 7.2).

Configuration errors never raise past the public API: every mutating call
returns a :class:`~room_humidity_simulator.results.ValidationResult`, and on
failure the previously valid state is retained unchanged.

Implemented in Tasks 8, 9 and 11 (Phase 1) and Tasks 16-18 (Phase 2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.coil import Coil
from room_humidity_simulator.compressor import (
    DEFAULT_COIL_HEAT_CAPACITY_J_PER_K,
    DEFAULT_MAX_POWER_W,
    DEFAULT_STILL_AIR_W_PER_K,
    Compressor,
    CompressorState,
    DefrostSettings,
)
from room_humidity_simulator.config import (
    COIL_EFFECTIVENESS_MAX,
    COIL_EFFECTIVENESS_MIN,
    CONDENSATION_CAPACITY_MIN_G,
    FLOW_RATE_MAX_M3_PER_S,
    FLOW_RATE_MIN_M3_PER_S,
    INITIAL_HUMIDITY_MAX_GM3,
    INITIAL_HUMIDITY_MIN_GM3,
    MAX_ACTIVE_DISTURBANCES,
    MOISTURE_RATE_MAX_G_PER_S,
    MOISTURE_RATE_MIN_G_PER_S,
    ROOM_TEMPERATURE_MAX_C,
    ROOM_TEMPERATURE_MIN_C,
    ROOM_VOLUME_MAX_M3,
    ROOM_VOLUME_MIN_M3,
    TIME_STEP_DURATION_MAX_S,
    TIME_STEP_DURATION_MIN_S,
    CoilConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
    is_real_number,
)
from room_humidity_simulator.cop_table import CopTable
from room_humidity_simulator.disturbances import Disturbance
from room_humidity_simulator.fan import Fan
from room_humidity_simulator.results import (
    ConfigError,
    DisturbanceError,
    ValidationResult,
)
from room_humidity_simulator.room import Room


@dataclass(frozen=True)
class RoomState:
    """Snapshot of the room's observable humidity state (Req 1.7-1.9, 7.5).

    Attributes:
        absolute_humidity_grams: Reported absolute humidity, ``[0.0, 999.9]`` g.
        relative_humidity_pct: Relative humidity, ``[0.0, 100.0]`` %.
        temperature_c: Room temperature in degrees Celsius.
        volume_m3: Room volume in cubic meters.
        relative_humidity_derivable: ``False`` when the saturation capacity is
            ``0.0`` and the relative humidity could not be derived
            (Requirement 2.7).
    """

    absolute_humidity_grams: float
    relative_humidity_pct: float
    temperature_c: float
    volume_m3: float
    relative_humidity_derivable: bool


@dataclass(frozen=True)
class StepRecord:
    """Breakdown of the most recently completed time step.

    Exposes the intermediate quantities of the mass balance and energy model
    so callers (and tests) can inspect how a step was computed.

    Attributes:
        dt: Time-step duration in seconds.
        humidity_before_grams: Absolute humidity at the start of the step.
        added_grams: Total valid disturbance contribution (Req 5.5).
        humidity_after_additions_grams: Absolute humidity after addition and
            saturation cap (Req 5.9).
        moved_air_volume_m3: Volume moved across the coil (Req 3.5).
        coil_surface_temperature_c: Coil surface temperature at the end of
            the step (with a power-limited compressor it can vary within it).
        removed_grams: Coil removal (Req 4.1).
        humidity_after_grams: Absolute humidity at the end of the step.
        compressor_state: Compressor state governing the step, or ``None``
            when no Compressor is configured.
        heat_extracted_joules: Heat extracted (Req 10.1), ``0.0`` unless
            running.
        electrical_joules: Electrical energy consumed (Req 10.5-10.7).
        power_limited: Whether the compressor ran at maximum power with the
            coil held above its setpoint (Req 9.6).
        cop: Effective COP of a running step (heat / electrical), or
            ``None`` when the compressor is off or absent.
        defrosting: The unit was defrosting this step (compressor stopped
            whatever was requested; Req 17.3).
        compressor_running: The compressor actually ran this step.
        frost_g: Ice on the coil at the end of the step.
        melted_g: Ice melted this step.
        reevaporated_g: Melted water returned to the room air (included in
            ``added_grams``).
        disturbance_errors: Invalid disturbance returns excluded (Req 6.6).
    """

    dt: float
    humidity_before_grams: float
    added_grams: float
    humidity_after_additions_grams: float
    moved_air_volume_m3: float
    coil_surface_temperature_c: float
    removed_grams: float
    humidity_after_grams: float
    compressor_state: Optional[CompressorState]
    heat_extracted_joules: float
    electrical_joules: float
    power_limited: bool
    cop: Optional[float]
    disturbance_errors: Tuple[DisturbanceError, ...]
    defrosting: bool = False
    compressor_running: bool = False
    frost_g: float = 0.0
    melted_g: float = 0.0
    reevaporated_g: float = 0.0


def _fail(field: str, message: str) -> ValidationResult:
    """Build a failed result whose code is the snake_case message."""
    return ValidationResult.fail(
        ConfigError(field=field, message=message, code=message.replace(" ", "_"))
    )


def _validate_room(cfg: RoomConfig) -> Optional[ValidationResult]:
    volume = cfg.volume_m3
    if not is_real_number(volume):
        return _fail("volumeM3", "room volume invalid")
    if volume < ROOM_VOLUME_MIN_M3:
        return _fail("volumeM3", "room volume below minimum")
    if volume > ROOM_VOLUME_MAX_M3:
        return _fail("volumeM3", "room volume exceeds maximum")
    temperature = cfg.temperature_c
    if not is_real_number(temperature) or not (
        ROOM_TEMPERATURE_MIN_C <= temperature <= ROOM_TEMPERATURE_MAX_C
    ):
        return _fail("temperatureC", "room temperature out of range")
    humidity = cfg.initial_humidity_gm3
    if not is_real_number(humidity):
        return _fail("initialHumidityGm3", "initial humidity invalid")
    if humidity < INITIAL_HUMIDITY_MIN_GM3:
        return _fail("initialHumidityGm3", "initial humidity below minimum")
    if humidity > INITIAL_HUMIDITY_MAX_GM3:
        return _fail("initialHumidityGm3", "initial humidity exceeds maximum")
    return None


def _validate_fan(cfg: FanConfig) -> Optional[ValidationResult]:
    flow = cfg.flow_rate_m3_per_s
    if not is_real_number(flow):
        return _fail("flowRateM3PerS", "flow rate invalid")
    if flow < FLOW_RATE_MIN_M3_PER_S:
        return _fail("flowRateM3PerS", "flow rate below minimum")
    if flow > FLOW_RATE_MAX_M3_PER_S:
        return _fail("flowRateM3PerS", "flow rate exceeds maximum")
    return None


def _validate_coil(cfg: CoilConfig) -> Optional[ValidationResult]:
    if not is_real_number(cfg.surface_temperature_c):
        return _fail("surfaceTemperatureC", "coil surface temperature invalid")
    capacity = cfg.condensation_capacity_g_per_s
    if not is_real_number(capacity):
        return _fail("condensationCapacityGPerS", "condensation capacity invalid")
    if capacity < CONDENSATION_CAPACITY_MIN_G:
        return _fail("condensationCapacityGPerS", "condensation capacity below minimum")
    effectiveness = cfg.effectiveness
    if not is_real_number(effectiveness) or not (
        COIL_EFFECTIVENESS_MIN <= effectiveness <= COIL_EFFECTIVENESS_MAX
    ):
        return _fail("effectiveness", "coil effectiveness out of range")
    return None


def _validate_duration(duration: object) -> Optional[ValidationResult]:
    if not is_real_number(duration) or not (
        TIME_STEP_DURATION_MIN_S < duration <= TIME_STEP_DURATION_MAX_S  # type: ignore[operator]
    ):
        return _fail("durationS", "time step duration out of range")
    return None


class Simulator:
    """Time-stepped room humidity simulation engine.

    Typical use::

        sim = Simulator()
        sim.configure(RoomConfig(50, 25, 15), FanConfig(0.5),
                      CoilConfig(5, 0.05), TimeStepConfig(60))
        sim.add_disturbance(Person_Disturbance(0.02))
        sim.advance(n=60)
        sim.room_state()

    A Simulator is unusable until :meth:`configure` succeeds; until then
    :meth:`advance` returns a "simulator not configured" error.
    """

    def __init__(self) -> None:
        self._room: Optional[Room] = None
        self._fan: Optional[Fan] = None
        self._coil: Optional[Coil] = None
        self._time_step_s: Optional[float] = None
        self._disturbances: List[Disturbance] = []
        self._simulation_time_s: float = 0.0
        # Phase 2: optional compressor and energy accounting (Req 11.6).
        self._compressor: Optional[Compressor] = None
        self._energy_consumed_j: float = 0.0
        self._power_draw_w: float = 0.0
        self._last_step: Optional[StepRecord] = None
        # Fan flow requested during the run, applied at the next step (Req 3.8).
        self._pending_fan_flow: Optional[float] = None

    # ------------------------------------------------------------------
    # Configuration (Task 8)
    # ------------------------------------------------------------------

    def configure(
        self,
        room: RoomConfig,
        fan: FanConfig,
        coil: CoilConfig,
        time_step: TimeStepConfig,
    ) -> ValidationResult[RoomState]:
        """Validate the configuration and (re)build Room, Fan and Coil.

        Validation is all-or-nothing: on the first violation no component is
        created or replaced and the prior valid state is retained
        (Requirements 1.2-1.6, 3.2-3.4, 4.8, 7.3). On success a new run
        starts: simulation time, energy consumed and power draw are reset to
        ``0.0`` (Requirement 11.6). Active disturbances and any configured
        Compressor are kept.

        The initial humidity is a concentration in g/m^3; the room is created
        with ``initial_humidity_gm3 * volume_m3`` grams, capped at saturation.

        Returns:
            A successful result carrying the new :class:`RoomState`, or the
            field-specific configuration error.
        """
        for error in (
            _validate_room(room),
            _validate_fan(fan),
            _validate_coil(coil),
            _validate_duration(time_step.duration_s),
        ):
            if error is not None:
                return error

        self._room = Room(
            float(room.volume_m3),
            float(room.temperature_c),
            float(room.initial_humidity_gm3) * float(room.volume_m3),
        )
        self._fan = Fan(float(fan.flow_rate_m3_per_s))
        self._coil = Coil(
            float(coil.surface_temperature_c),
            float(coil.condensation_capacity_g_per_s),
            float(coil.effectiveness),
        )
        self._time_step_s = float(time_step.duration_s)
        self._pending_fan_flow = None
        self._simulation_time_s = 0.0
        self._energy_consumed_j = 0.0
        self._power_draw_w = 0.0
        self._last_step = None
        return ValidationResult.ok(self.room_state())

    @property
    def is_configured(self) -> bool:
        """Whether :meth:`configure` has succeeded at least once."""
        return self._room is not None

    @property
    def room(self) -> Optional[Room]:
        """The configured Room, or ``None`` before configuration."""
        return self._room

    @property
    def fan(self) -> Optional[Fan]:
        """The configured Fan, or ``None`` before configuration (Req 3.7)."""
        return self._fan

    @property
    def coil(self) -> Optional[Coil]:
        """The configured Coil, or ``None`` before configuration."""
        return self._coil

    @property
    def time_step(self) -> Optional[float]:
        """The configured Time_Step duration in seconds."""
        return self._time_step_s

    @property
    def simulation_time(self) -> float:
        """Elapsed simulated seconds since the run started (Req 7.2)."""
        return self._simulation_time_s

    def room_state(self) -> Optional[RoomState]:
        """Current absolute and relative humidity (Req 1.7-1.9, 2.7, 7.5).

        Returns ``None`` before the Simulator is configured.
        """
        room = self._room
        if room is None:
            return None
        capacity = psychrometrics.saturation_content_grams(
            room.temperature, room.volume
        )
        return RoomState(
            absolute_humidity_grams=room.absolute_humidity_grams,
            relative_humidity_pct=room.relative_humidity(),
            temperature_c=room.temperature,
            volume_m3=room.volume,
            relative_humidity_derivable=capacity > 0.0,
        )

    # ------------------------------------------------------------------
    # Disturbance management (Task 9)
    # ------------------------------------------------------------------

    @property
    def active_disturbances(self) -> Tuple[Disturbance, ...]:
        """The disturbances currently in the active set (``0..100``)."""
        return tuple(self._disturbances)

    def add_disturbance(self, disturbance: Disturbance) -> ValidationResult[None]:
        """Add a disturbance, effective from the next step (Req 5.3).

        Any object exposing ``is_active()`` and ``contribution(dt)`` is
        accepted (Requirement 6.1). When it exposes a ``moisture_rate`` (as
        the built-in types do), the rate must be within ``[0.0, 1000.0]`` g/s
        (Requirement 5.7). At most 100 disturbances may be active
        (Requirements 5.6, 6.5). On error the active set is unchanged.
        """
        if not (
            callable(getattr(disturbance, "contribution", None))
            and callable(getattr(disturbance, "is_active", None))
        ):
            return _fail("disturbance", "disturbance invalid")
        if any(d is disturbance for d in self._disturbances):
            return _fail("disturbance", "disturbance already active")
        if len(self._disturbances) >= MAX_ACTIVE_DISTURBANCES:
            return _fail("disturbance", "maximum active disturbances reached")
        validate = getattr(disturbance, "validate", None)
        if callable(validate):
            check = validate()
            if check.is_error:
                return check
        if hasattr(disturbance, "moisture_rate"):
            rate = disturbance.moisture_rate  # type: ignore[attr-defined]
            if not is_real_number(rate) or not (
                MOISTURE_RATE_MIN_G_PER_S <= rate <= MOISTURE_RATE_MAX_G_PER_S
            ):
                return _fail("moistureRate", "moisture rate out of range")
        self._disturbances.append(disturbance)
        return ValidationResult.ok()

    def remove_disturbance(self, disturbance: Disturbance) -> ValidationResult[None]:
        """Remove an active disturbance, effective from the next step.

        Returns a "disturbance not found" error and leaves the active set
        unchanged when ``disturbance`` is not active (Requirements 5.4, 5.8).
        """
        for index, candidate in enumerate(self._disturbances):
            if candidate is disturbance:
                del self._disturbances[index]
                return ValidationResult.ok()
        return _fail("disturbance", "disturbance not found")

    # ------------------------------------------------------------------
    # Compressor (Tasks 16, 18)
    # ------------------------------------------------------------------

    @property
    def compressor(self) -> Optional[Compressor]:
        """The configured Compressor, or ``None`` (Phase 1 behavior)."""
        return self._compressor

    def configure_compressor(
        self,
        cop: Union[float, CopTable],
        setpoint_temperature_c: float,
        max_power_w: float = DEFAULT_MAX_POWER_W,
        defrost: Optional[DefrostSettings] = DefrostSettings(),
        mode: str = "on_off",
        coil_heat_capacity_j_per_k: float = DEFAULT_COIL_HEAT_CAPACITY_J_PER_K,
        still_air_w_per_k: float = DEFAULT_STILL_AIR_W_PER_K,
    ) -> ValidationResult[Compressor]:
        """Validate and attach the optional Compressor (Req 8.1-8.6, 8.10).

        ``cop`` is a fixed number or a :class:`CopTable` (COP as a function
        of coil and optionally room temperature). ``max_power_w`` is the
        maximum electrical power absorbed; the cooling available to the coil
        is ``COP(T_coil) * max_power_w``. ``defrost`` sets the frost model and
        defrost cycle (Req 17); ``None`` lets the coil go below 0 deg C without
        frosting, as in Phase 2.

        ``mode`` is the compressor type (Req 18). ``"on_off"`` (default): a
        fixed-speed compressor at full power whenever it runs; the coil
        temperature is not set but follows from its heat balance (heat
        capacity ``coil_heat_capacity_j_per_k``), so it depends only on the
        compressor state, the fan and the room; ``setpoint_temperature_c`` is
        then not used. ``"modulating"``: a variable-capacity compressor that
        holds the coil at the setpoint when it can (Phase 2 behavior).

        On error the previous state (including any existing Compressor) is
        retained. A successful (re)configuration creates a new Compressor in
        the ``off`` state (Requirement 8.8). Adding it to a running simulation
        leaves humidity, temperature, simulation time and energy unchanged;
        coupling and energy accounting begin with the next step
        (Requirement 12.5).
        """
        result = Compressor.create(
            cop, setpoint_temperature_c, max_power_w, defrost, mode, coil_heat_capacity_j_per_k, still_air_w_per_k
        )
        if result.is_ok:
            self._compressor = result.value
        return result

    def set_fan_flow(self, flow_rate_m3_per_s: float) -> ValidationResult[None]:
        """Change the fan flow during the run, from the next step (Req 3.8).

        Validated like the configured flow (``[0.0, 10.0]`` m^3/s); on error
        the current fan is kept. Used by control algorithms to switch fan
        speeds while the simulation runs.
        """
        if self._fan is None:
            return _fail("simulator", "simulator not configured")
        error = _validate_fan(FanConfig(flow_rate_m3_per_s))
        if error is not None:
            return error
        self._pending_fan_flow = float(flow_rate_m3_per_s)
        return ValidationResult.ok()

    def set_compressor_state(
        self, state: Union[CompressorState, str]
    ) -> ValidationResult[None]:
        """Request ``running`` or ``off`` from the next step (Requirement 9.5)."""
        if self._compressor is None:
            return _fail("compressorState", "compressor not configured")
        try:
            new_state = CompressorState(state)
        except ValueError:
            return _fail("compressorState", "compressor state invalid")
        self._compressor.set_state(new_state)
        return ValidationResult.ok()

    @property
    def energy_consumed_joules(self) -> float:
        """Cumulative electrical energy of the run, ``>= 0.0`` (Req 11.1-11.3)."""
        return self._energy_consumed_j

    @property
    def power_draw_watts(self) -> float:
        """Power drawn during the last completed step, ``>= 0.0`` (Req 11.4-11.8)."""
        return self._power_draw_w

    @property
    def last_step(self) -> Optional[StepRecord]:
        """Breakdown of the most recently completed step, if any."""
        return self._last_step

    # ------------------------------------------------------------------
    # Step engine (Task 11)
    # ------------------------------------------------------------------

    def advance(
        self, dt: Optional[float] = None, n: int = 1
    ) -> ValidationResult[RoomState]:
        """Advance the simulation by ``n`` sequential steps of ``dt`` seconds.

        ``dt`` defaults to the configured Time_Step duration. Both inputs are
        validated before any step runs, so a rejected request leaves the
        simulation time and room humidity unchanged (Requirements 7.3, 7.6,
        7.7).

        Returns:
            A successful result carrying the updated :class:`RoomState`, or an
            input error.
        """
        if self._room is None:
            return _fail("simulator", "simulator not configured")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            return _fail("advanceCount", "time step count invalid")
        step_dt = self._time_step_s if dt is None else dt
        error = _validate_duration(step_dt)
        if error is not None:
            return error
        for _ in range(n):
            self._step(float(step_dt))  # type: ignore[arg-type]
        return ValidationResult.ok(self.room_state())

    def _sum_contributions(
        self, dt: float
    ) -> Tuple[float, Tuple[DisturbanceError, ...]]:
        """Sum valid active-disturbance contributions (Req 5.5, 6.4, 6.6, 16.2).

        A disturbance exposing ``exchange_grams(room, dt)`` (an air exchange
        such as an open window) returns a signed mass computed from the room
        state at the start of the step; every other disturbance must return a
        non-negative ``contribution(dt)``.
        """
        total = 0.0
        errors: List[DisturbanceError] = []
        for disturbance in self._disturbances:
            exchange = getattr(disturbance, "exchange_grams", None)
            try:
                if not disturbance.is_active():
                    continue
                value = exchange(self._room, dt) if callable(exchange) else disturbance.contribution(dt)
            except Exception as exc:  # noqa: BLE001 - caller-supplied code.
                errors.append(
                    DisturbanceError(disturbance, exc, "disturbance raised an error")
                )
                continue
            signed_ok = callable(exchange)
            if not is_real_number(value) or math.isinf(value) or (value < 0.0 and not signed_ok):
                errors.append(
                    DisturbanceError(
                        disturbance, value, "invalid disturbance contribution"
                    )
                )
                continue
            total += value
        return total, tuple(errors)

    def _step(self, dt: float) -> None:
        """Run one time step in the mandated order (Req 7.1)."""
        if self._pending_fan_flow is not None:
            self._fan = Fan(self._pending_fan_flow)
            self._pending_fan_flow = None
        room, fan, coil = self._room, self._fan, self._coil
        assert room is not None and fan is not None and coil is not None
        compressor = self._compressor
        frost = compressor.defrost if compressor is not None else None
        coil.frost_halving_g = frost.frost_halving_g if frost is not None else None

        # Phase 2 hook (a): drive the coil from the compressor state.
        running = False
        defrosting = False
        on_off = compressor is not None and compressor.mode == "on_off"
        if compressor is not None:
            compressor.promote_pending_state()
            running = compressor.running
            defrosting = compressor.defrosting
            if not on_off:
                # Modulating unit: the coil is driven to the setpoint (or to
                # room temperature when off). An on/off unit's coil keeps its
                # own temperature, set by the heat balance (Req 18).
                coil.set_surface_temperature(
                    compressor.coil_surface_temperature(room.temperature)
                )

        # Frost melting on a modulating unit while it is not running: the
        # coil sits at 0 deg C while ice remains; part of the melt
        # re-evaporates into the room (Req 17.2, 17.4). On/off units melt
        # frost inside their coil heat balance.
        melted = reevaporated = 0.0
        if frost is not None and not on_off and not running and coil.frost_g > 0.0:
            melted, reevaporated = self._melt_frost(coil, fan, room, compressor, dt)

        humidity_before = room.exact_absolute_humidity_grams
        added, errors = self._sum_contributions(dt)
        added += reevaporated
        # Room.set_absolute_humidity caps at saturation (Req 5.9).
        room.set_absolute_humidity(humidity_before + added)
        humidity_after_additions = room.exact_absolute_humidity_grams

        moved = fan.moved_air_volume(dt)
        heat = 0.0
        electrical = 0.0
        power_limited = False
        step_cop: Optional[float] = None
        state: Optional[CompressorState] = None
        if on_off:
            # On/off unit: full power while running, coil temperature from
            # its heat balance, frost and melting included (Req 18).
            result = compressor.on_off_step(
                coil, room.volume, room.temperature, room.specific_humidity(), fan.flow_rate, dt, running
            )
            removed = result.removed_grams
            heat = result.heat_extracted_joules
            electrical = result.electrical_joules
            power_limited = running  # a fixed-speed compressor always runs flat out
            step_cop = result.cop if running else None
            coil.set_surface_temperature(result.coil_surface_temperature_c)
            melted = result.melted_grams
            if frost is not None and melted > 0.0:
                reevaporated = melted * frost.reevaporation_fraction
        elif running:
            # Phase 2: the power-limited compressor decides the coil
            # temperature it can sustain, and with it the removal and the
            # heat extracted (Req 9.1, 9.4, 9.6, 10.1-10.5).
            result = compressor.run_step(
                coil, room.volume, room.temperature, room.specific_humidity(), fan.flow_rate, dt
            )
            removed = result.removed_grams
            heat = result.heat_extracted_joules
            electrical = result.electrical_joules
            power_limited = result.power_limited
            step_cop = result.cop
            coil.set_surface_temperature(result.coil_surface_temperature_c)
            if frost is not None and result.coil_surface_temperature_c < 0.0:
                coil.frost_g += removed  # below 0 deg C the water freezes on the coil
        elif frost is not None and coil.frost_g > 0.0:
            removed = 0.0  # melting ice: no active cooling, nothing condenses
            coil.set_surface_temperature(0.0)
        else:
            removed = coil.removal(moved, room, dt)
        # ... and clamps at 0.0 (Req 4.6, 4.7, 7.4).
        room.set_absolute_humidity(humidity_after_additions - removed)
        if on_off and reevaporated > 0.0:
            # Melt water re-evaporating from the coil during this step.
            room.set_absolute_humidity(room.exact_absolute_humidity_grams + reevaporated)
            added += reevaporated

        # Phase 2 hook (b): energy accounting (Req 10, 11).
        if compressor is not None:
            state = compressor.state
            self._energy_consumed_j += electrical
            self._power_draw_w = electrical / dt if dt > 0.0 else 0.0
            compressor.update_defrost(running, coil.surface_temperature, dt)

        self._simulation_time_s += dt
        self._last_step = StepRecord(
            dt=dt,
            humidity_before_grams=humidity_before,
            added_grams=added,
            humidity_after_additions_grams=humidity_after_additions,
            moved_air_volume_m3=moved,
            coil_surface_temperature_c=coil.surface_temperature,
            removed_grams=removed,
            humidity_after_grams=room.exact_absolute_humidity_grams,
            compressor_state=state,
            heat_extracted_joules=heat,
            electrical_joules=electrical,
            power_limited=power_limited,
            cop=step_cop,
            disturbance_errors=errors,
            defrosting=defrosting,
            compressor_running=running,
            frost_g=coil.frost_g,
            melted_g=melted,
            reevaporated_g=reevaporated,
        )

    @staticmethod
    def _melt_frost(coil: Coil, fan: Fan, room: Room, compressor: Compressor, dt: float):
        """Melt ice on a coil at 0 deg C with the room air; return (melted, re-evaporated)."""
        delta_t = room.temperature  # room minus the 0 deg C ice surface
        if delta_t <= 0.0:
            return 0.0, 0.0
        joules = psychrometrics.sensible_heat_joules(
            fan.moved_air_volume(dt), room.temperature, 0.0, coil.active_effectiveness
        ) + compressor.still_air_w_per_k * delta_t * dt
        melted = min(coil.frost_g, joules / psychrometrics.LATENT_HEAT_OF_FUSION_J_PER_G)
        coil.frost_g -= melted
        if coil.frost_g < 1e-9:
            coil.frost_g = 0.0
        return melted, melted * compressor.defrost.reevaporation_fraction
