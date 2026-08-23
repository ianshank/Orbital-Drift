"""Typed dependency-inversion ports and deterministic standard-library fakes."""

from __future__ import annotations

from orbital_drift.ports.catalog import InMemorySceneCatalog, SceneCatalogPort
from orbital_drift.ports.compute import ComputePort, DeviceSpec, InMemoryCompute
from orbital_drift.ports.dataversion import DataVersionPort, InMemoryDataVersion
from orbital_drift.ports.registry import InMemoryModelRegistry, ModelRegistryPort
from orbital_drift.ports.tiles import InMemoryTileStore, RasterPayload, TileStorePort

__all__ = [
    "ComputePort",
    "DataVersionPort",
    "DeviceSpec",
    "InMemoryCompute",
    "InMemoryDataVersion",
    "InMemoryModelRegistry",
    "InMemorySceneCatalog",
    "InMemoryTileStore",
    "ModelRegistryPort",
    "RasterPayload",
    "SceneCatalogPort",
    "TileStorePort",
]
