"""Exact boundary tests for pure geometry value objects."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from orbital_drift.domain.errors import InvalidGeometryError
from orbital_drift.domain.geometry import BoundingBox, Tile


def _exact(message: str) -> str:
    """Build a regex that accepts one exact exception message."""
    return rf"^{re.escape(message)}$"


def test_bounding_box_preserves_edges_area_and_tuple_round_trip() -> None:
    bounds = BoundingBox(-180.0, -90.0, 180.0, 90.0)

    assert bounds.as_tuple() == (-180.0, -90.0, 180.0, 90.0)
    assert BoundingBox.from_tuple(bounds.as_tuple()) == bounds
    assert bounds.area_deg2 == 64800.0
    assert bounds.contains(-180.0, -90.0) is True
    assert bounds.contains(180.0, 90.0) is True
    assert bounds.contains(0.0, 0.0) is True
    assert bounds.contains(-180.000001, 0.0) is False
    assert bounds.contains(180.000001, 0.0) is False
    assert bounds.contains(0.0, -90.000001) is False
    assert bounds.contains(0.0, 90.000001) is False


@pytest.mark.parametrize(
    ("coordinates", "message"),
    [
        ((-180.1, 0.0, 1.0, 1.0), "min_lon must be within [-180.0, 180.0]"),
        ((0.0, 0.0, 180.1, 1.0), "max_lon must be within [-180.0, 180.0]"),
        ((0.0, -90.1, 1.0, 1.0), "min_lat must be within [-90.0, 90.0]"),
        ((0.0, 0.0, 1.0, 90.1), "max_lat must be within [-90.0, 90.0]"),
        ((1.0, 0.0, 1.0, 1.0), "min_lon must be less than max_lon"),
        ((1.0, 0.0, 0.0, 1.0), "min_lon must be less than max_lon"),
        ((0.0, 1.0, 1.0, 1.0), "min_lat must be less than max_lat"),
        ((0.0, 1.0, 1.0, 0.0), "min_lat must be less than max_lat"),
    ],
)
def test_bounding_box_rejects_invalid_coordinates(
    coordinates: tuple[float, float, float, float], message: str
) -> None:
    with pytest.raises(InvalidGeometryError, match=rf"^{re.escape(message)}$"):
        BoundingBox(*coordinates)


def test_intersection_includes_touching_boundaries_and_excludes_gap() -> None:
    base = BoundingBox(0.0, 0.0, 2.0, 2.0)

    assert base.intersects(BoundingBox(2.0, 0.5, 3.0, 1.5)) is True
    assert base.intersects(BoundingBox(2.0, 2.0, 3.0, 3.0)) is True
    assert base.intersects(BoundingBox(1.0, 1.0, 3.0, 3.0)) is True
    assert base.intersects(BoundingBox(2.000001, 0.0, 3.0, 1.0)) is False


def test_tile_requires_id_and_is_frozen() -> None:
    bounds = BoundingBox(0.0, 0.0, 1.0, 1.0)
    tile = Tile("tile-1", bounds, "EPSG:4326")

    assert tile.bounds == bounds
    assert tile.crs == "EPSG:4326"
    with pytest.raises(InvalidGeometryError, match=_exact("tile_id must be non-empty")):
        Tile("", bounds, "EPSG:4326")
    with pytest.raises(FrozenInstanceError):
        tile.tile_id = "other"  # type: ignore[misc]
