"""Promotion-gate statistics adapters with deterministic spatial resampling.

The package intentionally keeps each method in a focused module: spatial
structure, dependent-data uncertainty, paired superiority, calibration, and
ranking answer distinct questions and must not be substituted for one another.
"""

from __future__ import annotations

from orbital_drift.eval.bootstrap import (
    BlockSize,
    BootstrapResult,
    SpatialBlockBootstrapConfig,
    SpatialResample,
    resample_spatial_blocks,
    spatial_block_bootstrap,
)
from orbital_drift.eval.calibration import CalibrationResult, calibration_error
from orbital_drift.eval.ranking import AveragePrecisionResult, average_precision
from orbital_drift.eval.spatial import MoranConfig, MoranResult, morans_i
from orbital_drift.eval.superiority import SuperiorityConfig, SuperiorityResult, superiority_gate

__all__ = [
    "AveragePrecisionResult",
    "BlockSize",
    "BootstrapResult",
    "CalibrationResult",
    "MoranConfig",
    "MoranResult",
    "SpatialBlockBootstrapConfig",
    "SpatialResample",
    "SuperiorityConfig",
    "SuperiorityResult",
    "average_precision",
    "calibration_error",
    "morans_i",
    "resample_spatial_blocks",
    "spatial_block_bootstrap",
    "superiority_gate",
]
