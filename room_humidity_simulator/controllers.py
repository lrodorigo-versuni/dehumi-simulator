"""Reference (baseline) controllers to compare your algorithm against.

Each one implements ``step(measurements, dt) -> Commands`` and declares its
tunable numbers in ``PARAMETERS`` (shown as fields in the web UI).
"""

from __future__ import annotations

from room_humidity_simulator.control import Commands, Measurements


class AlwaysOn:
    """Compressor always on, fan at a fixed speed: maximum drying, no control."""

    PARAMETERS = {"fan_speed": 3}

    def __init__(self, fan_speed: int = 3) -> None:
        self.fan_speed = int(fan_speed)

    def step(self, m: Measurements, dt: float) -> Commands:
        return Commands(compressor_on=True, fan_speed=self.fan_speed)


class Hysteresis:
    """Classic humidistat: on above ``on_above_pct``, off below ``off_below_pct``.

    The fan runs at ``fan_speed`` while the compressor runs and stops with it.
    """

    PARAMETERS = {"on_above_pct": 55.0, "off_below_pct": 50.0, "fan_speed": 2}

    def __init__(self, on_above_pct: float = 55.0, off_below_pct: float = 50.0, fan_speed: int = 2) -> None:
        self.on_above = float(on_above_pct)
        self.off_below = float(off_below_pct)
        self.fan_speed = int(fan_speed)
        self.on = False

    def reset(self, context) -> None:
        self.on = False

    def step(self, m: Measurements, dt: float) -> Commands:
        if m.relative_humidity_pct >= self.on_above:
            self.on = True
        elif m.relative_humidity_pct <= self.off_below:
            self.on = False
        return Commands(compressor_on=self.on, fan_speed=self.fan_speed if self.on else 0)


class StagedHysteresis:
    """Humidistat whose fan speed rises with how far RH is above target.

    Compressor: on above ``target + on_offset``, off below ``target -
    off_offset``. Fan while running: high if RH exceeds target by more than
    ``high_above_pct``, medium above ``medium_above_pct``, low otherwise.
    """

    PARAMETERS = {"on_offset_pct": 3.0, "off_offset_pct": 3.0, "medium_above_pct": 4.0, "high_above_pct": 10.0}

    def __init__(
        self,
        on_offset_pct: float = 3.0,
        off_offset_pct: float = 3.0,
        medium_above_pct: float = 4.0,
        high_above_pct: float = 10.0,
    ) -> None:
        self.on_offset = float(on_offset_pct)
        self.off_offset = float(off_offset_pct)
        self.medium_above = float(medium_above_pct)
        self.high_above = float(high_above_pct)
        self.on = False
        self.top_speed = 3

    def reset(self, context) -> None:
        self.on = False
        self.top_speed = len(context["fan_speeds"]) - 1

    def step(self, m: Measurements, dt: float) -> Commands:
        error = m.relative_humidity_pct - m.target_rh_pct
        if error >= self.on_offset:
            self.on = True
        elif error <= -self.off_offset:
            self.on = False
        if not self.on:
            return Commands(compressor_on=False, fan_speed=0)
        if error > self.high_above:
            speed = self.top_speed
        elif error > self.medium_above:
            speed = min(2, self.top_speed)
        else:
            speed = min(1, self.top_speed)
        return Commands(compressor_on=True, fan_speed=speed)


BUILTIN_CONTROLLERS = {
    "Always on": AlwaysOn,
    "Hysteresis (humidistat)": Hysteresis,
    "Staged hysteresis": StagedHysteresis,
}
