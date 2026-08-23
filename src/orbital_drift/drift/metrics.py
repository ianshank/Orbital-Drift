"""Statistical Drift Metrics Engine.

Implements industry standard Population Stability Index (PSI) and two-sample
Kolmogorov-Smirnov (KS) test per spectral band, plus prediction distribution shift.
Strict adherence to Constitution Principle II (No bespoke metrics).
"""

from __future__ import annotations

from typing import Final, NamedTuple

import numpy as np
from scipy import stats

EPSILON: Final[float] = 1e-6


class BandDriftResult(NamedTuple):
    """Drift metrics for a single spectral band."""

    band_name: str
    psi: float
    ks_statistic: float
    ks_pvalue: float
    is_drifted: bool


class DriftReport(NamedTuple):
    """Aggregate multi-band drift report."""

    scene_id: str
    band_results: dict[str, BandDriftResult]
    max_psi: float
    min_ks_pvalue: float
    overall_drift_detected: bool


def calculate_psi(
    reference: np.ndarray,
    target: np.ndarray,
    num_bins: int = 10,
) -> float:
    """Calculates standard Population Stability Index (PSI) using quantile binning.

    PSI < 0.10: No significant change
    0.10 <= PSI < 0.25: Moderate shift
    PSI >= 0.25: Significant population drift
    """
    ref = reference.flatten()
    tgt = target.flatten()

    if ref.size == 0 or tgt.size == 0:
        return 0.0

    # Determine quantile bins from reference distribution
    quantiles = np.linspace(0, 100, num_bins + 1)
    raw_edges = np.unique(np.percentile(ref, quantiles))
    if len(raw_edges) <= 1:
        val = float(ref[0]) if ref.size > 0 else 0.0
        delta = 1e-4 if val == 0.0 else abs(val) * 1e-4
        bin_edges = np.array([-np.inf, val - delta, val + delta, np.inf])
    else:
        bin_edges = np.concatenate([[-np.inf], raw_edges[1:-1], [np.inf]])

    ref_counts, _ = np.histogram(ref, bins=bin_edges)
    tgt_counts, _ = np.histogram(tgt, bins=bin_edges)

    ref_pct = (ref_counts / ref.size) + EPSILON
    tgt_pct = (tgt_counts / tgt.size) + EPSILON

    # Normalize after epsilon smoothing
    ref_pct = ref_pct / np.sum(ref_pct)
    tgt_pct = tgt_pct / np.sum(tgt_pct)

    psi_val = np.sum((tgt_pct - ref_pct) * np.log(tgt_pct / ref_pct))
    return float(np.clip(psi_val, 0.0, 10.0))


def calculate_band_drift(
    reference_band: np.ndarray,
    target_band: np.ndarray,
    band_name: str = "B04",
    psi_threshold: float = 0.25,
    ks_alpha: float = 0.05,
) -> BandDriftResult:
    """Computes PSI and KS 2-sample test for a single band."""
    psi = calculate_psi(reference_band, target_band)

    ref_sample = reference_band.flatten()
    tgt_sample = target_band.flatten()
    # Downsample for KS test if array is very large to maintain efficiency
    max_samples = 5000
    if ref_sample.size > max_samples:
        ref_sample = np.random.choice(ref_sample, max_samples, replace=False)
    if tgt_sample.size > max_samples:
        tgt_sample = np.random.choice(tgt_sample, max_samples, replace=False)

    ks_stat, ks_pval = stats.ks_2samp(ref_sample, tgt_sample)
    is_drifted = (psi >= psi_threshold) or (psi >= 0.10 and ks_pval < ks_alpha)
    return BandDriftResult(
        band_name=band_name,
        psi=psi,
        ks_statistic=float(ks_stat),
        ks_pvalue=float(ks_pval),
        is_drifted=bool(is_drifted),
    )


def evaluate_scene_drift(
    reference_bands: np.ndarray,
    target_bands: np.ndarray,
    band_names: tuple[str, ...] = ("B02", "B03", "B04", "B08"),
    scene_id: str = "scene-test",
    psi_threshold: float = 0.25,
    ks_alpha: float = 0.05,
) -> DriftReport:
    """Evaluates multi-band drift across all spectral channels."""
    results: dict[str, BandDriftResult] = {}
    psis: list[float] = []
    pvals: list[float] = []
    drift_flags: list[bool] = []

    for idx, band_name in enumerate(band_names):
        ref_b = reference_bands[idx]
        tgt_b = target_bands[idx]
        res = calculate_band_drift(ref_b, tgt_b, band_name, psi_threshold, ks_alpha)
        results[band_name] = res
        psis.append(res.psi)
        pvals.append(res.ks_pvalue)
        drift_flags.append(res.is_drifted)

    overall_drift = any(drift_flags)
    return DriftReport(
        scene_id=scene_id,
        band_results=results,
        max_psi=float(max(psis)) if psis else 0.0,
        min_ks_pvalue=float(min(pvals)) if pvals else 1.0,
        overall_drift_detected=overall_drift,
    )
