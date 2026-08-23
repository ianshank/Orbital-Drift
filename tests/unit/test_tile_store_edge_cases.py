"""Unit tests for TileStore error handling and edge cases."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orbital_drift.ingest.tile_store import TileStore


def test_tile_store_load_nonexistent_scene_raises_filenotfound(tmp_path: Path) -> None:
    """Verifies that attempting to load a non-existent scene raises FileNotFoundError."""
    store = TileStore(base_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="not found"):
        store.load_scene("non-existent-scene-id")


def test_tile_store_missing_band_raises_filenotfound(tmp_path: Path) -> None:
    """Verifies error when a requested band file is missing in the scene folder."""
    store = TileStore(base_dir=tmp_path)
    # Save only B02
    store.save_scene("scene-partial", {"B02": np.zeros((10, 10))})

    with pytest.raises(FileNotFoundError, match="missing in scene"):
        store.load_scene("scene-partial", bands=("B02", "B08"))


def test_tile_store_list_scenes_empty_and_valid(tmp_path: Path) -> None:
    """Verifies listing scenes across empty and populated states."""
    store = TileStore(base_dir=tmp_path)
    assert store.list_scenes() == []

    store.save_scene("scene-1", {"B02": np.zeros((5, 5))})
    store.save_scene("scene-2", {"B02": np.zeros((5, 5))})

    scenes = sorted(store.list_scenes())
    assert scenes == ["scene-1", "scene-2"]
