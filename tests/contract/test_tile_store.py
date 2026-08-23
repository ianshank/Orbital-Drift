"""Contract tests for Tile Store and Cloud Masking (Constitution Principle V)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orbital_drift.ingest.cloud import apply_cloud_mask, evaluate_cloud_mask
from orbital_drift.ingest.tile_store import TileStore


@pytest.mark.contract
def test_tile_store_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Probes save and load integrity of multi-spectral arrays and metadata."""
    store = TileStore(base_dir=tmp_path)
    scene_id = "scene-2026-08-test"

    h, w = 128, 128
    bands_data = {
        "B02": np.full((h, w), 100, dtype=np.uint16),
        "B03": np.full((h, w), 200, dtype=np.uint16),
        "B04": np.full((h, w), 300, dtype=np.uint16),
        "B08": np.full((h, w), 400, dtype=np.uint16),
    }
    meta = {"cloud_cover": 0.05, "source": "synthetic"}

    saved_dir = store.save_scene(scene_id, bands_data, metadata=meta)
    assert saved_dir.exists()
    assert (saved_dir / "B02.npy").exists()
    assert (saved_dir / "metadata.json").exists()

    loaded_arr, loaded_meta = store.load_scene(scene_id, bands=("B02", "B03", "B04", "B08"))
    assert loaded_arr.shape == (4, h, w)
    assert loaded_arr[0, 0, 0] == 100
    assert loaded_arr[3, 0, 0] == 400
    assert loaded_meta["cloud_cover"] == 0.05
    assert loaded_meta["scene_id"] == scene_id

    assert scene_id in store.list_scenes()


@pytest.mark.contract
def test_cloud_masking_evaluation_and_thresholding() -> None:
    """Probes SCL cloud fraction calculation and exclusion flagging."""
    # 100 pixels: 80 vegetation (4), 20 high probability cloud (9)
    scl = np.array([4] * 80 + [9] * 20, dtype=np.uint8).reshape((10, 10))

    res_clean = evaluate_cloud_mask(scl, cloud_threshold=0.25)
    assert res_clean.total_pixels == 100
    assert res_clean.valid_pixels == 100
    assert res_clean.cloud_pixels == 20
    assert res_clean.cloud_fraction == pytest.approx(0.20)
    assert res_clean.excluded_from_training is False

    res_cloudy = evaluate_cloud_mask(scl, cloud_threshold=0.15)
    assert res_cloudy.cloud_fraction == pytest.approx(0.20)
    assert res_cloudy.excluded_from_training is True


@pytest.mark.contract
def test_apply_cloud_mask_zeros_cloud_pixels() -> None:
    """Probes that apply_cloud_mask replaces cloud pixels with fill_value."""
    bands = np.ones((4, 4, 4), dtype=np.float32) * 500.0
    scl = np.array(
        [
            [4, 4, 9, 9],
            [4, 4, 9, 9],
            [4, 4, 4, 4],
            [4, 4, 4, 4],
        ],
        dtype=np.uint8,
    )

    masked = apply_cloud_mask(bands, scl, fill_value=0.0)
    assert masked.shape == (4, 4, 4)
    # Cloudy top-right should be 0.0
    assert np.all(masked[:, 0:2, 2:4] == 0.0)
    # Clear region should remain 500.0
    assert np.all(masked[:, 2:4, 0:4] == 500.0)
