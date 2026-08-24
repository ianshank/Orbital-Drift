"""Immutable references to remotely catalogued satellite scenes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from orbital_drift.domain.errors import InvalidTemporalRangeError, MissingBandError
from orbital_drift.domain.geometry import BoundingBox


@dataclass(frozen=True)
class BandRef:
    """A named scene band and the URI from which its bytes can be obtained."""

    name: str
    href: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("band name must be non-empty")
        if not self.href:
            raise ValueError("band href must be non-empty")


@dataclass(frozen=True)
class SceneRef:
    """A portable scene reference with a copied read-only band mapping.

    ``MappingProxyType(dict(...))`` makes the frozen dataclass genuinely immutable in
    practice: callers cannot mutate the source mapping after construction or the mapping
    exposed by ``bands``.
    """

    scene_id: str
    bounds: BoundingBox
    acquired_at: datetime
    cloud_fraction: float
    bands: Mapping[str, BandRef]

    def __post_init__(self) -> None:
        if not self.scene_id:
            raise ValueError("scene_id must be non-empty")
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise InvalidTemporalRangeError("acquired_at must be timezone-aware")
        if not 0.0 <= self.cloud_fraction <= 1.0:
            raise ValueError("cloud_fraction must be within [0.0, 1.0]")
        copied_bands = dict(self.bands)
        object.__setattr__(self, "bands", MappingProxyType(copied_bands))


def select_bands(scene: SceneRef, names: tuple[str, ...]) -> Mapping[str, BandRef]:
    """Return requested bands, or list every unavailable requested name in one error."""
    missing = tuple(name for name in names if name not in scene.bands)
    if missing:
        raise MissingBandError(f"scene '{scene.scene_id}' is missing bands: {', '.join(missing)}")
    return MappingProxyType({name: scene.bands[name] for name in names})
