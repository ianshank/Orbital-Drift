"""Exact timezone and interval tests for the temporal domain model."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from orbital_drift.domain.errors import InvalidTemporalRangeError
from orbital_drift.domain.temporal import TemporalRange


def _exact(message: str) -> str:
    """Build a regex that accepts one exact exception message."""
    return rf"^{re.escape(message)}$"


def test_temporal_range_is_inclusive_and_round_trips_iso_interval() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone(timedelta(hours=2)))
    temporal_range = TemporalRange(start, end)

    assert temporal_range.duration == timedelta(0)
    assert temporal_range.contains(start) is True
    assert temporal_range.contains(end) is True
    assert temporal_range.contains(datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)) is False
    encoded = temporal_range.to_iso_interval()
    assert encoded == "2026-01-01T00:00:00+00:00/2026-01-01T02:00:00+02:00"
    assert TemporalRange.from_iso_interval(encoded) == temporal_range


def test_temporal_range_overlap_includes_shared_endpoint() -> None:
    first = TemporalRange(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    touching = TemporalRange(datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC))
    separated = TemporalRange(
        datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)
    )

    assert first.overlaps(touching) is True
    assert first.overlaps(separated) is False


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC), "start must be timezone-aware"),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2), "end must be timezone-aware"),
        (
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            "start must be less than or equal to end",
        ),
    ],
)
def test_temporal_range_rejects_naive_or_reversed_values(
    start: datetime, end: datetime, message: str
) -> None:
    with pytest.raises(InvalidTemporalRangeError, match=rf"^{re.escape(message)}$"):
        TemporalRange(start, end)


def test_contains_rejects_naive_instants_and_parser_has_exact_errors() -> None:
    temporal_range = TemporalRange(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )

    with pytest.raises(InvalidTemporalRangeError, match=_exact("instant must be timezone-aware")):
        temporal_range.contains(datetime(2026, 1, 1))
    with pytest.raises(
        InvalidTemporalRangeError, match=_exact("interval must contain exactly one '/' separator")
    ):
        TemporalRange.from_iso_interval("2026-01-01T00:00:00+00:00")
    with pytest.raises(
        InvalidTemporalRangeError, match=_exact("interval endpoints must be ISO-8601 datetimes")
    ):
        TemporalRange.from_iso_interval("not-a-date/also-not-a-date")
