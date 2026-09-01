"""Unit tests for Sentinel2PatchDataset edge cases and boundary conditions."""

from __future__ import annotations

import numpy as np
import torch

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.data.dataset import Sentinel2PatchDataset

# Not real credentials -- fixed test doubles for OrbitalDriftConfig's
# required lakeFS fields, matching tests/unit/test_config.py's convention so
# every test below can build a valid config without separately re-deriving
# the missing-credential defect that module covers.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


def test_dataset_image_smaller_than_patch_size() -> None:
    """Verifies that images smaller than patch_size are padded with zero reflectance."""
    # 100x100 raster with patch_size 256
    small_data = np.random.randint(500, 3000, size=(4, 100, 100), dtype=np.int16)
    small_labels = np.random.randint(0, 5, size=(100, 100), dtype=np.int64)

    dataset = Sentinel2PatchDataset(small_data, labels=small_labels, patch_size=256)
    assert len(dataset) == 1

    img, lbl = dataset[0]
    assert img.shape == (4, 256, 256)
    assert lbl.shape == (256, 256)
    assert img.dtype == torch.float32
    assert lbl.dtype == torch.int64

    # Top-left 100x100 should contain data; bottom-right padded with zeros
    assert torch.all(img[:, 100:, 100:] == 0.0)
    assert torch.all(lbl[100:, 100:] == 0)


def test_dataset_without_labels_inference_mode() -> None:
    """Verifies dataset functioning without label tensor (inference mode)."""
    raster = np.random.randint(100, 5000, size=(4, 512, 512), dtype=np.int16)
    dataset = Sentinel2PatchDataset(raster, labels=None, patch_size=256, stride=256)
    assert len(dataset) == 4

    img, lbl = dataset[0]
    assert img.shape == (4, 256, 256)
    assert lbl.shape == (256, 256)
    assert torch.all(lbl == 0)


def test_dataset_custom_stride_overlapping() -> None:
    """Verifies custom overlapping stride (e.g. patch 256, stride 128)."""
    raster = np.random.randint(100, 5000, size=(4, 512, 512), dtype=np.int16)
    dataset = Sentinel2PatchDataset(raster, patch_size=256, stride=128)
    # y positions: 0, 128, 256 (3); x positions: 0, 128, 256 (3) -> 3x3 = 9
    assert len(dataset) == 9


def test_dataset_normalization_ceiling() -> None:
    """Verifies that reflectances exceeding normalize_max are clipped to 1.0."""
    high_refl = np.full((4, 256, 256), 20000.0, dtype=np.float32)
    dataset = Sentinel2PatchDataset(high_refl, patch_size=256, normalize_max=10000.0)
    img, _ = dataset[0]
    assert torch.all(img == 1.0)


# -----------------------------------------------------------------------------
# RB-010 part 5: per-module config wiring (Constitution Principle III)
# -----------------------------------------------------------------------------


def test_dataset_normalize_max_sourced_from_config() -> None:
    """`Sentinel2PatchDataset` sources `normalize_max` from
    `config.dataset_normalize_max` when the caller omits it explicitly.
    """
    raster = np.full((4, 256, 256), 5000.0, dtype=np.float32)
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        dataset_normalize_max=5000.0,
    )

    dataset = Sentinel2PatchDataset(raster, patch_size=256, config=cfg)
    img, _ = dataset[0]

    assert torch.all(img == 1.0)


def test_dataset_explicit_normalize_max_overrides_config() -> None:
    """An explicit `normalize_max` argument always wins over a passed `config`."""
    raster = np.full((4, 256, 256), 5000.0, dtype=np.float32)
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        dataset_normalize_max=5000.0,
    )

    dataset = Sentinel2PatchDataset(raster, patch_size=256, normalize_max=10000.0, config=cfg)
    img, _ = dataset[0]

    assert torch.all(img == 0.5)


def test_dataset_no_config_keeps_pre_wiring_normalize_max_default() -> None:
    """No config, no explicit `normalize_max` -> unchanged default (10000.0)."""
    raster = np.full((4, 256, 256), 5000.0, dtype=np.float32)

    dataset = Sentinel2PatchDataset(raster, patch_size=256)
    img, _ = dataset[0]

    assert torch.all(img == 0.5)
