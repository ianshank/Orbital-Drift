"""Statistical Drift Metrics Engine.

Implements industry standard Population Stability Index (PSI) and two-sample
Kolmogorov-Smirnov (KS) test per spectral band, plus prediction distribution shift.
Strict adherence to Constitution Principle II (No bespoke metrics).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, NamedTuple

import numpy as np
from numpy.random import Generator
from scipy import stats

from orbital_drift.config import OrbitalDriftConfig

EPSILON: Final[float] = 1e-6  # pin: statistical smoothing constant, algorithm-intrinsic

# Historical literal defaults (RB-010 Part 5, docs/decision-log.md
# 2026-09-01). These are what calculate_band_drift/evaluate_scene_drift fall
# back to when no OrbitalDriftConfig is passed at all and no explicit
# override is given for the corresponding parameter, keeping every
# pre-existing caller (e.g. this module's earlier callers/tests, written
# before config wiring existed) byte-for-byte unaffected. OrbitalDriftConfig
# gives each the same-valued, documented field: psi_threshold, ks_alpha,
# psi_moderate_threshold.
DEFAULT_PSI_THRESHOLD: Final[float] = 0.25  # pin: fallback default (config-wired above)
DEFAULT_KS_ALPHA: Final[float] = 0.05  # pin: fallback default (config-wired above)
DEFAULT_PSI_MODERATE_THRESHOLD: Final[float] = 0.10  # pin: fallback default (config-wired above)


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
    num_bins: int = 10,  # pin: standard PSI quantile-bin count (see docstring below)
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
    quantiles = np.linspace(0, 100, num_bins + 1)  # pin: percentile scale is 0-100 by definition
    raw_edges = np.unique(np.percentile(ref, quantiles))
    if len(raw_edges) <= 1:
        val = float(ref[0]) if ref.size > 0 else 0.0
        delta = 1e-4 if val == 0.0 else abs(val) * 1e-4  # pin: degenerate-bin-edge perturbation
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
    return float(np.clip(psi_val, 0.0, 10.0))  # pin: PSI ceiling clip, algorithm-intrinsic bound


def _resolve_threshold(
    value: float | None,
    config: OrbitalDriftConfig | None,
    from_config: Callable[[OrbitalDriftConfig], float],
    default: float,
) -> float:
    """Resolves one drift threshold: explicit value > config field > literal default.

    This is the config-wiring pattern applied to every threshold parameter
    below: a value the caller explicitly passed always wins (an explicit
    ``psi_threshold=0.25`` is never silently overridden just because the
    caller also happened to pass a ``config`` whose field disagrees);
    otherwise a supplied ``OrbitalDriftConfig`` sources the field; otherwise
    this module's pre-config-wiring literal ``default`` applies. The last
    branch is what keeps every caller that passes neither an override nor a
    ``config`` byte-for-byte unaffected.
    """
    if value is not None:
        return value
    if config is not None:
        return from_config(config)
    return default


def calculate_band_drift(
    reference_band: np.ndarray,
    target_band: np.ndarray,
    band_name: str = "B04",
    *,
    rng: Generator,
    psi_threshold: float | None = None,
    ks_alpha: float | None = None,
    psi_moderate_threshold: float | None = None,
    config: OrbitalDriftConfig | None = None,
) -> BandDriftResult:
    """Computes PSI and KS 2-sample test for a single band.

    Threshold resolution (``psi_threshold``, ``ks_alpha``,
    ``psi_moderate_threshold``): an explicitly-passed value always wins;
    else a supplied ``config`` sources the field (``config.psi_threshold`` /
    ``config.ks_alpha`` / ``config.psi_moderate_threshold``); else this
    module's historical literal default applies (``DEFAULT_PSI_THRESHOLD`` /
    ``DEFAULT_KS_ALPHA`` / ``DEFAULT_PSI_MODERATE_THRESHOLD``). See
    ``_resolve_threshold``.

    ``is_drifted`` is True when EITHER:
      * PSI alone crosses the primary ``psi_threshold``; OR
      * PSI crosses the lower ``psi_moderate_threshold`` AND the KS test
        rejects the null at ``ks_alpha`` (``ks_pvalue < ks_alpha``) -- a
        KS-corroborated moderate-PSI signal that flags drift even when PSI
        alone hasn't crossed the primary, higher bar.

    ``rng`` is a required, caller-constructed ``numpy.random.Generator``
    (e.g. ``numpy.random.default_rng(seed)`` or ``Generator(PCG64(seed))``,
    mirroring ``orbital_drift.eval.bootstrap``/``orbital_drift.eval.superiority``).
    It is required rather than optional because this module gates automatic
    retraining (Constitution Principle IV, reproducibility): before this fix,
    the KS-test subsampling below called unseeded ``numpy.random.choice``,
    mutating global ``numpy.random`` state and making drift verdicts
    non-reproducible run-to-run on identical inputs -- and that path fires on
    essentially every realistic scene, since a single 256x256 patch (65,536
    pixels) is well over the ``max_samples`` cap below. Construct one fresh
    ``Generator`` per reproducible run: two ``Generator``s built from the
    same seed yield identical subsamples and therefore identical PSI/KS
    verdicts, while reusing one ``Generator`` instance across multiple calls
    deliberately advances its state and yields different subsamples each
    call, same as any other stateful generator.
    """
    resolved_psi_threshold = _resolve_threshold(
        psi_threshold, config, lambda c: c.psi_threshold, DEFAULT_PSI_THRESHOLD
    )
    resolved_ks_alpha = _resolve_threshold(ks_alpha, config, lambda c: c.ks_alpha, DEFAULT_KS_ALPHA)
    resolved_psi_moderate_threshold = _resolve_threshold(
        psi_moderate_threshold,
        config,
        lambda c: c.psi_moderate_threshold,
        DEFAULT_PSI_MODERATE_THRESHOLD,
    )

    psi = calculate_psi(reference_band, target_band)

    ref_sample = reference_band.flatten()
    tgt_sample = target_band.flatten()
    # Downsample for KS test if array is very large to maintain efficiency.
    # Uses the caller-supplied `rng` (never global `numpy.random` state) so
    # identical inputs + seed reproduce identical subsamples -- see the
    # `rng` paragraph above.
    max_samples = 5000  # pin: KS-test subsample computational cap, not a drift-decision threshold
    if ref_sample.size > max_samples:
        ref_sample = rng.choice(ref_sample, max_samples, replace=False)
    if tgt_sample.size > max_samples:
        tgt_sample = rng.choice(tgt_sample, max_samples, replace=False)

    ks_stat, ks_pval = stats.ks_2samp(ref_sample, tgt_sample)
    is_drifted = (psi >= resolved_psi_threshold) or (
        psi >= resolved_psi_moderate_threshold and ks_pval < resolved_ks_alpha
    )
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
    *,
    rng: Generator,
    psi_threshold: float | None = None,
    ks_alpha: float | None = None,
    psi_moderate_threshold: float | None = None,
    config: OrbitalDriftConfig | None = None,
) -> DriftReport:
    """Evaluates multi-band drift across all spectral channels.

    ``rng``, ``psi_threshold``, ``ks_alpha``, ``psi_moderate_threshold``, and
    ``config`` are forwarded unchanged to :func:`calculate_band_drift` for
    every band in ``band_names`` -- see that function's docstring for the
    threshold resolution order and the reproducibility rationale for
    ``rng``. The same ``rng`` instance is shared and advances across all
    bands in this call, so re-running ``evaluate_scene_drift`` with a fresh,
    identically-seeded ``Generator`` reproduces the same per-band subsamples
    (and therefore the same report) call-for-call.
    """
    results: dict[str, BandDriftResult] = {}
    psis: list[float] = []
    pvals: list[float] = []
    drift_flags: list[bool] = []

    for idx, band_name in enumerate(band_names):
        ref_b = reference_bands[idx]
        tgt_b = target_bands[idx]
        res = calculate_band_drift(
            ref_b,
            tgt_b,
            band_name,
            rng=rng,
            psi_threshold=psi_threshold,
            ks_alpha=ks_alpha,
            psi_moderate_threshold=psi_moderate_threshold,
            config=config,
        )
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
