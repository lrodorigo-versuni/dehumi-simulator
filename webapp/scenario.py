"""Scenario runner behind the web UI.

Turns a JSON-like scenario (room, fan, coil, optional compressor, a timeline
of disturbances and compressor on/off events) into time series ready to
chart, plus a parameter sweep. Pure functions over plain dicts: no HTTP here,
so everything is testable directly.

Errors never raise past this module: invalid input yields
``{"error": {"field": ..., "message": ...}}`` like the simulator's own
:class:`~room_humidity_simulator.results.ConfigError`.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from room_humidity_simulator import (
    Boiling_Water_Disturbance,
    CoilConfig,
    CopTable,
    DefrostSettings,
    FanConfig,
    Open_Window_Disturbance,
    Person_Disturbance,
    RoomConfig,
    Simulator,
    TimeStepConfig,
    psychrometrics,
)

MAX_STEPS = 20000
MAX_POINTS = 1500
MAX_SWEEP_POINTS = 25

# 1 g/s of condensate = 86.4 L/day (1 L of water ~ 1000 g).
L_PER_DAY_PER_G_PER_S = 86.4

DEFAULT_SCENARIO: Dict[str, Any] = {
    "room": {"volume_m3": 20.0, "temperature_c": 25.0, "initial_humidity_gm3": 12.0},
    "fan": {"flow_rate_m3_per_h": 180.0},
    "coil": {
        "surface_temperature_c": 25.0,
        "max_condensation_l_per_day": 43.2,  # 0.5 g/s
        "effectiveness": 0.85,
    },
    "compressor": {
        "enabled": True,
        "mode": "on_off",  # fixed-speed: coil temperature follows from the heat balance
        "coil_heat_capacity_j_per_k": 1000.0,
        "setpoint_temperature_c": 1.0,  # modulating mode only
        "max_power_w": 400.0,
        "cop_mode": "table",
        "defrost": {
            "enabled": True,
            "start_below_c": -1.0,
            "start_delay_s": 300.0,
            "end_above_c": 3.0,
            "min_duration_s": 120.0,
            "max_duration_s": 900.0,
            "frost_halving_g": 300.0,
            "reevaporation_pct": 30.0,
        },
        "cop": 2.5,  # fixed-COP mode; ~ the table at a 2-5 deg C coil, 25 deg C room
        "cop_table": {
            "coil_temperatures_c": [2, 5, 10, 15, 20, 25],
            "room_temperatures_c": [15, 25, 35],
            # 0.40 x Carnot, evaporating 5 K below the coil, condensing 20 K
            # above the room: COP = 0.4 * Te / (Tc - Te), temperatures in K.
            "cops": [
                [2.8, 3.1, 3.7, 4.5, 5.8, 7.8],
                [2.3, 2.4, 2.8, 3.2, 3.8, 4.7],
                [1.9, 2.0, 2.2, 2.5, 2.9, 3.4],
            ],
        },
    },
    "disturbances": [
        {"kind": "person", "moisture_rate_g_per_s": 0.015, "start_min": 0, "end_min": None},
        {"kind": "person", "moisture_rate_g_per_s": 0.015, "start_min": 0, "end_min": None},
        {"kind": "boiling_water", "moisture_rate_g_per_s": 0.2, "start_min": 0, "end_min": 20},
    ],
    "compressor_events": [{"at_min": 30, "state": "running"}],
    "control": None,  # filled from webapp.control_runner.DEFAULT_CONTROL
    "time_step_s": 60.0,
    "duration_min": 90.0,
}

SWEEP_PARAMETERS = {
    "fan_flow_m3_per_h": ("Fan flow", "m³/h"),
    "setpoint_c": ("Coil setpoint", "°C"),
    "max_power_w": ("Max compressor power", "W"),
    "effectiveness": ("Coil effectiveness", ""),
}


# Simulator error fields -> scenario paths (so the UI can highlight inputs).
FIELD_PATHS = {
    "volumeM3": "room.volume_m3",
    "temperatureC": "room.temperature_c",
    "initialHumidityGm3": "room.initial_humidity_gm3",
    "flowRateM3PerS": "fan.flow_rate_m3_per_h",
    "surfaceTemperatureC": "coil.surface_temperature_c",
    "condensationCapacityGPerS": "coil.max_condensation_l_per_day",
    "effectiveness": "coil.effectiveness",
    "durationS": "time_step_s",
    "cop": "compressor.cop",
    "setpointTemperatureC": "compressor.setpoint_temperature_c",
    "maxPowerW": "compressor.max_power_w",
    "mode": "compressor.mode",
    "coilHeatCapacityJPerK": "compressor.coil_heat_capacity_j_per_k",
    "copTable": "compressor.cop_table",
    "moistureRate": "disturbances",
}


WINDOW_FIELDS = {
    "airflowM3PerH": "airflow_m3_per_h",
    "outdoorTemperatureC": "outdoor_temperature_c",
    "outdoorRhPct": "outdoor_rh_pct",
    "gustiness": "gustiness_pct",
    "gustTimeS": "gust_time_s",
}


DEFROST_KEYS = ("frost_halving_g", "start_below_c", "start_delay_s", "end_above_c", "min_duration_s", "max_duration_s")
FIELD_PATHS.update({
    "frostHalvingG": "compressor.defrost.frost_halving_g",
    "defrostStartBelowC": "compressor.defrost.start_below_c",
    "defrostStartDelayS": "compressor.defrost.start_delay_s",
    "defrostEndAboveC": "compressor.defrost.end_above_c",
    "defrostMinS": "compressor.defrost.min_duration_s",
    "defrostMaxS": "compressor.defrost.max_duration_s",
    "reevaporationFraction": "compressor.defrost.reevaporation_pct",
})


class ScenarioError(Exception):
    """Invalid scenario input; carries a field and a message."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _error(field: str, message: str) -> Dict[str, Any]:
    return {"error": {"field": field, "message": message}}


def _num(value: Any, field: str, allow_none: bool = False) -> Optional[float]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        raise ScenarioError(field, "must be a number")
    return float(value)


def _get(d: Any, key: str, field: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise ScenarioError(field, "is missing")
    return d[key]


def _cop_spec(comp: Dict[str, Any]) -> Any:
    if comp.get("cop_mode", "fixed") == "table":
        t = _get(comp, "cop_table", "compressor.cop_table")
        rooms = t.get("room_temperatures_c") if isinstance(t, dict) else None
        if rooms is not None and len(rooms) == 0:
            rooms = None
        cops = _get(t, "cops", "compressor.cop_table.cops")
        if rooms is None and cops and isinstance(cops[0], list):
            cops = cops[0]  # a single-row table is 1D
        return CopTable(_get(t, "coil_temperatures_c", "compressor.cop_table"), cops, rooms)
    return _num(_get(comp, "cop", "compressor.cop"), "compressor.cop")


def _defrost_spec(d: Any) -> Optional[DefrostSettings]:
    """Frost model and defrost cycle from the scenario (absent = defaults)."""
    if d is None:
        return DefrostSettings()
    if not d.get("enabled", True):
        return None
    defaults = DefrostSettings()
    values = {}
    for key in DEFROST_KEYS:
        v = d.get(key, getattr(defaults, key))
        values[key] = _num(v, "compressor.defrost." + key)
    values["reevaporation_fraction"] = _num(
        d.get("reevaporation_pct", defaults.reevaporation_fraction * 100.0), "compressor.defrost.reevaporation_pct"
    ) / 100.0
    return DefrostSettings(**values)


def _build(scenario: Dict[str, Any]):
    """Validate the scenario and return (simulator, plan) or raise ScenarioError."""
    room = _get(scenario, "room", "room")
    fan = _get(scenario, "fan", "fan")
    coil = _get(scenario, "coil", "coil")
    dt = _num(_get(scenario, "time_step_s", "time_step_s"), "time_step_s")
    duration_min = _num(_get(scenario, "duration_min", "duration_min"), "duration_min")
    if duration_min <= 0:
        raise ScenarioError("duration_min", "must be greater than 0")
    if not dt or dt <= 0:
        raise ScenarioError("time_step_s", "time step duration out of range")
    steps = int(math.ceil(duration_min * 60.0 / dt))
    if steps > MAX_STEPS:
        raise ScenarioError(
            "duration_min", "too many steps ({}); use a longer time step".format(steps)
        )

    sim = Simulator()
    result = sim.configure(
        RoomConfig(
            _num(_get(room, "volume_m3", "room.volume_m3"), "room.volume_m3"),
            _num(_get(room, "temperature_c", "room.temperature_c"), "room.temperature_c"),
            _num(_get(room, "initial_humidity_gm3", "room.initial_humidity_gm3"), "room.initial_humidity_gm3"),
        ),
        FanConfig(_num(_get(fan, "flow_rate_m3_per_h", "fan.flow_rate_m3_per_h"), "fan.flow_rate_m3_per_h") / 3600.0),
        CoilConfig(
            _num(_get(coil, "surface_temperature_c", "coil.surface_temperature_c"), "coil.surface_temperature_c"),
            _num(_get(coil, "max_condensation_l_per_day", "coil.max_condensation_l_per_day"), "coil.max_condensation_l_per_day") / L_PER_DAY_PER_G_PER_S,
            _num(coil.get("effectiveness", 0.85), "coil.effectiveness"),
        ),
        TimeStepConfig(dt),
    )
    if result.is_error:
        raise ScenarioError(FIELD_PATHS.get(result.error.field, result.error.field), result.error.message)

    comp = scenario.get("compressor")
    if comp and comp.get("enabled", True):
        result = sim.configure_compressor(
            _cop_spec(comp),
            _num(_get(comp, "setpoint_temperature_c", "compressor.setpoint_temperature_c"), "compressor.setpoint_temperature_c"),
            _num(comp.get("max_power_w", 400.0), "compressor.max_power_w"),
            defrost=_defrost_spec(comp.get("defrost")),
            mode=comp.get("mode", "on_off"),
            coil_heat_capacity_j_per_k=_num(comp.get("coil_heat_capacity_j_per_k", 1000.0), "compressor.coil_heat_capacity_j_per_k"),
        )
        if result.is_error:
            raise ScenarioError(FIELD_PATHS.get(result.error.field, result.error.field), result.error.message)

    disturbances = []
    for i, d in enumerate(scenario.get("disturbances") or []):
        field = "disturbances[{}]".format(i)
        kind = d.get("kind")
        if kind == "window":
            # Each window gets its own reproducible gust sequence: the same
            # scenario always sees the same wind (fair A/B comparisons).
            obj = Open_Window_Disturbance(
                _num(d.get("airflow_m3_per_h"), field + ".airflow_m3_per_h"),
                _num(d.get("outdoor_temperature_c"), field + ".outdoor_temperature_c"),
                _num(d.get("outdoor_rh_pct"), field + ".outdoor_rh_pct"),
                gustiness=_num(d.get("gustiness_pct", 0.0), field + ".gustiness_pct") / 100.0,
                gust_time_s=_num(d.get("gust_time_s", 60.0), field + ".gust_time_s"),
                seed=int(_num(d.get("seed", i + 1), field + ".seed")),
            )
            check = obj.validate()
            if check.is_error:
                raise ScenarioError(field + "." + WINDOW_FIELDS.get(check.error.field, check.error.field), check.error.message)
        elif kind in ("person", "boiling_water"):
            rate = _num(d.get("moisture_rate_g_per_s"), field + ".moisture_rate_g_per_s")
            obj = Person_Disturbance(rate) if kind == "person" else Boiling_Water_Disturbance(rate)
        else:
            raise ScenarioError(field + ".kind", "must be 'person', 'boiling_water' or 'window'")
        start = _num(d.get("start_min", 0.0), field + ".start_min") * 60.0
        end = _num(d.get("end_min"), field + ".end_min", allow_none=True)
        end = None if end is None else end * 60.0
        disturbances.append({"obj": obj, "start": start, "end": end, "added": False, "field": field})

    events = []
    for i, e in enumerate(scenario.get("compressor_events") or []):
        field = "compressor_events[{}]".format(i)
        if e.get("state") not in ("running", "off"):
            raise ScenarioError(field + ".state", "must be 'running' or 'off'")
        events.append((_num(e.get("at_min"), field + ".at_min") * 60.0, e["state"]))
    events.sort(key=lambda ev: ev[0])
    return sim, {"steps": steps, "dt": dt, "disturbances": disturbances, "events": events}


def _apply_timeline(sim: Simulator, plan: Dict[str, Any], t: float) -> None:
    """Add/remove disturbances and switch the compressor for the step starting at ``t``."""
    eps = 1e-9
    for d in plan["disturbances"]:
        active = t >= d["start"] - eps and (d["end"] is None or t < d["end"] - eps)
        if active and not d["added"]:
            result = sim.add_disturbance(d["obj"])
            if result.is_error:
                raise ScenarioError(d["field"] + ".moisture_rate_g_per_s", result.error.message)
            d["added"] = True
        elif not active and d["added"]:
            sim.remove_disturbance(d["obj"])
            d["added"] = False
    if sim.compressor is not None:
        state = None
        for at, s in plan["events"]:
            if at <= t + eps:
                state = s
        if state is not None:
            sim.set_compressor_state(state)


def _dew_point(q: float, t: float) -> Optional[float]:
    if q <= 0.0:
        return None
    return psychrometrics.dew_point(q, t)


def run_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Run a scenario and return per-step series, a summary and the timeline.

    With ``control.mode == "controller"`` the compressor and fan are driven
    by a control algorithm instead of the on/off schedule (see
    :mod:`webapp.control_runner`).
    """
    control = scenario.get("control") or {}
    if control.get("mode") == "controller":
        from webapp.control_runner import ControlError, run_controlled

        try:
            return run_controlled(scenario, _build, _apply_timeline, MAX_POINTS)
        except (ScenarioError, ControlError) as exc:
            return _error(exc.field, exc.message)
    try:
        sim, plan = _build(scenario)
        dt, steps = plan["dt"], plan["steps"]
        room = sim.room
        stride = max(1, int(math.ceil(steps / MAX_POINTS)))
        keys = (
            "t_min", "absolute_humidity_g", "relative_humidity_pct", "added_g_per_min",
            "removed_g_per_min", "coil_temperature_c", "dew_point_c", "room_temperature_c",
            "power_w", "cop", "energy_kwh", "running", "power_limited", "defrosting", "frost_g",
        )
        series: Dict[str, List[Any]] = {k: [] for k in keys}
        first = sim.room_state()
        series["t_min"].append(0.0)
        series["absolute_humidity_g"].append(first.absolute_humidity_grams)
        series["relative_humidity_pct"].append(first.relative_humidity_pct)
        for k in ("added_g_per_min", "removed_g_per_min", "power_w", "energy_kwh"):
            series[k].append(0.0)
        series["coil_temperature_c"].append(sim.coil.surface_temperature)
        series["dew_point_c"].append(_dew_point(room.specific_humidity(), room.temperature))
        series["room_temperature_c"].append(room.temperature)
        series["cop"].append(None)
        series["running"].append(False)
        series["power_limited"].append(False)
        series["defrosting"].append(False)
        series["frost_g"].append(sim.coil.frost_g)

        total_added = total_removed = 0.0
        limited_steps = running_steps = 0
        acc_added = acc_removed = acc_energy = 0.0
        acc_n = 0
        any_running = any_limited = any_defrost = False
        defrost_steps = 0
        cop_heat = cop_elec = 0.0
        for i in range(steps):
            _apply_timeline(sim, plan, sim.simulation_time)
            sim.advance()
            step = sim.last_step
            total_added += step.added_grams
            total_removed += step.removed_grams
            running = step.compressor_running
            defrost_steps += step.defrosting
            any_defrost = any_defrost or step.defrosting
            running_steps += running
            limited_steps += step.power_limited
            cop_heat += step.heat_extracted_joules
            cop_elec += step.electrical_joules
            acc_added += step.added_grams
            acc_removed += step.removed_grams
            acc_energy += step.electrical_joules
            acc_n += 1
            any_running = any_running or running
            any_limited = any_limited or step.power_limited
            if (i + 1) % stride == 0 or i == steps - 1:
                span_min = acc_n * dt / 60.0
                state = sim.room_state()
                series["t_min"].append(sim.simulation_time / 60.0)
                series["absolute_humidity_g"].append(state.absolute_humidity_grams)
                series["relative_humidity_pct"].append(state.relative_humidity_pct)
                series["added_g_per_min"].append(acc_added / span_min)
                series["removed_g_per_min"].append(acc_removed / span_min)
                series["coil_temperature_c"].append(sim.coil.surface_temperature)
                series["dew_point_c"].append(_dew_point(room.specific_humidity(), room.temperature))
                series["room_temperature_c"].append(room.temperature)
                series["power_w"].append(acc_energy / (acc_n * dt))
                series["cop"].append(step.cop if running else None)
                series["energy_kwh"].append(sim.energy_consumed_joules / 3.6e6)
                series["running"].append(any_running)
                series["power_limited"].append(any_limited)
                series["defrosting"].append(any_defrost)
                series["frost_g"].append(step.frost_g)
                acc_added = acc_removed = acc_energy = 0.0
                acc_n = 0
                any_running = any_limited = any_defrost = False

        energy_kwh = sim.energy_consumed_joules / 3.6e6
        final = sim.room_state()
        return {
            "series": series,
            "summary": {
                "final_relative_humidity_pct": final.relative_humidity_pct,
                "final_absolute_humidity_g": final.absolute_humidity_grams,
                "water_removed_g": total_removed,
                "water_added_g": total_added,
                "energy_kwh": energy_kwh,
                "grams_per_kwh": total_removed / energy_kwh if energy_kwh > 0 else None,
                "average_cop": cop_heat / cop_elec if cop_elec > 0 else None,
                "running_min": running_steps * dt / 60.0,
                "power_limited_min": limited_steps * dt / 60.0,
                "defrost_cycles": sim.compressor.defrost_count if sim.compressor else 0,
                "defrost_min": defrost_steps * dt / 60.0,
                "max_frost_g": max(series["frost_g"]),
                "saturation_g": psychrometrics.saturation_content_grams(room.temperature, room.volume),
                "steps": steps,
                "points": len(series["t_min"]),
            },
            "max_power_w": sim.compressor.max_power_w if sim.compressor else None,
            "setpoint_c": _setpoint_or_none(sim),
            "compressor_mode": sim.compressor.mode if sim.compressor else None,
        }
    except ScenarioError as exc:
        return _error(exc.field, exc.message)


def _setpoint_or_none(sim) -> Optional[float]:
    """The setpoint to draw, only for a modulating unit (on/off has none)."""
    c = sim.compressor
    return c.setpoint_temperature if c is not None and c.mode == "modulating" else None


def _with_parameter(scenario: Dict[str, Any], parameter: str, value: float) -> Dict[str, Any]:
    import copy

    s = copy.deepcopy(scenario)
    if parameter == "fan_flow_m3_per_h":
        s["fan"]["flow_rate_m3_per_h"] = value
    elif parameter == "setpoint_c":
        s.setdefault("compressor", {})["setpoint_temperature_c"] = value
    elif parameter == "max_power_w":
        s.setdefault("compressor", {})["max_power_w"] = value
    elif parameter == "effectiveness":
        s["coil"]["effectiveness"] = value
    return s


def run_sweep(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run the scenario for each value of one parameter and collect summaries."""
    scenario = request.get("scenario")
    parameter = request.get("parameter")
    if parameter not in SWEEP_PARAMETERS:
        return _error("parameter", "must be one of " + ", ".join(SWEEP_PARAMETERS))
    try:
        lo = _num(request.get("from"), "from")
        hi = _num(request.get("to"), "to")
        n = int(_num(request.get("points", 12), "points"))
    except ScenarioError as exc:
        return _error(exc.field, exc.message)
    if not 2 <= n <= MAX_SWEEP_POINTS:
        return _error("points", "must be between 2 and {}".format(MAX_SWEEP_POINTS))
    if hi <= lo:
        return _error("to", "must be greater than 'from'")
    values = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    rows = []
    for v in values:
        out = run_scenario(_with_parameter(scenario, parameter, v))
        if "error" in out:
            out["error"]["message"] = "at {} = {:g}: {}".format(parameter, v, out["error"]["message"])
            return out
        rows.append(dict(value=v, **out["summary"]))
    label, unit = SWEEP_PARAMETERS[parameter]
    return {"parameter": parameter, "label": label, "unit": unit, "rows": rows}


def _default_control() -> None:
    from webapp.control_runner import DEFAULT_CONTROL
    import copy as _copy

    DEFAULT_SCENARIO["control"] = _copy.deepcopy(DEFAULT_CONTROL)


_default_control()
