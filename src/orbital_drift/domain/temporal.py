"""Timezone-safe temporal value objects used by satellite queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from orbital_drift.domain.errors import InvalidTemporalRangeError


def _require_timezone(instant: datetime, field_name: str) -> None:
    """Reject naive or indeterminate datetimes before they enter the domain."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidTemporalRangeError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class TemporalRange:
    """An inclusive, timezone-aware time interval suitable for STAC searches."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_timezone(self.start, "start")
        _require_timezone(self.end, "end")
        if self.start > self.end:
            raise InvalidTemporalRangeError("start must be less than or equal to end")

    @property
    def duration(self) -> timedelta:
        """Return the elapsed duration between the interval endpoints."""
        return self.end - self.start

    def contains(self, instant: datetime) -> bool:
        """Return whether an aware instant lies in this inclusive interval."""
        _require_timezone(instant, "instant")
        return self.start <= instant <= self.end

    def overlaps(self, other: TemporalRange) -> bool:
        """Return whether inclusive ranges overlap or meet at an endpoint."""
        return self.start <= other.end and other.start <= self.end

    def to_iso_interval(self) -> str:
        """Render the STAC-compatible ``<start>/<end>`` ISO-8601 interval."""
        return f"{self.start.isoformat()}/{self.end.isoformat()}"  # pin: ISO-8601 separator (STAC)

    @classmethod
    def from_iso_interval(cls, interval: str) -> TemporalRange:
        """Parse an interval produced by :meth:`to_iso_interval`."""
        parts = interval.split("/")  # pin: ISO-8601 interval separator, STAC convention
        if len(parts) != 2:
            raise InvalidTemporalRangeError("interval must contain exactly one '/' separator")
        try:
            start = datetime.fromisoformat(parts[0])
            end = datetime.fromisoformat(parts[1])
        except ValueError as error:
            raise InvalidTemporalRangeError(
                "interval endpoints must be ISO-8601 datetimes"
            ) from error
        return cls(start, end)
