"""Smoke tests confirming the project is set up correctly.

These validate Task 1 deliverables: the package imports, the shared
result/error types behave as structured results, and the configuration data
models and permitted-range constants are present. They are a placeholder
suite that the real functional tests in later tasks will build on.
"""

from __future__ import annotations

import importlib

import pytest


def test_package_imports():
    """The package and all layer modules import without error."""
    for module in (
        "room_humidity_simulator",
        "room_humidity_simulator.psychrometrics",
        "room_humidity_simulator.room",
        "room_humidity_simulator.fan",
        "room_humidity_simulator.coil",
        "room_humidity_simulator.disturbances",
        "room_humidity_simulator.simulator",
        "room_humidity_simulator.results",
        "room_humidity_simulator.config",
    ):
        assert importlib.import_module(module) is not None


def test_hypothesis_is_available():
    """Hypothesis is installed and importable for later property tests."""
    hypothesis = importlib.import_module("hypothesis")
    assert hasattr(hypothesis, "given")


def test_validation_result_ok_carries_value():
    from room_humidity_simulator.results import ValidationResult

    result = ValidationResult.ok(42)
    assert result.is_ok is True
    assert result.is_error is False
    assert result.value == 42
    assert result.error is None
    assert bool(result) is True


def test_validation_result_fail_carries_error():
    from room_humidity_simulator.results import ConfigError, ValidationResult

    error = ConfigError(
        field="volumeM3",
        message="room volume below minimum",
        code="room_volume_below_minimum",
    )
    result = ValidationResult.fail(error)
    assert result.is_ok is False
    assert result.is_error is True
    assert result.value is None
    assert result.error is error
    assert bool(result) is False
    assert "volumeM3" in str(error)
    assert "below minimum" in str(error)


def test_config_models_round_trip_fields():
    from room_humidity_simulator.config import (
        CoilConfig,
        FanConfig,
        RoomConfig,
        TimeStepConfig,
    )

    room = RoomConfig(volume_m3=50.0, temperature_c=20.0, initial_humidity_gm3=10.0)
    assert room.volume_m3 == 50.0
    assert room.temperature_c == 20.0
    assert room.initial_humidity_gm3 == 10.0

    fan = FanConfig(flow_rate_m3_per_s=2.5)
    assert fan.flow_rate_m3_per_s == 2.5

    coil = CoilConfig(surface_temperature_c=5.0, condensation_capacity_g_per_s=3.0)
    assert coil.surface_temperature_c == 5.0
    assert coil.condensation_capacity_g_per_s == 3.0
    assert coil.effectiveness == 0.85

    step = TimeStepConfig(duration_s=1.0)
    assert step.duration_s == 1.0


@pytest.mark.parametrize(
    "const_name, expected",
    [
        ("ROOM_VOLUME_MIN_M3", 0.01),
        ("ROOM_VOLUME_MAX_M3", 100000.0),
        ("ROOM_TEMPERATURE_MIN_C", -50.0),
        ("ROOM_TEMPERATURE_MAX_C", 100.0),
        ("INITIAL_HUMIDITY_MIN_GM3", 0.0),
        ("INITIAL_HUMIDITY_MAX_GM3", 1000.0),
        ("FLOW_RATE_MIN_M3_PER_S", 0.0),
        ("FLOW_RATE_MAX_M3_PER_S", 10.0),
        ("CONDENSATION_CAPACITY_MIN_G", 0.0),
        ("TIME_STEP_DURATION_MIN_S", 0.0),
        ("TIME_STEP_DURATION_MAX_S", 3600.0),
        ("MOISTURE_RATE_MIN_G_PER_S", 0.0),
        ("MOISTURE_RATE_MAX_G_PER_S", 1000.0),
        ("MAX_ACTIVE_DISTURBANCES", 100),
    ],
)
def test_permitted_range_constants(const_name, expected):
    from room_humidity_simulator import config

    assert getattr(config, const_name) == expected
