"""Template for your own control algorithm.

Every class in a ``.py`` file in this ``controllers/`` folder that has a
``step`` method appears in the web UI (Simulation tab -> Control ->
Algorithm), next to the built-in baselines. Edit it and re-run: the file is
reloaded on every run, no server restart needed.

You can also use it from Python::

    from room_humidity_simulator.control import ClosedLoop
    result = ClosedLoop(sim, MyController()).run(steps=180)
    print(result.kpis)

What you get each step (``m`` is a ``Measurements``):

    m.time_s                  simulation time (s)
    m.relative_humidity_pct   measured RH (with sensor noise / lag if set)
    m.room_temperature_c      room temperature
    m.coil_temperature_c      evaporator temperature (end of the last step)
    m.dew_point_c             dew point from the measured RH (None if dry)
    m.compressor_on           actual compressor state
    m.fan_speed               actual fan speed index
    m.compressor_power_w      compressor power in the last step
    m.compressor_can_switch   False while the min on/off time protects it
    m.target_rh_pct           target RH of the run

What you return: ``Commands(compressor_on=bool, fan_speed=int)`` where
``fan_speed`` indexes the fan speeds (by default 0=off, 1=low, 2=medium,
3=high). Requests that break the compressor protections (minimum on/off
time, compressor without fan) are ignored and counted in the KPIs.
"""

from room_humidity_simulator.control import Commands, Measurements


class MyController:
    """Example: hysteresis on RH plus a rate-of-rise trigger.

    Starts the compressor early when RH is rising fast (someone is showering
    or cooking) instead of waiting for the upper threshold, and picks the fan
    speed from the distance to target.
    """

    # Tunable numbers: shown as fields in the web UI, passed to __init__.
    PARAMETERS = {
        "on_above_pct": 55.0,
        "off_below_pct": 48.0,
        "rise_trigger_pct_per_min": 0.5,
        "filter_time_s": 180.0,
    }

    def __init__(self, on_above_pct=55.0, off_below_pct=48.0, rise_trigger_pct_per_min=0.5, filter_time_s=180.0):
        self.on_above = float(on_above_pct)
        self.off_below = float(off_below_pct)
        self.rise_trigger = float(rise_trigger_pct_per_min)
        self.filter_time = float(filter_time_s)
        self.on = False
        self.rh_filtered = None
        self.rise = 0.0
        self.top_speed = 3

    def reset(self, context):
        """Called once before a run: fan_speeds, time_step_s, band, constraints."""
        self.on = False
        self.rh_filtered = None
        self.rise = 0.0
        self.top_speed = len(context["fan_speeds"]) - 1

    def step(self, m: Measurements, dt: float) -> Commands:
        rh = m.relative_humidity_pct
        # Low-pass filter the RH and its slope: a raw difference of two noisy
        # readings would trigger on sensor noise and short-cycle the compressor.
        alpha = min(1.0, dt / max(self.filter_time, dt))
        if self.rh_filtered is None:
            self.rh_filtered = rh
        previous = self.rh_filtered
        self.rh_filtered += alpha * (rh - self.rh_filtered)
        slope = (self.rh_filtered - previous) / dt * 60.0  # % per minute
        self.rise += alpha * (slope - self.rise)

        if rh >= self.on_above or (self.rise >= self.rise_trigger and rh > m.target_rh_pct):
            self.on = True
        elif rh <= self.off_below:
            self.on = False

        if not self.on:
            return Commands(compressor_on=False, fan_speed=0)
        speed = self.top_speed if rh - m.target_rh_pct > 10 else max(1, self.top_speed - 1)
        return Commands(compressor_on=True, fan_speed=speed)
