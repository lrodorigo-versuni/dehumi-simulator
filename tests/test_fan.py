"""Example-based unit tests for the Fan domain component (Task 4.2).

These checks confirm two scenario-specific behaviors of ``Fan``:

- The ``flow_rate`` getter returns exactly the configured volumetric flow rate
  (Requirement 3.7).
- ``moved_air_volume(dt)`` computes ``flow_rate * dt`` for a positive flow rate
  (Requirement 3.5) and returns exactly ``0.0`` when the flow rate is ``0.0``
  for any time-step duration (Requirement 3.6).

_Requirements: 3.6, 3.7_
"""

from __future__ import annotations

import pytest

from room_humidity_simulator.fan import Fan


# --- Flow-rate getter returns the configured value (Requirement 3.7). ---


@pytest.mark.parametrize("flow_rate", [0.0, 0.25, 1.0, 5.5, 10.0])
def test_flow_rate_getter_returns_configured_value(flow_rate):
    """The getter echoes back exactly the configured flow rate (Req 3.7)."""
    fan = Fan(flow_rate_m3_per_s=flow_rate)
    assert fan.flow_rate == flow_rate


# --- moved_air_volume is flow_rate * dt for positive flow (Requirement 3.5). ---


@pytest.mark.parametrize(
    "flow_rate, dt, expected",
    [
        (1.0, 1.0, 1.0),
        (2.0, 3.0, 6.0),
        (0.5, 10.0, 5.0),
        (10.0, 60.0, 600.0),
        (0.25, 0.5, 0.125),
    ],
)
def test_moved_air_volume_is_flow_rate_times_dt(flow_rate, dt, expected):
    """For positive flow, moved air equals flow_rate * dt (Req 3.5)."""
    fan = Fan(flow_rate_m3_per_s=flow_rate)
    assert fan.moved_air_volume(dt) == pytest.approx(expected)


# --- moved_air_volume is 0.0 at zero flow for any dt (Requirement 3.6). ---


@pytest.mark.parametrize("dt", [0.0, 0.5, 1.0, 60.0, 3600.0])
def test_moved_air_volume_is_zero_at_zero_flow(dt):
    """With a zero flow rate the Fan moves 0.0 m^3 regardless of dt (Req 3.6)."""
    fan = Fan(flow_rate_m3_per_s=0.0)
    result = fan.moved_air_volume(dt)
    assert result == 0.0


def test_zero_flow_returns_exact_float_zero():
    """The zero-flow result is exactly the float 0.0, not a negative/other zero."""
    fan = Fan(flow_rate_m3_per_s=0.0)
    result = fan.moved_air_volume(42.0)
    assert result == 0.0
    assert isinstance(result, float)
