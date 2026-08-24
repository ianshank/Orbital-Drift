"""Unit tests for ModelRegistryOps lifecycle transitions and rollback mechanics."""

from __future__ import annotations

import logging

import pytest

from orbital_drift.registry.ops import ModelRegistryOps


def test_register_model_version_increments_versions() -> None:
    """Verifies incremental versioning per registered model."""
    reg = ModelRegistryOps()
    v1 = reg.register_model_version("unet-s2", "run-101", metadata={"dataset": "ds-1"})
    v2 = reg.register_model_version("unet-s2", "run-102")
    assert v1 == 1
    assert v2 == 2


def test_transition_stage_to_staging() -> None:
    """Verifies transition to Staging stage."""
    reg = ModelRegistryOps()
    v1 = reg.register_model_version("unet-s2", "run-101")
    reg.transition_stage("unet-s2", v1, "Staging")
    assert reg.get_stage_version("unet-s2", "Staging") == 1


def test_transition_stage_success_and_archive_prior_production(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies transition to Production archives prior Production versions."""
    reg = ModelRegistryOps()
    v1 = reg.register_model_version("unet-s2", "run-101")
    v2 = reg.register_model_version("unet-s2", "run-102")

    reg.transition_stage("unet-s2", v1, "Production")
    assert reg.get_stage_version("unet-s2", "Production") == 1

    with caplog.at_level(logging.INFO, logger="orbital_drift.registry.ops"):
        reg.transition_stage("unet-s2", v2, "Production", archive_existing=True)

    assert reg.get_stage_version("unet-s2", "Production") == 2
    assert reg.get_stage_version("unet-s2", "Archived") == 1
    assert "Archived prior Production model 'unet-s2' v1" in caplog.text


def test_transition_stage_non_existent_model_raises_value_error() -> None:
    """Verifies ValueError when transitioning non-existent model or version."""
    reg = ModelRegistryOps()
    with pytest.raises(ValueError, match="not found in registry"):
        reg.transition_stage("unknown-model", 1, "Production")

    reg.register_model_version("known-model", "run-1")
    with pytest.raises(ValueError, match="not found in registry"):
        reg.transition_stage("known-model", 99, "Production")


def test_get_stage_version_missing_model_or_stage() -> None:
    """Verifies None returned when querying unknown models or unassigned stages."""
    reg = ModelRegistryOps()
    assert reg.get_stage_version("unregistered", "Production") is None

    reg.register_model_version("registered", "run-1")
    assert reg.get_stage_version("registered", "Staging") is None


def test_rollback_production_promotes_latest_archived(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies rollback demotes current production and promotes latest archived."""
    reg = ModelRegistryOps()
    v1 = reg.register_model_version("unet", "run-1")
    v2 = reg.register_model_version("unet", "run-2")

    reg.transition_stage("unet", v1, "Production")
    reg.transition_stage("unet", v2, "Production", archive_existing=True)

    # Currently v2 is Production, v1 is Archived.
    assert reg.get_stage_version("unet", "Production") == 2

    with caplog.at_level(logging.INFO, logger="orbital_drift.registry.ops"):
        rolled_back_to = reg.rollback_production("unet")

    assert rolled_back_to == 1
    assert reg.get_stage_version("unet", "Production") == 1
    assert "Rolled back model 'unet': promoted v1 to Production" in caplog.text


def test_rollback_production_with_no_archived_version() -> None:
    """Verifies rollback returns None when no prior archived version exists."""
    reg = ModelRegistryOps()
    assert reg.rollback_production("nonexistent") is None

    v1 = reg.register_model_version("unet", "run-1")
    reg.transition_stage("unet", v1, "Production")
    # Only 1 version exists; no archived version exists.
    assert reg.rollback_production("unet") is None
