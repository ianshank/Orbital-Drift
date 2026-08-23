"""Unit tests for Sentinel-2 Scene Classification Layer (SCL) cloud masking edge cases."""

from __future__ import annotations

import numpy as np
import pytest

from orbital_drift.ingest.cloud import apply_cloud_mask, evaluate_cloud_mask


def test_apply_cloud_mask_100_percent_cloud() -> None:
    """Verifies that all-cloud SCL input masks 100% of spectral pixels."""
    bands = np.ones((4, 2, 2), dtype=np.float32) * 1500.0
    # SCL classes 3, 8, 9, 10 are cloud/shadow classes
    scl = np.array(
        [
            [3, 8],
            [9, 10],
        ],
        dtype=np.uint8,
    )

    masked = apply_cloud_mask(bands, scl, fill_value=0.0)
    assert np.all(masked == 0.0)


def test_apply_cloud_mask_0_percent_cloud() -> None:
    """Verifies that clear-sky SCL leaves spectral bands unchanged."""
    bands = np.ones((4, 2, 2), dtype=np.float32) * 1500.0
    scl = np.array(
        [
            [4, 5],
            [6, 7],
        ],
        dtype=np.uint8,
    )

    masked = apply_cloud_mask(bands, scl, fill_value=0.0)
    assert np.all(masked == 1500.0)


def test_evaluate_cloud_mask_exact_boundary() -> None:
    """Verifies pass/fail verdict at exact cloud fraction threshold."""
    # 2 cloud pixels (8, 9) out of 10 valid pixels -> 0.20 fraction
    scl = np.array([8, 9, 4, 4, 4, 4, 4, 4, 4, 4], dtype=np.uint8)

    # Threshold 0.20 -> passes (fraction <= threshold)
    res_pass = evaluate_cloud_mask(scl, cloud_threshold=0.20)
    assert res_pass.cloud_fraction == pytest.approx(0.20)
    assert res_pass.excluded_from_training is False

    # Threshold 0.15 -> fails (fraction > threshold)
    res_fail = evaluate_cloud_mask(scl, cloud_threshold=0.15)
    assert res_fail.excluded_from_training is True


def test_evaluate_cloud_mask_empty_and_no_data() -> None:
    """Verifies handling of empty and pure no-data SCL arrays."""
    empty_scl = np.array([], dtype=np.uint8)
    res_empty = evaluate_cloud_mask(empty_scl)
    assert res_empty.excluded_from_training is True

    no_data_scl = np.zeros((10, 10), dtype=np.uint8)  # Class 0: NO_DATA
    res_no_data = evaluate_cloud_mask(no_data_scl)
    assert res_no_data.valid_pixels == 0
    assert res_no_data.excluded_from_training is True
