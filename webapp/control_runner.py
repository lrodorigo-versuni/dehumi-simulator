"""Closed-loop runs for the web UI: controller discovery, runs, comparison.

Controllers come from :mod:`room_humidity_simulator.controllers` (built-in
baselines) and from every ``.py`` file in the project's ``controllers/``
folder, re-imported on every request so edits apply without a restart.
Controller ids are ``builtin:<name>`` or ``file:<file.py>:<ClassName>``.
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from room_humidity_simulator import psychrometrics
from room_humidity_simulator.control import (
    ClosedLoop,
    ComfortBand,
    CompressorProtection,
    ControllerError,
    FanSpeed,
    SensorModel,
)
from room_humidity_simulator.controllers import BUILTIN_CONTROLLERS

CONTROLLERS_DIR = Path(__file__).resolve().parent.parent / "controllers"

DEFAULT_CONTROL: Dict[str, Any] = {
    "mode": "schedule",
    "controller": "file:my_controller.py:MyController",
    "parameters": {},
    "compare": "builtin:Hysteresis (humidistat)",
    "compare_parameters": {},
    "band": {"target_pct": 50.0, "low_pct": 45.0, "high_pct": 55.0},
    "fan_speeds": [
        {"name": "off", "flow_m3_per_h": 0.0, "power_w": 0.0},
        {"name": "low", "flow_m3_per_h": 90.0, "power_w": 15.0},
        {"name": "medium", "flow_m3_per_h": 150.0, "power_w": 25.0},
        {"name": "high", "flow_m3_per_h": 220.0, "power_w": 40.0},
    ],
    "protection": {"min_on_s": 180.0, "min_off_s": 180.0},
    "sensor": {"noise_std_pct": 0.3, "resolution_pct": 0.1, "time_constant_s": 30.0, "seed": 1},
}


def _load_file(path: Path) -> Tuple[Dict[str, type], Optional[str]]:
    """Import a controllers file fresh; return its controller classes or an error."""
    name = "user_controllers_" + path.stem
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 - user code
        return {}, "{}: {}".format(type(exc).__name__, exc)
    classes = {
        cls_name: cls
        for cls_name, cls in vars(module).items()
        if inspect.isclass(cls) and cls.__module__ == name and callable(getattr(cls, "step", None))
    }
    return classes, None


def _all_controllers() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for label, cls in BUILTIN_CONTROLLERS.items():
        found["builtin:" + label] = {"name": label, "cls": cls, "source": "built-in baseline"}
    if CONTROLLERS_DIR.is_dir():
        for path in sorted(CONTROLLERS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            classes, error = _load_file(path)
            if error:
                found["file:" + path.name] = {"name": path.name, "cls": None, "source": "controllers/" + path.name, "error": error}
            for cls_name, cls in classes.items():
                found["file:{}:{}".format(path.name, cls_name)] = {
                    "name": cls_name, "cls": cls, "source": "controllers/" + path.name,
                }
    return found


def list_controllers() -> List[Dict[str, Any]]:
    out = []
    for cid, info in _all_controllers().items():
        cls = info["cls"]
        out.append(
            {
                "id": cid,
                "name": info["name"],
                "source": info["source"],
                "doc": inspect.getdoc(cls) if cls else None,
                "parameters": dict(getattr(cls, "PARAMETERS", {}) or {}) if cls else {},
                "error": info.get("error"),
            }
        )
    return out


class ControlError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _instantiate(cid: str, params: Dict[str, Any], field: str):
    info = _all_controllers().get(cid)
    if info is None:
        raise ControlError(field, "unknown controller '{}'".format(cid))
    if info["cls"] is None:
        raise ControlError(field, "{} failed to load: {}".format(info["source"], info.get("error")))
    cls = info["cls"]
    allowed = getattr(cls, "PARAMETERS", {}) or {}
    kwargs = {k: v for k, v in (params or {}).items() if k in allowed and v is not None}
    try:
        return cls(**kwargs), info["name"]
    except Exception as exc:  # noqa: BLE001 - user code
        raise ControlError(field, "{}: {}".format(type(exc).__name__, exc))


def _settings(control: Dict[str, Any]):
    def number(d: Dict[str, Any], key: str, field: str) -> float:
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
            raise ControlError(field, "must be a number")
        return float(v)

    speeds = []
    for i, f in enumerate(control.get("fan_speeds") or []):
        field = "control.fan_speeds[{}]".format(i)
        flow = number(f, "flow_m3_per_h", field + ".flow_m3_per_h")
        if not 0.0 <= flow <= 36000.0:
            raise ControlError(field + ".flow_m3_per_h", "must be between 0 and 36000 m³/h")
        power = number(f, "power_w", field + ".power_w")
        if power < 0.0:
            raise ControlError(field + ".power_w", "must be >= 0")
        speeds.append(FanSpeed(str(f.get("name") or "speed {}".format(i)), flow, power))
    if not speeds or all(s.flow_m3_per_h <= 0.0 for s in speeds):
        raise ControlError("control.fan_speeds", "needs at least one speed with airflow")
    b = control.get("band") or {}
    band = ComfortBand(
        number(b, "target_pct", "control.band.target_pct"),
        number(b, "low_pct", "control.band.low_pct"),
        number(b, "high_pct", "control.band.high_pct"),
    )
    if not band.low_pct <= band.target_pct <= band.high_pct:
        raise ControlError("control.band.target_pct", "target must lie between low and high")
    p = control.get("protection") or {}
    protection = CompressorProtection(
        number(p, "min_on_s", "control.protection.min_on_s"), number(p, "min_off_s", "control.protection.min_off_s")
    )
    sn = control.get("sensor") or {}
    sensor = SensorModel(
        number(sn, "noise_std_pct", "control.sensor.noise_std_pct"),
        number(sn, "resolution_pct", "control.sensor.resolution_pct"),
        number(sn, "time_constant_s", "control.sensor.time_constant_s"),
        int(number(sn, "seed", "control.sensor.seed")),
    )
    for field, v in (
        ("control.sensor.noise_std_pct", sensor.noise_std_pct),
        ("control.sensor.resolution_pct", sensor.resolution_pct),
        ("control.sensor.time_constant_s", sensor.time_constant_s),
        ("control.protection.min_on_s", protection.min_on_s),
        ("control.protection.min_off_s", protection.min_off_s),
    ):
        if v < 0:
            raise ControlError(field, "must be >= 0")
    return speeds, band, protection, sensor


def _run_one(scenario, build, apply_timeline, cid, params, field, settings):
    speeds, band, protection, sensor = settings
    sim, plan = build(scenario)
    if sim.compressor is None:
        raise ControlError("compressor.enabled", "an algorithm needs the compressor enabled")
    controller, name = _instantiate(cid, params, field)
    plan["events"] = []  # the algorithm, not the schedule, drives the compressor
    loop = ClosedLoop(
        sim, controller, fan_speeds=speeds, sensor=sensor, protection=protection, band=band,
        before_step=lambda s, t: apply_timeline(s, plan, t),
    )
    try:
        result = loop.run(plan["steps"])
    except ControllerError as exc:
        raise ControlError(field, str(exc))
    except Exception as exc:  # noqa: BLE001 - user code
        frames = traceback.extract_tb(exc.__traceback__)
        mine = [f for f in frames if Path(f.filename).resolve().parent == CONTROLLERS_DIR.resolve()]
        frame = (mine or frames)[-1] if frames else None
        where = " ({}:{})".format(Path(frame.filename).name, frame.lineno) if frame else ""
        raise ControlError(field, "{} in step(): {}{}".format(type(exc).__name__, exc, where))
    return sim, plan, result, name


def _downsample(sim, plan, result, max_points: int):
    """Map the per-step closed-loop series to the UI series format."""
    s = result.series
    steps = len(s["t_s"]) - 1
    stride = max(1, math.ceil(steps / max_points))
    dt = plan["dt"]
    room = sim.room
    out: Dict[str, List[Any]] = {k: [] for k in (
        "t_min", "absolute_humidity_g", "relative_humidity_pct", "rh_measured_pct", "added_g_per_min",
        "removed_g_per_min", "coil_temperature_c", "dew_point_c", "room_temperature_c", "power_w",
        "fan_flow_m3_per_h", "cop", "energy_kwh", "running", "power_limited", "defrosting", "frost_g",
    )}

    def push(i, added, removed, power, running, limited, cop, defrosting=False):
        q = s["absolute_humidity_g"][i] / room.volume
        out["t_min"].append(s["t_s"][i] / 60.0)
        out["absolute_humidity_g"].append(s["absolute_humidity_g"][i])
        out["relative_humidity_pct"].append(s["rh_true_pct"][i])
        out["rh_measured_pct"].append(s["rh_measured_pct"][i])
        out["added_g_per_min"].append(added)
        out["removed_g_per_min"].append(removed)
        out["coil_temperature_c"].append(s["coil_temperature_c"][i])
        out["dew_point_c"].append(psychrometrics.dew_point(q, room.temperature) if q > 0 else None)
        out["room_temperature_c"].append(room.temperature)
        out["power_w"].append(power)
        out["fan_flow_m3_per_h"].append(s["fan_flow_m3_per_h"][i])
        out["cop"].append(cop)
        out["energy_kwh"].append(s["energy_kwh"][i])
        out["running"].append(running)
        out["power_limited"].append(limited)
        out["defrosting"].append(defrosting)
        out["frost_g"].append(s["frost_g"][i])

    push(0, 0.0, 0.0, 0.0, False, False, None)
    pos = 0
    while pos < steps:
        end = min(pos + stride, steps)
        idx = range(pos + 1, end + 1)  # the steps folded into this point
        n = len(idx)
        span_min = n * dt / 60.0
        push(
            end,
            sum(s["added_g"][j] for j in idx) / span_min,
            sum(s["removed_g"][j] for j in idx) / span_min,
            sum(s["compressor_power_w"][j] for j in idx) / n,
            any(s["compressor_on"][j] for j in idx),
            any(s["power_limited"][j] for j in idx),
            s["cop"][end] if s["compressor_on"][end] else None,
            any(s["defrosting"][j] for j in idx),
        )
        pos = end
    return out


def run_controlled(scenario, build, apply_timeline, max_points: int) -> Dict[str, Any]:
    """Run the scenario under the selected algorithm (and the comparison one)."""
    control = scenario.get("control") or {}
    settings = _settings(control)
    sim, plan, result, name = _run_one(
        scenario, build, apply_timeline, control.get("controller"), control.get("parameters"),
        "control.controller", settings,
    )
    series = _downsample(sim, plan, result, max_points)
    k = result.kpis
    summary = {
        "final_relative_humidity_pct": series["relative_humidity_pct"][-1],
        "final_absolute_humidity_g": series["absolute_humidity_g"][-1],
        "water_removed_g": k["water_removed_g"],
        "water_added_g": sum(result.series["added_g"]),
        "energy_kwh": k["energy_kwh"],
        "grams_per_kwh": k["grams_per_kwh"],
        "average_cop": None,
        "running_min": k["compressor_on_pct"] / 100.0 * plan["steps"] * plan["dt"] / 60.0,
        "power_limited_min": sum(result.series["power_limited"]) * plan["dt"] / 60.0,
        "saturation_g": psychrometrics.saturation_content_grams(sim.room.temperature, sim.room.volume),
        "steps": plan["steps"],
        "points": len(series["t_min"]),
    }
    out: Dict[str, Any] = {
        "series": series,
        "summary": summary,
        "kpis": k,
        "controller_name": name,
        "band": vars(settings[1]),
        "max_power_w": sim.compressor.max_power_w,
        "setpoint_c": sim.compressor.setpoint_temperature if sim.compressor.mode == "modulating" else None,
        "compressor_mode": sim.compressor.mode,
        "compare": None,
    }
    if control.get("compare"):
        sim_b, plan_b, result_b, name_b = _run_one(
            scenario, build, apply_timeline, control["compare"], control.get("compare_parameters"),
            "control.compare", settings,
        )
        sb = _downsample(sim_b, plan_b, result_b, max_points)
        out["compare"] = {
            "controller_name": name_b,
            "kpis": result_b.kpis,
            "series": {
                k2: sb[k2]
                for k2 in ("relative_humidity_pct", "energy_kwh", "fan_flow_m3_per_h", "running", "power_limited", "power_w", "defrosting", "frost_g")
            },
        }
    return out
