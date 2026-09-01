"""Unit tests for Statistical Drift Engine edge cases and numerical stability.

Covers degenerate distributions, zero-variance arrays, identical samples,
and threshold boundary conditions.
"""

from __future__ import annotations

import numpy as np

from orbital_drift.drift.metrics import (
    calculate_band_drift,
    calculate_psi,
    evaluate_scene_drift,
)


def test_psi_identical_distributions() -> None:
    """Verifies that identical reference and target distributions yield PSI ~ 0."""
    data = np.random.normal(loc=2000.0, scale=500.0, size=10000)
    psi = calculate_psi(reference=data, target=data, num_bins=10)
    assert psi < 0.01


def test_psi_completely_disjoint_distributions() -> None:
    """Verifies that completely disjoint distributions yield high PSI > 0.5."""
    ref = np.random.normal(loc=1000.0, scale=100.0, size=10000)
    target = np.random.normal(loc=8000.0, scale=100.0, size=10000)
    psi = calculate_psi(reference=ref, target=target, num_bins=10)
    assert psi > 0.5


def test_psi_zero_variance_constant_array() -> None:
    """Verifies that constant arrays (zero variance) do not cause zero-division crash."""
    ref = np.full(5000, 1500.0, dtype=np.float32)
    target = np.full(5000, 1500.0, dtype=np.float32)
    psi = calculate_psi(reference=ref, target=target, num_bins=10)
    assert np.isfinite(psi)
    assert psi < 0.05


def test_psi_constant_ref_different_constant_target() -> None:
    """Verifies constant reference vs different constant target."""
    ref = np.full(5000, 1000.0, dtype=np.float32)
    target = np.full(5000, 3000.0, dtype=np.float32)
    psi = calculate_psi(reference=ref, target=target, num_bins=10)
    assert np.isfinite(psi)
    assert psi >= 0.25


def test_calculate_band_drift_empty_arrays() -> None:
    """Verifies that empty input arrays return zero PSI and do not crash."""
    result = calculate_band_drift(
        reference_band=np.array([]),
        target_band=np.array([]),
        band_name="B02",
        rng=np.random.default_rng(0),
    )
    assert result.psi == 0.0
    assert result.is_drifted is False


def test_evaluate_scene_drift_stacked_bands() -> None:
    """Verifies evaluate_scene_drift across multi-band stacked cubes."""
    ref_cube = np.random.normal(2000.0, 300.0, size=(4, 64, 64))
    target_cube = np.random.normal(2000.0, 300.0, size=(4, 64, 64))
    report = evaluate_scene_drift(
        reference_bands=ref_cube,
        target_bands=target_cube,
        band_names=("B02", "B03", "B04", "B08"),
        scene_id="scene-001",
        rng=np.random.default_rng(0),
    )
    assert report.scene_id == "scene-001"
    assert len(report.band_results) == 4
    assert report.max_psi < 0.10
    assert report.overall_drift_detected is False
