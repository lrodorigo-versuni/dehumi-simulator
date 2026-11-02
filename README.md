# Room Humidity Simulator

A time-stepped simulation engine that models how moisture in a single enclosed
room evolves under three coupled mechanisms: moisture-injecting disturbances
(people, boiling water), a fan that drives airflow across a condensing coil,
and the coil itself, which removes moisture from the moved air.

## Project layout

```
room_humidity_simulator/     # the package
  psychrometrics.py          # stateless physics (saturation content, dew point)
  room.py                    # Room domain component
  fan.py                     # Fan domain component
  coil.py                    # Coil domain component
  disturbances.py            # Disturbance interface + built-in types (person, kettle, open window)
  compressor.py              # optional Compressor (Phase 2: coil cooling + energy)
  cop_table.py               # COP lookup table (coil x room temperature)
  control.py                 # closed loop: controller interface, sensors, protections, KPIs
  controllers.py             # baseline controllers
controllers/                 # your control algorithms (my_controller.py template)
webapp/                      # local web UI (server.py, scenario.py, static/)
examples/demo.py             # text demo scenario
  simulator.py               # orchestrator (step engine)
  results.py                 # shared ConfigError / ValidationResult types
  config.py                  # config data models + permitted-range constants
tests/                       # pytest + Hypothesis test suite
```

## Web UI

A local web app (Python standard library only, nothing to install) to set up
a scenario, watch it update live and compare parameters:

```bash
.venv/bin/python -m webapp.server
```

Then open <http://127.0.0.1:8765>. It provides:

- **Scenario panel**: room, fan and coil, compressor (setpoint, max power,
  fixed COP or an editable COP lookup table with its curves), an on/off
  schedule, and moisture sources with start/end times. Every change re-runs
  the simulation; the scenario is kept in the browser between visits.
- **Simulation view**: summary tiles and time charts for relative humidity,
  water vapor (with saturation), moisture added vs removed, coil / dew point /
  room temperature, compressor power (with its limit) and COP. Hovering one
  chart shows every value at that time on all of them (arrow keys work too);
  shading marks when the compressor runs. Data table and CSV download.
- **Parameter sweep**: re-runs the scenario across a range of fan flow,
  setpoint, max power or coil effectiveness and plots final humidity, water
  removed, energy, water per kWh, average COP or time at max power.

The backend is `webapp/scenario.py` (pure functions, tested) behind
`webapp/server.py` (`/api/defaults`, `/api/simulate`, `/api/sweep`).

## Frost and defrost

Below 0 °C the water removed from the air freezes on the coil (the
compressor also removes the 334 J/g heat of fusion) and the ice lowers the
coil effectiveness (`frost_halving_g` grams halve it). Like a dehumidifier
control board, the unit defrosts when the coil stays at or below −1 °C for
5 min: compressor off, fan on, the room air melts the ice while the coil
sits at 0 °C, and 30% of the melt re-evaporates into the room. Defrost
ends when the coil is back above +3 °C (2–15 min). All of this is
`DefrostSettings(...)`, passed as `configure_compressor(..., defrost=...)`;
`defrost=None` disables the frost model. Algorithms see
`measurements.defrosting`; KPIs report defrost cycles, time defrosting and
max frost.

**Compressor type.** `configure_compressor(..., mode="on_off")` (the
default) models a fixed-speed compressor: whenever it runs it absorbs its
full power and delivers `COP(T_coil) × max_power_w` of cooling. There is no
coil setpoint: the coil temperature is a state with its own heat capacity
(`coil_heat_capacity_j_per_k`, default 1000 J/K) and follows

```
C · dT/dt = air (sensible + condensation/freezing) + still air − compressor cooling
```

so it is a direct consequence of the compressor state, the fan and the room:
it pulls down when the compressor starts, warms back when it stops, goes
below 0 °C on its own in cool rooms or at low fan speed, frosts, and triggers
the defrost. With ice on the coil it cannot warm past 0 °C until the ice has
melted. `mode="modulating"` keeps the Phase 2 variable-capacity unit that
holds the coil at `setpoint_temperature_c` when it can (the setpoint is only
used in that mode). For an on/off unit, the algorithm controls the coil
temperature only indirectly: through the fan speed (more air, warmer coil)
and the on/off timing.

## Open window (disturbance with noise)

`Open_Window_Disturbance(airflow_m3_per_h, outdoor_temperature_c,
outdoor_rh_pct, gustiness=0.4, gust_time_s=60, seed=1)` exchanges room air
with outdoor air. Over a step the room moves toward the outdoor humidity
(vapor pressure conserved): `V·(q_out − q_room)·(1 − e^(−Q·dt/V))` grams,
so cold winter air dries the room and muggy summer air humidifies it, never
overshooting. The airflow follows the wind: `Q = mean·max(0, 1 + x)` with
`x` a smooth random (Ornstein-Uhlenbeck) gust process of relative std.
`gustiness` and correlation time `gust_time_s`, reproducible from `seed`
and independent of what the controller does, so A/B comparisons face the
same wind. In the web UI: *Moisture sources → + open window*.

Limitation: room temperature stays constant, so muggy outdoor air simply
saturates the room instead of also warming it.

## Testing a control algorithm

Write your algorithm as a Python class with a `step(measurements, dt)`
method returning `Commands(compressor_on=..., fan_speed=...)`, and drop it
in `controllers/` (start from `controllers/my_controller.py`). Each time
step it receives the measured RH (with optional sensor noise, resolution and
lag), room and coil temperature, the actual compressor/fan state and whether
the compressor protection currently allows a switch.

The loop enforces what a real control board would: minimum compressor
on/off times, no compressor start without airflow, and a running compressor
always keeps the fan at least at its lowest speed. Refused requests are
counted. KPIs cover comfort (time in band, time above, max RH, RMS error,
RH excess), energy (compressor + fan, water per kWh) and wear (starts,
starts per hour, blocked commands).

From Python:

```python
from room_humidity_simulator.control import ClosedLoop, ComfortBand, SensorModel
from controllers.my_controller import MyController

loop = ClosedLoop(sim, MyController(), sensor=SensorModel(noise_std_pct=0.3, time_constant_s=30),
                  band=ComfortBand(target_pct=50, low_pct=45, high_pct=55))
result = loop.run(steps=480)
print(result.kpis)          # result.series has the per-step trajectory
```

In the web UI choose **Control → Algorithm**, pick your class (files in
`controllers/` are reloaded on every run; press *Reload algorithms* after
adding one), tune its `PARAMETERS`, and pick a second algorithm under
*Compare with* to get a side-by-side KPI table and overlaid charts. Built-in
baselines: *Always on*, *Hysteresis (humidistat)*, *Staged hysteresis*.
Errors raised inside `step()` are shown with file and line.

## Usage

```python
from room_humidity_simulator import (
    CoilConfig, FanConfig, Person_Disturbance, RoomConfig, Simulator, TimeStepConfig,
)

sim = Simulator()
result = sim.configure(
    RoomConfig(volume_m3=50, temperature_c=25, initial_humidity_gm3=15),
    FanConfig(flow_rate_m3_per_s=0.05),       # 180 m^3/h
    CoilConfig(surface_temperature_c=25, condensation_capacity_g_per_s=0.05),
    TimeStepConfig(duration_s=60),
)
assert result.is_ok, result.error

sim.add_disturbance(Person_Disturbance(moisture_rate=0.02))

# Optional Phase 2: a compressor (400 W absorbed, COP 3.5 -> 1.4 kW of
# cooling at most) cools the coil toward its setpoint while running.
sim.configure_compressor(cop=3.5, setpoint_temperature_c=5, max_power_w=400)
sim.set_compressor_state("running")      # takes effect from the next step

state = sim.advance(n=60).value           # 60 steps of 60 s
print(state.absolute_humidity_grams, state.relative_humidity_pct)
print(sim.energy_consumed_joules, sim.power_draw_watts)
```

Every mutating call returns a `ValidationResult`; on error the previous valid
state is kept and `result.error` describes the offending field.
`sim.last_step` exposes the breakdown of the latest step (added/removed grams,
coil temperature, heat and electrical energy, invalid-disturbance errors).

## Coil model

Air crossing the coil is cooled toward the coil temperature; an ideal coil
releases it saturated there, at humidity `q_out` (the lowest the room can be
dried to). With fan flow `F`, effectiveness `ε` and capacity `cap` (g/s), the
coil removes water at

```
rate = min(cap, F · ε · (q_room − q_out))      (0 if the room dew point ≤ coil temperature)
```

The room keeps recirculating through the coil, so each step is solved
exactly: removal grows with room humidity and with coil subcooling, slows as
the room approaches `q_out`, and long steps never over-dry the room.
`condensation_capacity_g_per_s` is a rate in g/s; `effectiveness` (contact
factor, `1 − bypass factor`) defaults to 0.85.

## Compressor power limit

The compressor absorbs at most `max_power_w` (default 400 W), so it delivers
at most `cop × max_power_w` of cooling. The heat the moved air gives the coil
is sensible (cooling the air) plus latent (condensation, 2260 J/g). If that
load at the setpoint fits, the coil sits at the setpoint and the compressor
modulates below full power; otherwise the coil settles at the warmer
temperature where load = `cop × max_power_w`, the compressor runs at full
power and `sim.last_step.power_limited` is `True`. With a limited compressor
too much airflow wastes the cooling on sensible heat: there is an optimal fan
flow (around 150-200 m³/h for 400 W), as in real dehumidifiers.

### COP lookup table

Instead of a fixed number, `cop` can be a `CopTable`: COP as a function of
coil (evaporator) temperature, optionally also of room temperature (2D),
interpolated linearly / bilinearly and clamped at the table edges.

```python
from room_humidity_simulator import CopTable

lut = CopTable(
    coil_temperatures_c=[-10, 0, 10, 20],
    room_temperatures_c=[15, 25, 35],          # omit for a 1D table
    cops=[[2.3, 3.2, 4.5, 6.5],                # one row per room temperature
          [1.8, 2.5, 3.4, 4.7],
          [1.4, 1.9, 2.6, 3.5]],
)
sim.configure_compressor(cop=lut, setpoint_temperature_c=5, max_power_w=400)
```

The available cooling becomes `COP(T_coil) × max_power_w` and the energy of
each (sub-)step is `heat / COP(T_coil)`; `sim.last_step.cop` reports the
effective COP of the step. With a COP that falls on colder coils, very cold
setpoints become expensive or unreachable and warmer setpoints remove more
water per kWh.

A demo scenario prints a minute-by-minute table:

```bash
.venv/bin/python examples/demo.py
```

## Development

Install the test dependencies (ideally in a virtual environment):

```bash
pip install -e ".[test]"
```

Run the test suite:

```bash
pytest
```

Property-based tests use [Hypothesis](https://hypothesis.readthedocs.io/) and
run a minimum of 100 iterations each (configured via the `simulator` profile in
`tests/conftest.py`). Example and integration tests use
[pytest](https://docs.pytest.org/).
