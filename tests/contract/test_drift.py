"""Contract tests for Statistical Drift Engine and Trigger State Machine.

Adheres to Constitution Principle V.
"""

from __future__ import annotations

import numpy as np
import pytest

from orbital_drift.drift.metrics import (
    calculate_psi,
    evaluate_scene_drift,
)
from orbital_drift.drift.trigger import DriftTriggerManager


@pytest.mark.contract
def test_psi_identical_distributions_yields_zero() -> None:
    """Probes that identical distributions yield near-zero PSI."""
    ref = np.random.normal(loc=1000.0, scale=100.0, size=(100, 100))
    tgt = np.random.normal(loc=1000.0, scale=100.0, size=(100, 100))

    psi = calculate_psi(ref, tgt)
    assert psi < 0.05, f"Expected near zero PSI for identical distributions, got {psi}"


@pytest.mark.contract
def test_psi_shifted_distribution_exceeds_threshold() -> None:
    """Probes that heavily shifted distributions exceed significant drift threshold."""
    ref = np.random.normal(loc=1000.0, scale=50.0, size=(100, 100))
    tgt = np.random.normal(loc=2500.0, scale=150.0, size=(100, 100))  # Significant shift

    psi = calculate_psi(ref, tgt)
    assert psi >= 0.25, f"Expected PSI >= 0.25 for shifted distribution, got {psi}"


@pytest.mark.contract
def test_band_drift_and_scene_drift_evaluation() -> None:
    """Probes multi-band scene drift evaluation."""
    ref_bands = np.random.normal(1000.0, 50.0, size=(4, 50, 50))
    # Target bands with shift in band index 2 (B04)
    tgt_bands = ref_bands.copy()
    tgt_bands[2] = np.random.normal(3000.0, 200.0, size=(50, 50))

    report = evaluate_scene_drift(
        reference_bands=ref_bands,
        target_bands=tgt_bands,
        band_names=("B02", "B03", "B04", "B08"),
        psi_threshold=0.25,
    )
    assert report.overall_drift_detected is True
    assert report.band_results["B04"].is_drifted is True
    assert report.band_results["B02"].is_drifted is False


@pytest.mark.contract
def test_drift_trigger_manager_hysteresis() -> None:
    """Probes hysteresis window triggering after consecutive drift events."""
    manager = DriftTriggerManager(hysteresis_window=3, cooldown_scenes=5)

    # Scene 1: drifted (count = 1 -> no trigger)
    d1 = manager.process_scene_verdict(is_drifted=True, scene_id="s1")
    assert d1.should_trigger is False
    assert d1.consecutive_drifted_count == 1

    # Scene 2: drifted (count = 2 -> no trigger)
    d2 = manager.process_scene_verdict(is_drifted=True, scene_id="s2")
    assert d2.should_trigger is False
    assert d2.consecutive_drifted_count == 2

    # Scene 3: drifted (count = 3 -> TRIGGER FIRES!)
    d3 = manager.process_scene_verdict(is_drifted=True, scene_id="s3")
    assert d3.should_trigger is True
    assert manager.is_retraining_active is True


@pytest.mark.contract
def test_drift_trigger_manager_retraining_coalescing_and_completion() -> None:
    """Probes queue-depth-1 coalescing and completion state reset."""
    manager = DriftTriggerManager(hysteresis_window=1, cooldown_scenes=3)

    # Trigger initial retrain
    d1 = manager.process_scene_verdict(is_drifted=True, scene_id="s1")
    assert d1.should_trigger
    assert manager.is_retraining_active == True  # noqa: E712

    # Drift while active -> coalesced
    d2 = manager.process_scene_verdict(is_drifted=True, scene_id="s2")
    assert not d2.should_trigger
    assert "already in progress" in d2.reason

    # Mark completed -> active reset
    manager.mark_retraining_completed()
    assert manager.is_retraining_active == False  # noqa: E712

    # New scene after reset
    d3 = manager.process_scene_verdict(is_drifted=False, scene_id="s3")
    assert not d3.should_trigger
