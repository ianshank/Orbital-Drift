"""Raster-tile storage port using a portable byte payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from orbital_drift.domain.geometry import Tile


@dataclass(frozen=True)
class RasterPayload:
    """Portable raster bytes with shape and dtype metadata, independent of array libraries.

    Adapters translate this stable boundary object to and from NumPy, Torch, GDAL, or
    another array representation; the domain therefore remains hardware- and library-free.
    """

    shape: tuple[int, ...]
    dtype: str
    buffer: bytes | memoryview

    def __post_init__(self) -> None:
        if not self.shape or any(dimension < 0 for dimension in self.shape):
            raise ValueError("shape must contain at least one non-negative dimension")
        if not self.dtype:
            raise ValueError("dtype must be non-empty")
        object.__setattr__(self, "buffer", bytes(self.buffer))


@runtime_checkable
class TileStorePort(Protocol):
    """Persist and retrieve tile payloads without exposing an object-store SDK."""

    def write(self, tile: Tile, payload: RasterPayload) -> str:
        """Write a payload and return its stable storage reference."""

    def read(self, tile: Tile) -> RasterPayload:
        """Read a previously stored payload."""

    def exists(self, tile: Tile) -> bool:
        """Return whether a tile exists."""

    def list_tiles(self, prefix: str) -> tuple[str, ...]:
        """Return stored tile IDs matching a prefix."""


class InMemoryTileStore:
    """A deterministic tile-store fake backed by an ordinary Python dictionary."""

    def __init__(self) -> None:
        self._payloads: dict[str, RasterPayload] = {}

    def write(self, tile: Tile, payload: RasterPayload) -> str:
        """Store a payload under its tile ID and return that ID."""
        self._payloads[tile.tile_id] = payload
        return tile.tile_id

    def read(self, tile: Tile) -> RasterPayload:
        """Return a payload or a clear error when the tile was never written."""
        try:
            return self._payloads[tile.tile_id]
        except KeyError as error:
            raise KeyError(f"tile not found: {tile.tile_id}") from error

    def exists(self, tile: Tile) -> bool:
        """Return whether the tile ID has a stored payload."""
        return tile.tile_id in self._payloads

    def list_tiles(self, prefix: str) -> tuple[str, ...]:
        """Return matching IDs in deterministic lexical order."""
        return tuple(sorted(tile_id for tile_id in self._payloads if tile_id.startswith(prefix)))
