"""Contract tests for Tile Store and Cloud Masking (Constitution Principle V)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.ingest.cloud import apply_cloud_mask, evaluate_cloud_mask
from orbital_drift.ingest.tile_store import TileStore

# Not real credentials -- fixed test doubles for OrbitalDriftConfig's
# required lakeFS fields, matching tests/unit/test_config.py's convention so
# every test below can build a valid config without separately re-deriving
# the missing-credential defect that module covers.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


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


# -----------------------------------------------------------------------------
# RB-010 part 5: per-module config wiring (Constitution Principle III)
# -----------------------------------------------------------------------------


@pytest.mark.contract
def test_cloud_masking_uses_config_sourced_threshold_when_omitted() -> None:
    """`evaluate_cloud_mask` sources its threshold from `config` when the
    caller omits `cloud_threshold` explicitly.
    """
    # 25 cloud pixels out of 100 valid pixels -> cloud_fraction == 0.25,
    # which straddles the module default (0.20, would exclude) and a
    # config-sourced 0.30 (would not exclude) -- distinguishing which one
    # the function actually used.
    scl = np.array([9] * 25 + [4] * 75, dtype=np.uint8).reshape((10, 10))
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        cloud_cover_max_threshold=0.30,
    )

    result_with_config = evaluate_cloud_mask(scl, config=cfg)
    assert result_with_config.cloud_fraction == pytest.approx(0.25)
    assert result_with_config.excluded_from_training is False

    result_without_config = evaluate_cloud_mask(scl)
    assert result_without_config.excluded_from_training is True


@pytest.mark.contract
def test_cloud_masking_explicit_threshold_overrides_config() -> None:
    """An explicit `cloud_threshold` argument always wins over `config`."""
    scl = np.array([9] * 25 + [4] * 75, dtype=np.uint8).reshape((10, 10))
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        cloud_cover_max_threshold=0.90,
    )

    result = evaluate_cloud_mask(scl, cloud_threshold=0.10, config=cfg)
    assert result.excluded_from_training is True


@pytest.mark.contract
def test_tile_store_base_dir_sourced_from_config(tmp_path: Path) -> None:
    """`TileStore` sources its `base_dir` from `config.tile_store_path` when
    the caller omits `base_dir` explicitly.
    """
    custom_dir = tmp_path / "configured-tiles"
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        tile_store_path=custom_dir,
    )

    store = TileStore(config=cfg)

    assert store.base_dir == custom_dir
    assert custom_dir.exists()


@pytest.mark.contract
def test_tile_store_explicit_base_dir_overrides_config(tmp_path: Path) -> None:
    """An explicit `base_dir` argument always wins over `config`."""
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        tile_store_path=tmp_path / "configured-tiles",
    )
    explicit_dir = tmp_path / "explicit-tiles"

    store = TileStore(base_dir=explicit_dir, config=cfg)

    assert store.base_dir == explicit_dir


@pytest.mark.contract
def test_tile_store_load_scene_bands_sourced_from_config(tmp_path: Path) -> None:
    """`load_scene` sources its `bands` tuple from `config.bands` (captured
    at construction time) when the caller omits `bands` explicitly.
    """
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        bands=("B05", "B06"),
    )
    store = TileStore(base_dir=tmp_path, config=cfg)
    store.save_scene(
        "scene-config-bands",
        {
            "B05": np.full((4, 4), 1, dtype=np.uint16),
            "B06": np.full((4, 4), 2, dtype=np.uint16),
        },
    )

    loaded_arr, _ = store.load_scene("scene-config-bands")

    assert loaded_arr.shape == (2, 4, 4)
    assert loaded_arr[0, 0, 0] == 1
    assert loaded_arr[1, 0, 0] == 2
