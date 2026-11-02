"""Shared pytest/Hypothesis configuration for the test suite.

Registers and loads a Hypothesis profile that runs at least 100 examples per
property test, matching the design's requirement that "Each property test runs
a minimum of 100 iterations." Later property-based tests (Tasks 2+) inherit
this profile automatically.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile(
    "simulator",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("simulator")
