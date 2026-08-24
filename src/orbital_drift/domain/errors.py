"""Typed failures raised by the pure domain model."""

from __future__ import annotations


class DomainError(ValueError):
    """Base class for invalid values that violate a domain invariant."""


class InvalidGeometryError(DomainError):
    """Raised when a geometry cannot represent a valid geographic extent."""


class InvalidTemporalRangeError(DomainError):
    """Raised when a temporal range is unordered or lacks timezone information."""


class MissingBandError(DomainError):
    """Raised when a scene selection requests one or more unavailable bands."""


class InvalidLineageError(DomainError):
    """Raised when lineage data cannot produce valid, portable provenance JSON."""


class NonFiniteMetricError(InvalidLineageError):
    """Raised when a provenance metric is NaN or infinite."""


class UnsupportedSchemaVersionError(InvalidLineageError):
    """Raised when a lineage payload is outside the parser compatibility window."""
