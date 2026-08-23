"""Pure, dependency-free value objects and provenance contracts."""

from __future__ import annotations

from orbital_drift.domain.geometry import BoundingBox, Tile
from orbital_drift.domain.lineage import SCHEMA_VERSION, LineageEnvelope
from orbital_drift.domain.scene import BandRef, SceneRef, select_bands
from orbital_drift.domain.temporal import TemporalRange

__all__ = [
    "SCHEMA_VERSION",
    "BandRef",
    "BoundingBox",
    "LineageEnvelope",
    "SceneRef",
    "TemporalRange",
    "Tile",
    "select_bands",
]
