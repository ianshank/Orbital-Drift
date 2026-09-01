"""Pure geographic value objects expressed in decimal degrees."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from orbital_drift.domain.errors import InvalidGeometryError

MIN_LONGITUDE: Final = -180.0  # pin: WGS84 geodetic bound, not operator-tunable
MAX_LONGITUDE: Final = 180.0  # pin: WGS84 geodetic bound, not operator-tunable
MIN_LATITUDE: Final = -90.0  # pin: WGS84 geodetic bound, not operator-tunable
MAX_LATITUDE: Final = 90.0  # pin: WGS84 geodetic bound, not operator-tunable


@dataclass(frozen=True)
class BoundingBox:
    """A closed geographic rectangle whose coordinate limits are validated."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if not MIN_LONGITUDE <= self.min_lon <= MAX_LONGITUDE:
            raise InvalidGeometryError("min_lon must be within [-180.0, 180.0]")
        if not MIN_LONGITUDE <= self.max_lon <= MAX_LONGITUDE:
            raise InvalidGeometryError("max_lon must be within [-180.0, 180.0]")
        if not MIN_LATITUDE <= self.min_lat <= MAX_LATITUDE:
            raise InvalidGeometryError("min_lat must be within [-90.0, 90.0]")
        if not MIN_LATITUDE <= self.max_lat <= MAX_LATITUDE:
            raise InvalidGeometryError("max_lat must be within [-90.0, 90.0]")
        if self.min_lon >= self.max_lon:
            raise InvalidGeometryError("min_lon must be less than max_lon")
        if self.min_lat >= self.max_lat:
            raise InvalidGeometryError("min_lat must be less than max_lat")

    @classmethod
    def from_tuple(cls, bounds: tuple[float, float, float, float]) -> BoundingBox:
        """Create a box from the STAC-style ``(min_lon, min_lat, max_lon, max_lat)`` tuple."""
        return cls(*bounds)

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return coordinates in STAC-style axis order."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    @property
    def area_deg2(self) -> float:
        """Return the planar area in square decimal degrees."""
        return (self.max_lon - self.min_lon) * (self.max_lat - self.min_lat)

    def contains(self, lon: float, lat: float) -> bool:
        """Return whether a point lies in this closed rectangle, including its boundary."""
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat

    def intersects(self, other: BoundingBox) -> bool:
        """Return whether closed rectangles overlap or touch at an edge or corner."""
        return not (
            self.max_lon < other.min_lon
            or other.max_lon < self.min_lon
            or self.max_lat < other.min_lat
            or other.max_lat < self.min_lat
        )


@dataclass(frozen=True)
class Tile:
    """An immutable spatial-tile identity and its declared coordinate reference system."""

    tile_id: str
    bounds: BoundingBox
    crs: str

    def __post_init__(self) -> None:
        if not self.tile_id:
            raise InvalidGeometryError("tile_id must be non-empty")
