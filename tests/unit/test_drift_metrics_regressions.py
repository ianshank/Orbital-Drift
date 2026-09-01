"""Regression tests for drift/metrics.py's config wiring and RNG reproducibility.

RB-010 Part 5 (docs/decision-log.md 2026-09-01 entry; per-module config
wiring). This part's task brief found three confirmed defects in this file:

1. ``calculate_band_drift``/``evaluate_scene_drift`` independently re-declared
   ``psi_threshold``/``ks_alpha`` defaults instead of sourcing them from
   ``OrbitalDriftConfig`` (Constitution Principle III -- No Hardcoded Values).
2. A THIRD threshold -- the bare ``0.10`` in
   ``is_drifted = (psi >= psi_threshold) or (psi >= 0.10 and ks_pval <
   ks_alpha)`` -- had no name and no config field at all until
   ``OrbitalDriftConfig.psi_moderate_threshold`` was added in RB-010 Part 4.
3. The KS-test subsampling path (arrays over ``max_samples=5000`` elements --
   which is essentially every realistic scene, since a single 256x256 patch
   is 65,536 pixels) called unseeded ``numpy.random.choice``, mutating
   process-global ``numpy.random`` state and making drift verdicts
   non-reproducible run-to-run on identical inputs -- a Constitution
   Principle IV violation in the exact module that gates automatic
   retraining.

Each class below reproduces one of these before asserting the fixed
behaviour, per this repo's TDD protocol: every test here fails against the
pre-fix code (either because the ``rng``/``config``/``psi_moderate_threshold``
keyword or the ``DEFAULT_*`` constant does not exist yet, or because the
assertion about its effect on ``is_drifted``/reproducibility does not hold)
and passes after.
"""

from __future__ import annotations

import numpy as np
import pytest

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.drift.metrics import (
    DEFAULT_KS_ALPHA,
    DEFAULT_PSI_MODERATE_THRESHOLD,
    DEFAULT_PSI_THRESHOLD,
    calculate_band_drift,
    evaluate_scene_drift,
)

# Not real credentials -- fixed test doubles for OrbitalDriftConfig's required
# lakeFS fields, irrelevant to every test below (mirrors
# tests/unit/test_config.py's _TEST_ACCESS_KEY/_TEST_SECRET_KEY convention).
_TEST_ACCESS_KEY = "drift-metrics-test-access-value"
_TEST_SECRET_KEY = "drift-metrics-test-secret-value"  # noqa: S105 -- test double, not a real credential

# A per-band shape safely over calculate_band_drift's max_samples=5000 cap,
# so every test that uses it genuinely exercises the KS subsampling path --
# not just the (unsampled) PSI computation.
_OVER_SUBSAMPLE_CAP_SHAPE = (90, 90)  # 8100 elements


def _config(
    *,
    psi_threshold: float = DEFAULT_PSI_THRESHOLD,
    ks_alpha: float = DEFAULT_KS_ALPHA,
    psi_moderate_threshold: float = DEFAULT_PSI_MODERATE_THRESHOLD,
) -> OrbitalDriftConfig:
    """Builds a config with valid-but-fake lakeFS credentials plus the three
    drift thresholds this module cares about."""
    return OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        psi_threshold=psi_threshold,
        ks_alpha=ks_alpha,
        psi_moderate_threshold=psi_moderate_threshold,
    )


def _subsampling_band_pair() -> tuple[np.ndarray, np.ndarray]:
    """Two >5000-element bands with a mild, non-zero shift.

    Deterministic construction (fixed seeds baked into the fixture data
    itself, independent of whatever ``rng`` a test passes to
    ``calculate_band_drift``) so this fixture is identical across test runs
    regardless of the code path under test.
    """
    ref = np.random.default_rng(101).normal(loc=1000.0, scale=100.0, size=_OVER_SUBSAMPLE_CAP_SHAPE)
    tgt = np.random.default_rng(202).normal(loc=1030.0, scale=100.0, size=_OVER_SUBSAMPLE_CAP_SHAPE)
    assert ref.size > 5000, "fixture must exceed max_samples to exercise KS subsampling"
    assert tgt.size > 5000, "fixture must exceed max_samples to exercise KS subsampling"
    return ref, tgt


def _moderate_psi_band_pair() -> tuple[np.ndarray, np.ndarray]:
    """A fixed, deterministic band pair whose PSI (~0.2045) sits strictly
    between the default ``psi_moderate_threshold`` (0.10) and
    ``psi_threshold`` (0.25), with an extremely significant KS p-value --
    so the "moderate PSI + KS-corroborated" branch is the ONLY thing that
    can make ``is_drifted`` True for this pair at default thresholds; the
    primary ``psi_threshold`` branch never crosses. Values pinned by
    tests/unit/test_drift_metrics_regressions.py's own exploration; see
    ``TestPsiModerateThresholdIsConsulted.test_moderate_path_fires_at_default_thresholds``
    for the self-checking assertions that keep this claim honest.
    """
    ref = np.random.default_rng(4242).normal(loc=1000.0, scale=100.0, size=6000)
    tgt = np.random.default_rng(4243).normal(loc=1045.0, scale=100.0, size=6000)
    return ref, tgt


# ═══════════════════════════════════════════════════════════════════════════════
# Finding: rng is a required, explicit parameter (no silent global-state fallback)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGeneratorIsRequired:
    """``rng`` has no default -- omitting it is a caller error, not a silent
    fallback to unseeded global ``numpy.random`` state."""

    def test_calculate_band_drift_rejects_missing_rng(self) -> None:
        ref, tgt = _subsampling_band_pair()
        with pytest.raises(TypeError, match="rng"):
            calculate_band_drift(ref, tgt, band_name="B04")  # type: ignore[call-arg]

    def test_evaluate_scene_drift_rejects_missing_rng(self) -> None:
        ref, tgt = _subsampling_band_pair()
        ref_bands = np.stack([ref])
        tgt_bands = np.stack([tgt])
        with pytest.raises(TypeError, match="rng"):
            evaluate_scene_drift(ref_bands, tgt_bands, band_names=("B04",))  # type: ignore[call-arg]


# ═══════════════════════════════════════════════════════════════════════════════
# Finding: unseeded np.random.choice made KS subsampling non-reproducible
# ═══════════════════════════════════════════════════════════════════════════════


class TestKSSubsamplingDeterminism:
    """Same seed -> bit-identical PSI/KS results; different seed -> different
    (but still valid) results. Both bands exceed max_samples=5000 so the
    subsampling path this bug lived in is actually exercised."""

    def test_same_seed_yields_bit_identical_results_across_repeated_calls(self) -> None:
        ref, tgt = _subsampling_band_pair()

        result_a = calculate_band_drift(ref, tgt, band_name="B04", rng=np.random.default_rng(7))
        result_b = calculate_band_drift(ref, tgt, band_name="B04", rng=np.random.default_rng(7))

        assert result_a == result_b
        assert result_a.psi == result_b.psi
        assert result_a.ks_statistic == result_b.ks_statistic
        assert result_a.ks_pvalue == result_b.ks_pvalue
        assert result_a.is_drifted == result_b.is_drifted

    def test_different_seed_yields_different_but_valid_results(self) -> None:
        ref, tgt = _subsampling_band_pair()

        result_a = calculate_band_drift(ref, tgt, band_name="B04", rng=np.random.default_rng(7))
        result_c = calculate_band_drift(ref, tgt, band_name="B04", rng=np.random.default_rng(999))

        # PSI never subsamples (calculate_psi always uses the full arrays),
        # so it is identical regardless of rng; only the KS statistic/
        # p-value -- which depend on which 5000-of-8100 elements were drawn
        # -- should differ between the two seeds.
        assert result_a.psi == result_c.psi
        assert result_a.ks_statistic != result_c.ks_statistic
        assert result_a.ks_pvalue != result_c.ks_pvalue

        for result in (result_a, result_c):
            assert 0.0 <= result.ks_statistic <= 1.0
            assert 0.0 <= result.ks_pvalue <= 1.0
            assert np.isfinite(result.psi)

    def test_reusing_one_generator_instance_advances_state_between_calls(self) -> None:
        """Documents the correct usage boundary: a *single* ``Generator``
        instance reused across two calls is expected to yield different
        subsamples -- it is a stateful stream, same as any other
        ``numpy.random.Generator`` consumer. Reproducibility requires
        constructing a *fresh*, identically-seeded ``Generator`` per
        call/run (as the test above does), not reusing one instance across
        calls that must each be reproducible independently.
        """
        ref, tgt = _subsampling_band_pair()
        shared_rng = np.random.default_rng(7)

        first = calculate_band_drift(ref, tgt, band_name="B04", rng=shared_rng)
        second = calculate_band_drift(ref, tgt, band_name="B04", rng=shared_rng)

        assert first.ks_statistic != second.ks_statistic

    def test_evaluate_scene_drift_same_seed_is_reproducible(self) -> None:
        ref, tgt = _subsampling_band_pair()
        ref_bands = np.stack([ref, ref])
        tgt_bands = np.stack([tgt, tgt])
        band_names = ("B04", "B08")

        report_a = evaluate_scene_drift(
            ref_bands,
            tgt_bands,
            band_names=band_names,
            scene_id="scene-repro",
            rng=np.random.default_rng(55),
        )
        report_b = evaluate_scene_drift(
            ref_bands,
            tgt_bands,
            band_names=band_names,
            scene_id="scene-repro",
            rng=np.random.default_rng(55),
        )

        assert report_a == report_b
        assert report_a.max_psi == report_b.max_psi
        assert report_a.min_ks_pvalue == report_b.min_ks_pvalue


# ═══════════════════════════════════════════════════════════════════════════════
# Finding: the bare 0.10 in the moderate-PSI/KS-corroboration branch was dead
# to configuration -- now named `psi_moderate_threshold` and consulted
# ═══════════════════════════════════════════════════════════════════════════════


class TestPsiModerateThresholdIsConsulted:
    """Proves ``psi_moderate_threshold`` actually gates the secondary drift
    signal (not dead code) by changing the verdict when it changes."""

    def test_moderate_path_fires_at_default_thresholds(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        result = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_threshold=DEFAULT_PSI_THRESHOLD,
            ks_alpha=DEFAULT_KS_ALPHA,
            psi_moderate_threshold=DEFAULT_PSI_MODERATE_THRESHOLD,
        )

        # Self-checking: confirms the fixture actually lands where the
        # module docstring above claims before trusting is_drifted below.
        assert DEFAULT_PSI_MODERATE_THRESHOLD <= result.psi < DEFAULT_PSI_THRESHOLD
        assert result.ks_pvalue < DEFAULT_KS_ALPHA
        assert result.is_drifted is True

    def test_raising_moderate_threshold_above_observed_psi_disables_the_path(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        result = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_threshold=DEFAULT_PSI_THRESHOLD,
            ks_alpha=DEFAULT_KS_ALPHA,
            psi_moderate_threshold=0.30,  # above this pair's ~0.2045 PSI
        )

        assert result.psi < 0.30
        assert result.is_drifted is False

    def test_moderate_path_requires_ks_corroboration_not_psi_alone(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        result = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_threshold=DEFAULT_PSI_THRESHOLD,
            ks_alpha=1e-300,  # far below this pair's ks_pvalue: never "< ks_alpha"
            psi_moderate_threshold=DEFAULT_PSI_MODERATE_THRESHOLD,
        )

        assert result.psi >= DEFAULT_PSI_MODERATE_THRESHOLD
        assert not (result.ks_pvalue < 1e-300)
        assert result.is_drifted is False

    def test_primary_threshold_still_fires_independent_of_moderate_threshold(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        result = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_threshold=0.15,  # below this pair's ~0.2045 PSI
            ks_alpha=DEFAULT_KS_ALPHA,
            psi_moderate_threshold=0.99,  # would otherwise disable the secondary path
        )

        assert result.psi >= 0.15
        assert result.is_drifted is True


# ═══════════════════════════════════════════════════════════════════════════════
# Finding: psi_threshold/ks_alpha/psi_moderate_threshold are now sourced from
# OrbitalDriftConfig instead of being duplicated, independently-defaulted
# parameters
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigSourcedThresholds:
    """Resolution order: explicit override > config field > historical
    literal default."""

    def test_no_config_and_no_override_matches_historical_literal_defaults(self) -> None:
        assert DEFAULT_PSI_THRESHOLD == 0.25
        assert DEFAULT_KS_ALPHA == 0.05
        assert DEFAULT_PSI_MODERATE_THRESHOLD == 0.10

        ref, tgt = _moderate_psi_band_pair()
        with_defaults = calculate_band_drift(
            ref, tgt, band_name="B04", rng=np.random.default_rng(1)
        )
        with_explicit_literals = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_threshold=DEFAULT_PSI_THRESHOLD,
            ks_alpha=DEFAULT_KS_ALPHA,
            psi_moderate_threshold=DEFAULT_PSI_MODERATE_THRESHOLD,
        )

        assert with_defaults == with_explicit_literals
        assert with_defaults.is_drifted is True

    def test_config_supplies_thresholds_when_no_explicit_override_given(self) -> None:
        ref, tgt = _moderate_psi_band_pair()

        # At historical/default thresholds this pair drifts via the moderate
        # path (see TestPsiModerateThresholdIsConsulted). A config that
        # raises BOTH thresholds above this pair's PSI, with no explicit
        # per-call override, must suppress that -- proving `config` (not
        # just literals or explicit kwargs) is actually consulted.
        cfg = _config(psi_threshold=0.90, psi_moderate_threshold=0.90)
        result = calculate_band_drift(
            ref, tgt, band_name="B04", rng=np.random.default_rng(1), config=cfg
        )

        assert result.is_drifted is False

    def test_explicit_override_wins_over_config(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        cfg = _config(psi_threshold=0.90, psi_moderate_threshold=0.90)

        result = calculate_band_drift(
            ref,
            tgt,
            band_name="B04",
            rng=np.random.default_rng(1),
            psi_moderate_threshold=DEFAULT_PSI_MODERATE_THRESHOLD,  # explicit override
            config=cfg,
        )

        assert result.is_drifted is True

    def test_evaluate_scene_drift_forwards_config_to_every_band(self) -> None:
        ref, tgt = _moderate_psi_band_pair()
        ref_bands = np.stack([ref])
        tgt_bands = np.stack([tgt])
        cfg = _config(psi_threshold=0.90, psi_moderate_threshold=0.90)

        report = evaluate_scene_drift(
            ref_bands,
            tgt_bands,
            band_names=("B04",),
            scene_id="scene-config-wired",
            rng=np.random.default_rng(1),
            config=cfg,
        )

        assert report.band_results["B04"].is_drifted is False
        assert report.overall_drift_detected is False
