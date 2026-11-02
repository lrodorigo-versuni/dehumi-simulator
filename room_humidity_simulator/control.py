"""Closed-loop control: test a fan / compressor control algorithm.

Your algorithm is any object with a ``step(measurements, dt) -> Commands``
method (see :class:`Controller`). :class:`ClosedLoop` runs it against the
simulator::

    every time step:
        measurements = sensors(room state)      # noise, resolution, lag
        commands     = controller.step(measurements, dt)
        apply commands (compressor on/off, fan speed index)
                     through the compressor protections (min on/off times)
        simulator.advance()

and returns the trajectory plus performance KPIs (comfort, energy, wear).

The actuators match a typical dehumidifier: an **on/off compressor** and a
**fan with a few fixed speeds** (:class:`FanSpeed`, each with its flow and
electrical power). The controller is called once per simulator time step,
so the time step is the control period.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment]

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.compressor import CompressorState
from room_humidity_simulator.simulator import Simulator


@dataclass(frozen=True)
class FanSpeed:
    """One selectable fan speed."""

    name: str
    flow_m3_per_h: float
    power_w: float = 0.0


DEFAULT_FAN_SPEEDS = (
    FanSpeed("off", 0.0, 0.0),
    FanSpeed("low", 90.0, 15.0),
    FanSpeed("medium", 150.0, 25.0),
    FanSpeed("high", 220.0, 40.0),
)


@dataclass(frozen=True)
class Measurements:
    """What the controller sees at the start of a step.

    Attributes:
        time_s: Simulation time in seconds.
        relative_humidity_pct: Measured room RH (after the sensor model).
        room_temperature_c: Measured room temperature.
        coil_temperature_c: Coil (evaporator) temperature at the end of the
            previous step.
        dew_point_c: Dew point computed from the measured RH, or ``None``.
        compressor_on: Actual compressor state (after protections).
        fan_speed: Actual fan speed index into the fan speeds.
        compressor_power_w: Compressor electrical power in the last step.
        compressor_can_switch: Whether a compressor change would be accepted
            now (minimum on/off time elapsed).
        target_rh_pct: The target RH of the run (from :class:`ComfortBand`).
        defrosting: The unit is defrosting: the compressor stays off
            whatever is requested, and the fan runs at least at its lowest
            speed, until the coil is clear.
    """

    time_s: float
    relative_humidity_pct: float
    room_temperature_c: float
    coil_temperature_c: float
    dew_point_c: Optional[float]
    compressor_on: bool
    fan_speed: int
    compressor_power_w: float
    compressor_can_switch: bool
    target_rh_pct: float
    defrosting: bool = False


@dataclass(frozen=True)
class Commands:
    """What the controller asks for the next step.

    Attributes:
        compressor_on: Request the compressor on (``True``) or off.
        fan_speed: Index into the fan speeds (0 = first, usually "off").
    """

    compressor_on: bool
    fan_speed: int


class Controller(Protocol):
    """Interface your algorithm implements.

    Optional: a ``reset(context)`` method, called once before the run with a
    dict holding ``fan_speeds``, ``time_step_s``, ``band`` and
    ``constraints``; and a class attribute ``PARAMETERS`` (``{name:
    default}``) listing tunable numbers, which the web UI shows as fields
    and passes to the constructor as keyword arguments.
    """

    def step(self, measurements: Measurements, dt: float) -> Commands:  # pragma: no cover
        ...


@dataclass(frozen=True)
class SensorModel:
    """RH sensor seen by the controller (the KPIs use the true RH).

    Attributes:
        noise_std_pct: Gaussian noise standard deviation, in % RH.
        resolution_pct: Quantization step, in % RH (0 = none).
        time_constant_s: First-order lag of the sensor (0 = instantaneous).
        seed: RNG seed, so runs are reproducible and comparable.
    """

    noise_std_pct: float = 0.0
    resolution_pct: float = 0.0
    time_constant_s: float = 0.0
    seed: int = 1


@dataclass(frozen=True)
class CompressorProtection:
    """Compressor protections enforced like a real control board.

    A request that would violate them is ignored (and counted).

    Attributes:
        min_on_s: Minimum run time once started.
        min_off_s: Minimum rest time before a restart.
        require_fan: Never run the compressor without airflow (it would ice
            the coil in a real unit): a start with the fan off is refused, and
            while the compressor runs the fan is kept at least at its lowest
            speed.
    """

    min_on_s: float = 180.0
    min_off_s: float = 180.0
    require_fan: bool = True


@dataclass(frozen=True)
class ComfortBand:
    """Target RH and the band counted as comfortable."""

    target_pct: float = 50.0
    low_pct: float = 45.0
    high_pct: float = 55.0


class ControllerError(Exception):
    """The controller returned something unusable."""


@dataclass
class ClosedLoopResult:
    """Trajectory (one entry per step, plus the initial state) and KPIs."""

    series: Dict[str, List[Any]] = field(default_factory=dict)
    kpis: Dict[str, Optional[float]] = field(default_factory=dict)


class _Sensor:
    def __init__(self, model: SensorModel) -> None:
        self.model = model
        self.rng = random.Random(model.seed)
        self.state: Optional[float] = None

    def read(self, true_rh: float, dt: float) -> float:
        m = self.model
        if self.state is None or m.time_constant_s <= 0.0:
            self.state = true_rh
        else:
            self.state += (true_rh - self.state) * -math.expm1(-dt / m.time_constant_s)
        value = self.state
        if m.noise_std_pct > 0.0:
            value += self.rng.gauss(0.0, m.noise_std_pct)
        if m.resolution_pct > 0.0:
            value = round(value / m.resolution_pct) * m.resolution_pct
        return min(max(value, 0.0), 100.0)


def _dew_point_from_rh(rh_pct: float, temperature_c: float) -> Optional[float]:
    if rh_pct <= 0.0:
        return None
    q = rh_pct / 100.0 * psychrometrics.saturation_content_density(temperature_c)
    return psychrometrics.dew_point(q, temperature_c)


class ClosedLoop:
    """Run a controller against a configured :class:`Simulator`.

    Args:
        simulator: A configured simulator with a compressor attached (its
            configured fan flow is replaced by the controller's choices).
        controller: Your algorithm (see :class:`Controller`).
        fan_speeds: The selectable fan speeds; the controller picks an index.
        sensor: RH sensor model.
        protection: Compressor protections.
        band: Comfort band used by the KPIs (and passed to the controller).
        before_step: Optional hook ``f(simulator, time_s)`` called before each
            step, e.g. to add/remove disturbances on a timeline.
    """

    def __init__(
        self,
        simulator: Simulator,
        controller: Controller,
        fan_speeds: Sequence[FanSpeed] = DEFAULT_FAN_SPEEDS,
        sensor: SensorModel = SensorModel(),
        protection: CompressorProtection = CompressorProtection(),
        band: ComfortBand = ComfortBand(),
        before_step: Optional[Callable[[Simulator, float], None]] = None,
    ) -> None:
        if simulator.room is None:
            raise ValueError("simulator not configured")
        if simulator.compressor is None:
            raise ValueError("closed-loop control needs a compressor (configure_compressor)")
        if not fan_speeds:
            raise ValueError("at least one fan speed is required")
        self.sim = simulator
        self.controller = controller
        self.fan_speeds = tuple(fan_speeds)
        self.sensor = sensor
        self.protection = protection
        self.band = band
        self.before_step = before_step
        running = [i for i, f in enumerate(self.fan_speeds) if f.flow_m3_per_h > 0.0]
        if protection.require_fan and not running:
            raise ValueError("require_fan needs at least one fan speed with airflow")
        self._lowest_running_speed = (
            min(running, key=lambda i: self.fan_speeds[i].flow_m3_per_h) if running else 0
        )

    def _validate(self, commands: Any) -> Commands:
        if not isinstance(commands, Commands):
            raise ControllerError(
                "step() must return Commands(compressor_on=..., fan_speed=...), got {!r}".format(commands)
            )
        speed = commands.fan_speed
        if isinstance(speed, bool) or not isinstance(speed, int) or not 0 <= speed < len(self.fan_speeds):
            raise ControllerError(
                "fan_speed must be an int in [0, {}], got {!r}".format(len(self.fan_speeds) - 1, speed)
            )
        return Commands(bool(commands.compressor_on), speed)

    def run(self, steps: int) -> ClosedLoopResult:
        sim, prot = self.sim, self.protection
        dt = sim.time_step
        sensor = _Sensor(self.sensor)
        if hasattr(self.controller, "reset"):
            self.controller.reset(
                {"fan_speeds": self.fan_speeds, "time_step_s": dt, "band": self.band, "constraints": prot}
            )
        compressor_on = False
        fan_speed = 0
        last_switch = -math.inf  # time of the last actual compressor change
        sim.set_compressor_state("off")
        sim.set_fan_flow(self.fan_speeds[0].flow_m3_per_h / 3600.0)

        keys = (
            "t_s", "rh_true_pct", "rh_measured_pct", "absolute_humidity_g", "coil_temperature_c",
            "compressor_on", "fan_speed", "fan_flow_m3_per_h", "compressor_power_w", "fan_power_w",
            "energy_kwh", "removed_g", "added_g", "cop", "blocked", "power_limited",
            "defrosting", "frost_g",
        )
        s: Dict[str, List[Any]] = {k: [] for k in keys}
        energy_j = 0.0
        last_power = 0.0
        blocked_total = 0
        fan_without_need = 0

        def record(rh_meas, blocked, removed, added, cop, fan_power, limited=False, running=False, defrosting=False):
            state = sim.room_state()
            s["t_s"].append(sim.simulation_time)
            s["rh_true_pct"].append(state.relative_humidity_pct)
            s["rh_measured_pct"].append(rh_meas)
            s["absolute_humidity_g"].append(state.absolute_humidity_grams)
            s["coil_temperature_c"].append(sim.coil.surface_temperature)
            s["compressor_on"].append(running)
            s["fan_speed"].append(fan_speed)
            s["fan_flow_m3_per_h"].append(self.fan_speeds[fan_speed].flow_m3_per_h)
            s["compressor_power_w"].append(last_power)
            s["fan_power_w"].append(fan_power)
            s["energy_kwh"].append(energy_j / 3.6e6)
            s["removed_g"].append(removed)
            s["added_g"].append(added)
            s["cop"].append(cop)
            s["blocked"].append(blocked)
            s["power_limited"].append(limited)
            s["defrosting"].append(defrosting)
            s["frost_g"].append(sim.coil.frost_g)

        record(sim.room_state().relative_humidity_pct, False, 0.0, 0.0, None, 0.0)
        for _ in range(steps):
            t = sim.simulation_time
            if self.before_step is not None:
                self.before_step(sim, t)
            room = sim.room
            rh_meas = sensor.read(sim.room_state().relative_humidity_pct, dt)
            can_switch = (t - last_switch) >= (prot.min_on_s if compressor_on else prot.min_off_s)
            meas = Measurements(
                time_s=t,
                relative_humidity_pct=rh_meas,
                room_temperature_c=room.temperature,
                coil_temperature_c=sim.coil.surface_temperature,
                dew_point_c=_dew_point_from_rh(rh_meas, room.temperature),
                compressor_on=compressor_on,
                fan_speed=fan_speed,
                compressor_power_w=last_power,
                compressor_can_switch=can_switch,
                target_rh_pct=self.band.target_pct,
                defrosting=sim.compressor.defrosting,
            )
            cmd = self._validate(self.controller.step(meas, dt))

            speed = cmd.fan_speed
            want_on = cmd.compressor_on
            blocked = False

            def no_airflow(index: int) -> bool:
                return prot.require_fan and self.fan_speeds[index].flow_m3_per_h <= 0.0

            if want_on and not compressor_on and no_airflow(speed):
                want_on, blocked = False, True  # never start without airflow
            if want_on != compressor_on:
                if can_switch:
                    compressor_on = want_on
                    last_switch = t
                else:
                    blocked = True  # inside the minimum on/off time
            if (compressor_on or sim.compressor.defrosting) and no_airflow(speed):
                # Like a control board: while the compressor runs, the fan
                # runs at least at its lowest speed.
                speed = self._lowest_running_speed
                blocked = True
            fan_speed = speed
            blocked_total += blocked
            sim.set_fan_flow(self.fan_speeds[fan_speed].flow_m3_per_h / 3600.0)
            sim.set_compressor_state(CompressorState.RUNNING if compressor_on else CompressorState.OFF)

            sim.advance()
            step = sim.last_step
            fan_power = self.fan_speeds[fan_speed].power_w
            energy_j += step.electrical_joules + fan_power * dt
            last_power = sim.power_draw_watts
            fan_without_need += (not step.compressor_running) and not step.defrosting and fan_speed > 0
            record(
                rh_meas, blocked, step.removed_grams, step.added_grams, step.cop, fan_power,
                step.power_limited, step.compressor_running, step.defrosting,
            )

        self.result = ClosedLoopResult(series=s, kpis=self._kpis(s, dt, steps, blocked_total, fan_without_need))
        return self.result

    def _kpis(self, s, dt, steps, blocked, fan_only) -> Dict[str, Optional[float]]:
        band = self.band
        rh = s["rh_true_pct"][1:]  # state after each step
        hours = steps * dt / 3600.0
        inside = sum(band.low_pct <= v <= band.high_pct for v in rh)
        above = sum(v > band.high_pct for v in rh)
        below = sum(v < band.low_pct for v in rh)
        err = [v - band.target_pct for v in rh]
        excess = [max(v - band.high_pct, 0.0) for v in rh]
        removed = sum(s["removed_g"])
        energy = s["energy_kwh"][-1]
        compressor_kwh = sum(s["compressor_power_w"][1:]) * dt / 3.6e6
        on = s["compressor_on"]
        on_steps = sum(on[1:])
        # Actual starts, including the automatic restarts after a defrost.
        starts = sum(1 for i in range(1, len(on)) if on[i] and not on[i - 1])
        defrost = s["defrosting"]
        defrosts = sum(1 for i in range(1, len(defrost)) if defrost[i] and not defrost[i - 1])
        n = max(len(rh), 1)
        return {
            "time_in_band_pct": 100.0 * inside / n,
            "time_above_band_pct": 100.0 * above / n,
            "time_below_band_pct": 100.0 * below / n,
            "mean_rh_pct": sum(rh) / n,
            "max_rh_pct": max(rh) if rh else None,
            "rms_error_pct": math.sqrt(sum(e * e for e in err) / n),
            "rh_excess_pct_h": sum(excess) * dt / 3600.0,
            "energy_kwh": energy,
            "compressor_energy_kwh": compressor_kwh,
            "fan_energy_kwh": energy - compressor_kwh,
            "water_removed_g": removed,
            "grams_per_kwh": removed / energy if energy > 0 else None,
            "compressor_starts": float(starts),
            "starts_per_hour": starts / hours if hours > 0 else None,
            "compressor_on_pct": 100.0 * on_steps / n,
            "fan_only_pct": 100.0 * fan_only / n,
            "blocked_commands": float(blocked),
            "defrost_cycles": float(defrosts),
            "defrost_time_pct": 100.0 * sum(defrost[1:]) / n,
            "max_frost_g": max(s["frost_g"]),
        }
