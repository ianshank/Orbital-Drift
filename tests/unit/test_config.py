"""Unit tests for orbital_drift configuration and validation (Constitution Principle III)."""

from __future__ import annotations

import pytest

from orbital_drift.config import OrbitalDriftConfig


def test_config_defaults_and_validation() -> None:
    """Verifies default parameters and custom overrides."""
    cfg = OrbitalDriftConfig()

    assert cfg.aoi_name == "default-aoi"
    assert len(cfg.bands) == 4
    assert "B02" in cfg.bands
    assert "B08" in cfg.bands
    assert 0.0 <= cfg.cloud_cover_max_threshold <= 1.0
    assert cfg.train_device == "cuda:0"
    assert cfg.serve_device == "cuda:1"
    assert cfg.psi_threshold == 0.25
    assert cfg.drift_hysteresis_window == 3


def test_config_environment_variable_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that environment variables override defaults."""
    monkeypatch.setenv("ORBITAL_DRIFT_AOI_NAME", "midwest-croplands")
    monkeypatch.setenv("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "0.10")
    monkeypatch.setenv("ORBITAL_DRIFT_DRIFT_HYSTERESIS_WINDOW", "5")

    cfg = OrbitalDriftConfig()
    assert cfg.aoi_name == "midwest-croplands"
    assert cfg.cloud_cover_max_threshold == 0.10
    assert cfg.drift_hysteresis_window == 5
