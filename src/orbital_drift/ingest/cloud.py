"""Sentinel-2 Scene Classification Layer (SCL) cloud masking and evaluation.

SCL Class Codes:
0: NO_DATA
1: SATURATED_OR_DEFECTIVE
2: DARK_AREA_PIXELS
3: CLOUD_SHADOWS
4: VEGETATION
5: NOT_VEGETATED
6: WATER
7: UNCLASSIFIED
8: CLOUD_MEDIUM_PROBABILITY
9: CLOUD_HIGH_PROBABILITY
10: THIN_CIRRUS
11: SNOW
"""

from __future__ import annotations

from typing import Final, NamedTuple

import numpy as np

# SCL classes corresponding to cloud and cloud shadows
CLOUD_CLASSES: Final[tuple[int, ...]] = (3, 8, 9, 10)
VALID_DATA_CLASSES: Final[tuple[int, ...]] = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11)


class CloudEvaluationResult(NamedTuple):
    """Result of cloud mask calculation on an SCL array."""

    total_pixels: int
    valid_pixels: int
    cloud_pixels: int
    cloud_fraction: float
    excluded_from_training: bool

    @property
    def is_usable(self) -> bool:
        """Returns True if cloud fraction is within allowable threshold."""
        return not self.excluded_from_training


def evaluate_cloud_mask(
    scl_array: np.ndarray,
    cloud_threshold: float = 0.20,
) -> CloudEvaluationResult:
    """Evaluates cloud fraction from an SCL 2D numpy array.

    Args:
        scl_array: 2D numpy array of integer SCL class values.
        cloud_threshold: Threshold above which scene is flagged excluded_from_training.

    Returns:
        CloudEvaluationResult containing statistics and exclusion status.
    """
    if scl_array.size == 0:
        return CloudEvaluationResult(
            total_pixels=0,
            valid_pixels=0,
            cloud_pixels=0,
            cloud_fraction=0.0,
            excluded_from_training=True,
        )

    total_pixels = int(scl_array.size)
    valid_mask = np.isin(scl_array, VALID_DATA_CLASSES)
    valid_pixels = int(np.sum(valid_mask))

    if valid_pixels == 0:
        return CloudEvaluationResult(
            total_pixels=total_pixels,
            valid_pixels=0,
            cloud_pixels=0,
            cloud_fraction=1.0,
            excluded_from_training=True,
        )

    cloud_mask = np.isin(scl_array, CLOUD_CLASSES)
    cloud_pixels = int(np.sum(cloud_mask))
    cloud_fraction = float(cloud_pixels / valid_pixels)

    excluded = cloud_fraction > cloud_threshold

    return CloudEvaluationResult(
        total_pixels=total_pixels,
        valid_pixels=valid_pixels,
        cloud_pixels=cloud_pixels,
        cloud_fraction=cloud_fraction,
        excluded_from_training=excluded,
    )


def apply_cloud_mask(
    raster_bands: np.ndarray,
    scl_array: np.ndarray,
    fill_value: float = 0.0,
) -> np.ndarray:
    """Masks cloudy pixels in multi-spectral bands.

    Args:
        raster_bands: (C, H, W) numpy array of spectral bands.
        scl_array: (H, W) numpy array of SCL classes.
        fill_value: Value to replace cloudy pixels with.

    Returns:
        Masked (C, H, W) array.
    """
    cloud_mask = np.isin(scl_array, CLOUD_CLASSES)
    masked = raster_bands.copy()
    for c in range(masked.shape[0]):
        masked[c, cloud_mask] = fill_value
    return masked
