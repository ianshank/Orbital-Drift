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


def test_apply_cloud_mask_unknown_scl_code_is_not_cloud() -> None:
    """Unknown SCL codes stay not-cloud; a 0-11 LUT would raise or mis-mask."""
    bands = np.ones((3, 2, 2), dtype=np.float32) * 1500.0
    scl = np.array(
        [
            [4, 99],
            [4, 99],
        ],
        dtype=np.uint8,
    )
    masked = apply_cloud_mask(bands, scl, fill_value=-1.0)
    assert np.all(masked == 1500.0)


def test_apply_cloud_mask_nonzero_fill_applies_to_every_channel() -> None:
    """Broadcast fill must hit every band, not only channel 0."""
    bands = np.arange(24, dtype=np.float32).reshape(3, 2, 4)
    original = bands.copy()
    scl = np.array(
        [
            [4, 4, 9, 9],
            [4, 4, 9, 9],
        ],
        dtype=np.uint8,
    )
    fill = 42.0
    masked = apply_cloud_mask(bands, scl, fill_value=fill)
    assert np.all(masked[:, :, 2:4] == fill)
    assert np.array_equal(masked[:, :, 0:2], original[:, :, 0:2])
    assert masked.shape[0] == 3


def test_apply_cloud_mask_does_not_mutate_input() -> None:
    bands = np.ones((2, 2, 2), dtype=np.float32) * 7.0
    original = bands.copy()
    scl = np.array([[9, 4], [4, 9]], dtype=np.uint8)
    apply_cloud_mask(bands, scl, fill_value=0.0)
    assert np.array_equal(bands, original)


def test_apply_cloud_mask_zero_and_one_channel() -> None:
    scl = np.array([[4, 9], [4, 9]], dtype=np.uint8)
    zero = np.ones((0, 2, 2), dtype=np.float32)
    masked_zero = apply_cloud_mask(zero, scl, fill_value=0.0)
    assert masked_zero.shape == (0, 2, 2)

    one = np.ones((1, 2, 2), dtype=np.float32) * 5.0
    masked_one = apply_cloud_mask(one, scl, fill_value=0.0)
    assert masked_one[0, 0, 0] == pytest.approx(5.0)
    assert masked_one[0, 0, 1] == pytest.approx(0.0)


def test_apply_cloud_mask_empty_spatial() -> None:
    bands = np.ones((3, 0, 0), dtype=np.float32)
    scl = np.zeros((0, 0), dtype=np.uint8)
    masked = apply_cloud_mask(bands, scl, fill_value=1.0)
    assert masked.shape == (3, 0, 0)


def test_apply_cloud_mask_fortran_order_matches_c_order() -> None:
    bands_c = np.arange(12, dtype=np.float32).reshape(3, 2, 2)
    bands_f = np.asfortranarray(bands_c)
    scl = np.array([[4, 9], [4, 9]], dtype=np.uint8)
    masked_c = apply_cloud_mask(bands_c, scl, fill_value=0.0)
    masked_f = apply_cloud_mask(bands_f, scl, fill_value=0.0)
    assert np.array_equal(masked_c, masked_f)


def test_apply_cloud_mask_scl_shape_mismatch_raises() -> None:
    bands = np.ones((2, 2, 2), dtype=np.float32)
    scl = np.ones((3, 3), dtype=np.uint8)
    with pytest.raises((IndexError, ValueError)):
        apply_cloud_mask(bands, scl, fill_value=0.0)


def test_evaluate_cloud_mask_empty_and_no_data() -> None:
    """Verifies handling of empty and pure no-data SCL arrays."""
    empty_scl = np.array([], dtype=np.uint8)
    res_empty = evaluate_cloud_mask(empty_scl)
    assert res_empty.excluded_from_training is True

    no_data_scl = np.zeros((10, 10), dtype=np.uint8)  # Class 0: NO_DATA
    res_no_data = evaluate_cloud_mask(no_data_scl)
    assert res_no_data.valid_pixels == 0
    assert res_no_data.excluded_from_training is True
