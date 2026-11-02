"""Room Humidity Simulator.

A time-stepped simulation engine modeling moisture evolution in a single
enclosed room under coupled mechanisms: moisture-injecting disturbances, a
fan driving airflow across a condensing coil, and the coil itself.

The package is organized into layers:

- ``psychrometrics``: stateless physics (saturation content, dew point).
- ``room``, ``fan``, ``coil``, ``disturbances``: domain components.
- ``compressor``: optional Phase 2 component driving the coil temperature.
- ``simulator``: the orchestrator wiring components together.
- ``results``: shared structured result/error types.
- ``config``: configuration data models and permitted-range constants.
"""

from room_humidity_simulator.compressor import Compressor, CompressorState, DefrostSettings
from room_humidity_simulator.config import (
    CoilConfig,
    CompressorConfig,
    FanConfig,
    RoomConfig,
    TimeStepConfig,
)
from room_humidity_simulator.cop_table import CopTable
from room_humidity_simulator.disturbances import (
    Boiling_Water_Disturbance,
    Disturbance,
    Open_Window_Disturbance,
    Person_Disturbance,
)
from room_humidity_simulator.results import (
    ConfigError,
    DisturbanceError,
    ValidationResult,
)
from room_humidity_simulator.simulator import RoomState, Simulator, StepRecord

__all__ = [
    "ConfigError",
    "DisturbanceError",
    "ValidationResult",
    "RoomConfig",
    "FanConfig",
    "CoilConfig",
    "TimeStepConfig",
    "CompressorConfig",
    "Disturbance",
    "Person_Disturbance",
    "Boiling_Water_Disturbance",
    "Open_Window_Disturbance",
    "Compressor",
    "CompressorState",
    "DefrostSettings",
    "CopTable",
    "Simulator",
    "RoomState",
    "StepRecord",
]
