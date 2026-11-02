"""Tests for the web UI backend: scenario runner, sweep and HTTP endpoints."""

from __future__ import annotations

import copy
import json
import math
import threading
import urllib.error
import urllib.request

import pytest

from webapp import server
from webapp.scenario import DEFAULT_SCENARIO, MAX_POINTS, run_scenario, run_sweep


def _scenario(**changes):
    s = copy.deepcopy(DEFAULT_SCENARIO)
    for path, value in changes.items():
        target = s
        keys = path.split("__")
        for k in keys[:-1]:
            target = target[k]
        target[keys[-1]] = value
    return s


def test_default_scenario_runs_and_is_consistent():
    out = run_scenario(DEFAULT_SCENARIO)
    s, summary = out["series"], out["summary"]
    assert summary["steps"] == 90
    n = len(s["t_min"])
    assert all(len(v) == n for v in s.values())
    assert s["t_min"][0] == 0.0 and s["t_min"][-1] == pytest.approx(90.0)
    assert all(0.0 <= rh <= 100.0 for rh in s["relative_humidity_pct"])
    # Energy is cumulative and the power never exceeds the 400 W limit.
    assert s["energy_kwh"] == sorted(s["energy_kwh"])
    assert max(s["power_w"]) <= 400.0 * (1 + 1e-9)
    assert summary["energy_kwh"] == pytest.approx(s["energy_kwh"][-1])
    assert summary["water_added_g"] == pytest.approx((2 * 0.015 * 90 + 0.2 * 20) * 60.0)


def test_timeline_switches_sources_and_compressor():
    s = run_scenario(DEFAULT_SCENARIO)["series"]
    at = {round(t): i for i, t in enumerate(s["t_min"])}
    # Kettle (0.2 g/s = 12 g/min) plus two people until minute 20, then people only.
    assert s["added_g_per_min"][at[10]] == pytest.approx(13.8)
    assert s["added_g_per_min"][at[25]] == pytest.approx(1.8)
    # Compressor switched on at minute 30.
    assert not any(s["running"][: at[30] + 1])
    assert all(s["running"][at[31]:])
    assert s["removed_g_per_min"][at[29]] == 0.0 and s["removed_g_per_min"][at[31]] > 0.0


def test_fixed_cop_and_disabled_compressor():
    fixed = run_scenario(_scenario(compressor__cop_mode="fixed", compressor__cop=3.0))
    assert fixed["summary"]["average_cop"] == pytest.approx(3.0)
    off = run_scenario(_scenario(compressor__enabled=False))
    assert off["summary"]["energy_kwh"] == 0.0
    assert off["max_power_w"] is None


def test_one_dimensional_cop_table():
    table = {"coil_temperatures_c": [0, 10, 20], "room_temperatures_c": [], "cops": [[2.5, 3.4, 4.7]]}
    out = run_scenario(_scenario(compressor__cop_table=table))
    assert "error" not in out
    assert 2.5 <= out["summary"]["average_cop"] <= 4.7


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"room__volume_m3": 0.001}, "room.volume_m3"),
        ({"coil__effectiveness": 1.5}, "coil.effectiveness"),
        ({"fan__flow_rate_m3_per_h": 99999}, "fan.flow_rate_m3_per_h"),
        ({"compressor__max_power_w": 0}, "compressor.max_power_w"),
        ({"compressor__setpoint_temperature_c": None}, "compressor.setpoint_temperature_c"),
        ({"time_step_s": 7200}, "time_step_s"),
        ({"duration_min": 0}, "duration_min"),
        ({"time_step_s": 1, "duration_min": 10000}, "duration_min"),
    ],
)
def test_errors_point_at_the_offending_input(changes, field):
    out = run_scenario(_scenario(**changes))
    assert out["error"]["field"] == field
    assert out["error"]["message"]


def test_invalid_cop_table_and_disturbance_are_reported():
    s = _scenario()
    s["compressor"]["cop_table"]["cops"][1][2] = -1
    assert run_scenario(s)["error"]["field"] == "compressor.cop_table"
    s = _scenario()
    s["disturbances"][0]["moisture_rate_g_per_s"] = 2000
    assert run_scenario(s)["error"]["field"] == "disturbances[0].moisture_rate_g_per_s"
    s = _scenario()
    s["disturbances"][1]["kind"] = "dog"
    assert run_scenario(s)["error"]["field"] == "disturbances[1].kind"


def test_long_runs_are_downsampled_but_totals_are_exact():
    out = run_scenario(_scenario(time_step_s=10, duration_min=600))
    assert out["summary"]["steps"] == 3600
    assert out["summary"]["points"] <= MAX_POINTS + 1
    assert out["series"]["t_min"][-1] == pytest.approx(600.0)
    assert out["summary"]["water_added_g"] == pytest.approx((2 * 0.015 * 600 + 0.2 * 20) * 60.0)


def test_sweep_finds_airflow_optimum():
    out = run_sweep({"scenario": DEFAULT_SCENARIO, "parameter": "fan_flow_m3_per_h", "from": 30, "to": 1000, "points": 12})
    rh = [r["final_relative_humidity_pct"] for r in out["rows"]]
    best = rh.index(min(rh))
    assert 0 < best < len(rh) - 1  # an interior optimum, not an edge
    assert run_sweep({"scenario": DEFAULT_SCENARIO, "parameter": "nope", "from": 0, "to": 1})["error"]["field"] == "parameter"
    assert run_sweep({"scenario": DEFAULT_SCENARIO, "parameter": "setpoint_c", "from": 5, "to": 1})["error"]["field"] == "to"


@pytest.mark.parametrize(
    "path, ok",
    [
        ("/", True),
        ("/app.js", True),
        ("/../server.py", False),
        ("/%2e%2e/server.py", False),
        ("/..%2F..%2Fpyproject.toml", False),
        ("/missing.js", False),
    ],
)
def test_static_files_cannot_escape_the_static_dir(path, ok):
    target = server.static_target(path)
    assert (target is not None) == ok
    if ok:
        assert server.STATIC_DIR in target.parents


def test_non_finite_values_become_null_in_json():
    assert server._finite({"a": [math.inf, 1.0, math.nan], "b": {"c": -math.inf}}) == {"a": [None, 1.0, None], "b": {"c": None}}


@pytest.fixture
def live_server():
    try:
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    except OSError as exc:  # sandboxed environments may forbid binding
        pytest.skip("cannot bind a local port: {}".format(exc))
    httpd.RequestHandlerClass.log_message = lambda *a: None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:{}".format(httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_http_endpoints(live_server):
    with urllib.request.urlopen(live_server + "/") as res:
        assert res.status == 200 and b"Room humidity simulator" in res.read()
    with urllib.request.urlopen(live_server + "/api/defaults") as res:
        assert "scenario" in json.loads(res.read())
    status, body = _post(live_server + "/api/simulate", DEFAULT_SCENARIO)
    assert status == 200 and body["summary"]["steps"] == 90
    status, body = _post(live_server + "/api/simulate", _scenario(room__volume_m3=-1))
    assert status == 400 and body["error"]["field"] == "room.volume_m3"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(live_server + "/../server.py")
    assert err.value.code == 404


# ---------------------------------------------------------------------------
# Closed-loop control from the web UI
# ---------------------------------------------------------------------------

from webapp import control_runner  # noqa: E402


def _controlled(**changes):
    s = _scenario(**changes)
    s["control"]["mode"] = "controller"
    return s


def test_controllers_are_listed_with_parameters():
    ids = {c["id"]: c for c in control_runner.list_controllers()}
    assert "builtin:Hysteresis (humidistat)" in ids
    mine = ids["file:my_controller.py:MyController"]
    assert mine["parameters"]["on_above_pct"] == 55.0
    assert mine["source"] == "controllers/my_controller.py"


def test_controlled_run_with_comparison():
    s = _controlled(duration_min=240, time_step_s=30)
    s["disturbances"].append({"kind": "boiling_water", "moisture_rate_g_per_s": 0.4, "start_min": 120, "end_min": 135})
    out = run_scenario(s)
    assert "error" not in out
    assert out["controller_name"] == "MyController"
    assert out["compare"]["controller_name"] == "Hysteresis (humidistat)"
    assert 0.0 <= out["kpis"]["time_in_band_pct"] <= 100.0
    n = len(out["series"]["t_min"])
    assert all(len(v) == n for v in out["series"].values())
    assert all(len(v) == n for v in out["compare"]["series"].values())
    assert {"running", "power_limited", "power_w"} <= set(out["compare"]["series"])
    assert set(out["series"]["fan_flow_m3_per_h"]) <= {0.0, 90.0, 150.0, 220.0}
    assert out["series"]["energy_kwh"][-1] == pytest.approx(out["kpis"]["energy_kwh"])
    json.dumps(out, allow_nan=False)


def test_controller_parameters_reach_the_algorithm():
    always_low = _controlled()
    always_low["control"].update(controller="builtin:Always on", parameters={"fan_speed": 1}, compare=None)
    out = run_scenario(always_low)
    assert set(out["series"]["fan_flow_m3_per_h"][1:]) == {90.0}
    assert out["compare"] is None


@pytest.mark.parametrize(
    "edit, field",
    [
        (lambda c: c.update(controller="file:nope.py:X"), "control.controller"),
        (lambda c: c.update(compare="builtin:nope"), "control.compare"),
        (lambda c: c["band"].update(target_pct=80.0), "control.band.target_pct"),
        (lambda c: c.update(fan_speeds=[{"name": "off", "flow_m3_per_h": 0.0, "power_w": 0.0}]), "control.fan_speeds"),
        (lambda c: c["sensor"].update(noise_std_pct=-1.0), "control.sensor.noise_std_pct"),
        (lambda c: c["protection"].update(min_on_s=None), "control.protection.min_on_s"),
    ],
)
def test_control_settings_errors(edit, field):
    s = _controlled()
    edit(s["control"])
    assert run_scenario(s)["error"]["field"] == field


def test_algorithm_needs_the_compressor():
    s = _controlled()
    s["compressor"]["enabled"] = False
    assert run_scenario(s)["error"]["field"] == "compressor.enabled"


def test_user_controller_files_are_reloaded_and_errors_located(tmp_path, monkeypatch):
    monkeypatch.setattr(control_runner, "CONTROLLERS_DIR", tmp_path)
    f = tmp_path / "algo.py"
    f.write_text(
        "from room_humidity_simulator.control import Commands\n"
        "class A:\n"
        "    PARAMETERS = {'speed': 1}\n"
        "    def __init__(self, speed=1):\n"
        "        self.speed = int(speed)\n"
        "    def step(self, m, dt):\n"
        "        return Commands(True, self.speed)\n"
    )
    s = _controlled()
    s["control"].update(controller="file:algo.py:A", parameters={"speed": 3}, compare=None)
    assert set(run_scenario(s)["series"]["fan_flow_m3_per_h"][1:]) == {220.0}

    # Edited on disk -> picked up on the next run, no restart.
    f.write_text(f.read_text().replace("return Commands(True, self.speed)", "return [1][5]"))
    err = run_scenario(s)["error"]
    assert err["field"] == "control.controller"
    assert "IndexError in step()" in err["message"] and "algo.py:7" in err["message"]

    (tmp_path / "bad.py").write_text("def broken(:\n")
    listed = {c["id"]: c for c in control_runner.list_controllers()}
    assert listed["file:bad.py"]["error"].startswith("SyntaxError")
