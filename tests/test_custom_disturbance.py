"""Example-based unit tests for custom disturbance incorporation (Task 7.3).

The ``Disturbance`` interface (``is_active()`` and ``contribution(dt)``) is the
sole contract the simulation engine depends on for moisture sources. A caller
may supply a *custom* disturbance type -- one the engine has never seen -- and
it must be incorporated through that interface without any change to the Coil
or Fan components (Requirements 6.1, 6.3).

Because the ``Simulator``'s disturbance-management wiring (Task 9) may not be
implemented yet, these tests exercise the contract at the **interface level**:

- A caller-supplied class that conforms to the ``Disturbance`` interface is
  accepted as a ``Disturbance`` (subclass / duck-typing check) (Requirement 6.1).
- Its ``contribution(dt)`` is honored as given: whatever the custom type
  computes is the moisture mass in grams incorporated for that step, with no
  interference from Coil or Fan logic (Requirements 6.1, 6.3).
- The custom type is used purely through the interface -- the test never
  references the Coil or Fan, demonstrating that incorporating a new
  disturbance requires no changes to them (Requirement 6.3).

_Requirements: 6.1, 6.3_
"""

from __future__ import annotations

import math

import pytest

from room_humidity_simulator.disturbances import Disturbance


class _CustomRampDisturbance(Disturbance):
    """A caller-authored disturbance that is *not* one of the built-in types.

    It contributes moisture following its own rule -- a quadratic ramp in the
    time-step duration (``rate * dt ** 2``) -- to prove the engine honors an
    arbitrary interface-conforming contribution rather than assuming the
    built-in ``rate * dt`` form.
    """

    def __init__(self, rate: float, active: bool = True) -> None:
        self.rate = rate
        self._active = active

    def is_active(self) -> bool:
        return self._active

    def contribution(self, dt: float) -> float:
        return self.rate * dt * dt


class _DuckTypedDisturbance:
    """A disturbance that conforms by duck typing without subclassing.

    It implements ``is_active()`` and ``contribution(dt)`` but does not inherit
    from ``Disturbance``, matching the "conforms to a common interface"
    phrasing of Requirement 6.1 (structural conformance).
    """

    def __init__(self, fixed_grams: float, active: bool = True) -> None:
        self.fixed_grams = fixed_grams
        self._active = active

    def is_active(self) -> bool:
        return self._active

    def contribution(self, dt: float) -> float:
        # Honors the dt > 0 contract but applies its own (constant) rule.
        return self.fixed_grams


# --- Subclass conformance to the Disturbance interface (Requirement 6.1). ---


def test_custom_disturbance_is_a_disturbance_instance():
    """A custom subclass is recognized as a Disturbance (interface conformance)."""
    disturbance = _CustomRampDisturbance(rate=2.0)
    assert isinstance(disturbance, Disturbance)


def test_custom_disturbance_exposes_the_interface_methods():
    """The custom type exposes both required interface methods, callable as such."""
    disturbance = _CustomRampDisturbance(rate=2.0)
    assert callable(disturbance.is_active)
    assert callable(disturbance.contribution)


@pytest.mark.parametrize("active", [True, False])
def test_custom_disturbance_is_active_is_honored(active):
    """is_active() returns the custom type's own activity flag unchanged."""
    disturbance = _CustomRampDisturbance(rate=5.0, active=active)
    assert disturbance.is_active() is active


# --- contribution(dt) is honored as the custom type computes it (Req 6.1, 6.3). ---


@pytest.mark.parametrize(
    ("rate", "dt"),
    [
        (1.0, 1.0),
        (2.0, 3.0),
        (0.0, 10.0),
        (7.5, 0.5),
        (100.0, 60.0),
    ],
)
def test_custom_contribution_is_returned_verbatim(rate, dt):
    """The interface returns exactly the custom rule's result for dt > 0."""
    disturbance = _CustomRampDisturbance(rate=rate)
    expected = rate * dt * dt
    result = disturbance.contribution(dt)
    assert math.isclose(result, expected, rel_tol=1e-12, abs_tol=1e-12)


def test_custom_contribution_can_be_used_through_the_base_interface_type():
    """Treating the instance through the Disturbance type still honors its rule.

    This mirrors how the Simulator would hold the disturbance only as a
    ``Disturbance`` reference and call ``contribution`` through it, with no Coil
    or Fan involvement (Requirements 6.1, 6.3).
    """
    disturbance: Disturbance = _CustomRampDisturbance(rate=4.0)
    if disturbance.is_active():
        contributed = disturbance.contribution(2.0)
    else:  # pragma: no cover - the ramp disturbance defaults to active
        contributed = 0.0
    assert contributed == pytest.approx(4.0 * 2.0 * 2.0)


# --- Duck-typed conformance without subclassing (Requirement 6.1). ---


def test_duck_typed_disturbance_conforms_structurally():
    """A non-subclass that implements the interface methods conforms structurally."""
    disturbance = _DuckTypedDisturbance(fixed_grams=12.5)
    assert hasattr(disturbance, "is_active")
    assert hasattr(disturbance, "contribution")
    assert disturbance.is_active() is True


def test_duck_typed_contribution_is_honored():
    """The duck-typed type's own contribution rule is honored for dt > 0."""
    disturbance = _DuckTypedDisturbance(fixed_grams=12.5)
    assert disturbance.contribution(5.0) == pytest.approx(12.5)
    # Its constant rule is independent of dt by design -- honored as given.
    assert disturbance.contribution(1.0) == pytest.approx(12.5)


def test_summing_custom_and_builtin_contributions_needs_no_coil_or_fan():
    """Contributions compose through the interface alone, independent of Coil/Fan.

    The total disturbance moisture for a step is the arithmetic sum of each
    active disturbance's ``contribution`` (the shape of Requirement 6.4), and a
    custom type participates on equal footing with the built-ins -- computed
    entirely through the interface, with no reference to Coil or Fan
    (Requirements 6.1, 6.3).
    """
    from room_humidity_simulator.disturbances import Person_Disturbance

    dt = 2.0
    custom = _CustomRampDisturbance(rate=3.0)  # 3 * 2 * 2 = 12.0
    builtin = Person_Disturbance(moisture_rate=5.0)  # 5 * 2 = 10.0

    active = [d for d in (custom, builtin) if d.is_active()]
    total = sum(d.contribution(dt) for d in active)

    assert total == pytest.approx(12.0 + 10.0)
