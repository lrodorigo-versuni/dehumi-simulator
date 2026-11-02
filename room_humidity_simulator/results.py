"""Shared structured result and error types.

Validation across the simulator never throws raw exceptions past the public
API. Instead, invalid configuration or operations are surfaced as structured
``ValidationResult`` values carrying a descriptive, field-specific
``ConfigError`` the caller can inspect. This realizes the Error Handling
section of the design: "Validation errors never throw past the public API as
raw exceptions; they are surfaced as structured results the caller can
inspect."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ConfigError:
    """A descriptive, field-specific configuration/validation error.

    Attributes:
        field: The name of the offending field (for example ``"volumeM3"``)
            or operation (for example ``"advanceCount"``). Lets callers map
            an error back to the input that caused it.
        message: A human-readable, field-specific description of the problem,
            matching the Error Handling table in the design (for example
            "room volume below minimum").
        code: A short, stable, machine-friendly identifier for the error
            condition (for example ``"room_volume_below_minimum"``). Useful
            for programmatic branching without string matching on ``message``.
    """

    field: str
    message: str
    code: str

    def __str__(self) -> str:
        return "{field}: {message}".format(field=self.field, message=self.message)


@dataclass(frozen=True)
class ValidationResult(Generic[T]):
    """The outcome of a validation or configuration operation.

    A ``ValidationResult`` is either a success carrying an optional ``value``,
    or a failure carrying a ``ConfigError``. Use :meth:`ok` and :meth:`fail`
    to construct instances rather than the constructor directly.

    Attributes:
        is_ok: ``True`` when the operation succeeded.
        value: The produced value on success, ``None`` otherwise.
        error: The structured error on failure, ``None`` otherwise.
    """

    is_ok: bool
    value: Optional[T] = None
    error: Optional[ConfigError] = None

    @staticmethod
    def ok(value: Optional[T] = None) -> "ValidationResult[T]":
        """Build a successful result carrying ``value``."""
        return ValidationResult(is_ok=True, value=value, error=None)

    @staticmethod
    def fail(error: ConfigError) -> "ValidationResult[T]":
        """Build a failed result carrying ``error``."""
        return ValidationResult(is_ok=False, value=None, error=error)

    @property
    def is_error(self) -> bool:
        """Whether this result represents a failure."""
        return not self.is_ok

    def __bool__(self) -> bool:
        return self.is_ok


@dataclass(frozen=True)
class DisturbanceError:
    """Runtime indication that a disturbance returned an invalid contribution.

    Produced during stepping when an active disturbance's ``contribution(dt)``
    is negative, NaN, non-numeric, or raises. The contribution is excluded
    from the step total (Requirement 6.6). This is a runtime guard rather
    than a configuration error (design: *Runtime guards*).

    Attributes:
        disturbance: The offending disturbance object, identifying it.
        value: The invalid value it returned (or the exception it raised).
        message: A human-readable description of the problem.
    """

    disturbance: object
    value: object
    message: str
