"""Demo scenario for the Room Humidity Simulator.

A 20 m^3 room at 25 deg C with two occupants. A kettle boils for the first
20 minutes; the compressor is off for the first 30 minutes and then runs,
aiming for a 1 deg C coil (just above freezing) with 400 W of absorbed power. Prints a table every 5 simulated minutes.

Run from the project root:

    .venv/bin/python examples/demo.py
"""

from __future__ import annotations

from room_humidity_simulator import (
    Boiling_Water_Disturbance,
    CoilConfig,
    CopTable,
    FanConfig,
    Person_Disturbance,
    RoomConfig,
    Simulator,
    TimeStepConfig,
)

STEP_S = 60.0          # one step = 1 simulated minute
REPORT_EVERY = 5       # print a row every 5 steps
TOTAL_MINUTES = 90
KETTLE_OFF_AT = 20     # minute the kettle is removed
COMPRESSOR_ON_AT = 30  # minute the compressor is switched on


def bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100.0 * width)
    return "#" * filled + "." * (width - filled)


def main() -> None:
    sim = Simulator()
    result = sim.configure(
        RoomConfig(volume_m3=20.0, temperature_c=25.0, initial_humidity_gm3=12.0),
        FanConfig(flow_rate_m3_per_s=0.05),  # 180 m^3/h, typical dehumidifier
        CoilConfig(surface_temperature_c=25.0, condensation_capacity_g_per_s=0.5, effectiveness=0.85),
        TimeStepConfig(duration_s=STEP_S),
    )
    assert result.is_ok, result.error

    sim.add_disturbance(Person_Disturbance(moisture_rate=0.015))
    sim.add_disturbance(Person_Disturbance(moisture_rate=0.015))
    kettle = Boiling_Water_Disturbance(moisture_rate=0.2)
    sim.add_disturbance(kettle)
    # COP from a lookup table over coil (evaporator) and room temperature.
    # Illustrative values for a small dehumidifier compressor: replace them
    # with your compressor's datasheet. 400 W absorbed at most.
    cop_lut = CopTable(
        coil_temperatures_c=[-10, -5, 0, 5, 10, 15, 20, 25],
        room_temperatures_c=[15, 25, 35],
        cops=[
            [2.3, 2.7, 3.2, 3.8, 4.5, 5.4, 6.5, 7.8],  # room 15 C
            [1.8, 2.1, 2.5, 2.9, 3.4, 4.0, 4.7, 5.5],  # room 25 C
            [1.4, 1.6, 1.9, 2.2, 2.6, 3.0, 3.5, 4.1],  # room 35 C
        ],
    )
    sim.configure_compressor(cop=cop_lut, setpoint_temperature_c=1.0, max_power_w=400.0)

    header = (
        f"{'min':>4} {'acqua g':>8} {'UR %':>6}  {'':20}  "
        f"{'+g/step':>7} {'-g/step':>7} {'compr.':>7} {'coil C':>6} {'COP':>4} {'W':>5} {'kWh':>6}"
    )
    print(header)
    print("-" * len(header))

    for minute in range(1, TOTAL_MINUTES + 1):
        # Events are requested before the step and apply from that step on.
        if minute == KETTLE_OFF_AT + 1:
            sim.remove_disturbance(kettle)
            print(f"{'':>4} >> bollitore spento")
        if minute == COMPRESSOR_ON_AT + 1:
            sim.set_compressor_state("running")
            print(f"{'':>4} >> compressore acceso (400 W, setpoint bobina 1 C)")

        state = sim.advance().value
        step = sim.last_step
        if minute % REPORT_EVERY == 0:
            print(
                f"{minute:>4} {state.absolute_humidity_grams:>8.1f} "
                f"{state.relative_humidity_pct:>6.1f}  {bar(state.relative_humidity_pct)}  "
                f"{step.added_grams:>7.2f} {step.removed_grams:>7.2f} "
                f"{step.compressor_state.value:>7} {step.coil_surface_temperature_c:>6.1f} "
                f"{(f'{step.cop:.2f}' if step.cop is not None else '-'):>4} "
                f"{sim.power_draw_watts:>5.0f} {sim.energy_consumed_joules / 3.6e6:>6.3f}"
            )


if __name__ == "__main__":
    main()
