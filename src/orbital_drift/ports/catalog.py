"""Catalog port and a deterministic stdlib in-memory fake."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orbital_drift.domain.geometry import BoundingBox
from orbital_drift.domain.scene import SceneRef
from orbital_drift.domain.temporal import TemporalRange


@runtime_checkable
class SceneCatalogPort(Protocol):
    """Find scene references without prescribing a STAC client implementation."""

    def search(
        self, bounds: BoundingBox, when: TemporalRange, max_cloud_fraction: float, limit: int
    ) -> tuple[SceneRef, ...]:
        """Return up to ``limit`` matching scenes in insertion order."""


class InMemorySceneCatalog:
    """A CPU-only catalog fake that filters supplied immutable scene references."""

    def __init__(self, scenes: tuple[SceneRef, ...] = ()) -> None:
        self._scenes = scenes

    def search(
        self, bounds: BoundingBox, when: TemporalRange, max_cloud_fraction: float, limit: int
    ) -> tuple[SceneRef, ...]:
        """Return matching scenes using the same inclusive domain predicates as production."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return tuple(
            scene
            for scene in self._scenes
            if scene.bounds.intersects(bounds)
            and when.contains(scene.acquired_at)
            and scene.cloud_fraction <= max_cloud_fraction
        )[:limit]
