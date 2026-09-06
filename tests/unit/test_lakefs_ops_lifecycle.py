"""Unit tests for LakeFSOps covering all lifecycle methods and edge cases."""

from __future__ import annotations

import logging

import pytest

from orbital_drift.data.lakefs_ops import SIMULATED_COMMIT_ID_LENGTH, LakeFSOps


def test_lakefs_ops_initialization_defaults() -> None:
    """Verifies default constructor arguments."""
    ops = LakeFSOps()
    assert ops.endpoint_url == "http://localhost:8000"
    assert ops.repository == "orbital-drift"
    assert ops.main_branch == "main"
    assert ops.access_key == ""
    assert ops.secret_key == ""


def test_lakefs_ops_commit_scene_default_branch(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies commit_scene with default branch and metadata."""
    ops = LakeFSOps(repository="test-repo", main_branch="main")
    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        commit_id = ops.commit_scene("scene-001")

    assert isinstance(commit_id, str)
    assert len(commit_id) == SIMULATED_COMMIT_ID_LENGTH
    # T056: log messages now include [SIMULATED] prefix
    assert "[SIMULATED] lakeFS commit" in caplog.text
    assert "for scene 'scene-001'" in caplog.text


def test_lakefs_ops_commit_scene_custom_branch_and_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies commit_scene with custom branch and metadata dictionary."""
    ops = LakeFSOps(repository="test-repo", main_branch="main")
    metadata = {"cloud_cover": 0.05, "sensor": "Sentinel-2"}
    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        commit_id = ops.commit_scene("scene-002", metadata=metadata, branch="staging")

    assert isinstance(commit_id, str)
    assert len(commit_id) == SIMULATED_COMMIT_ID_LENGTH
    assert "branch 'staging'" in caplog.text
    assert "for scene 'scene-002'" in caplog.text


def test_lakefs_ops_create_experiment_branch_defaults(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies create_experiment_branch with default source branch."""
    ops = LakeFSOps(repository="test-repo", main_branch="main")
    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        branch = ops.create_experiment_branch("exp-42")

    assert branch == "exp-exp-42"
    assert "from 'main'" in caplog.text


def test_lakefs_ops_pin_dataset_snapshot_default_tag(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies pin_dataset_snapshot with default tag generation."""
    ops = LakeFSOps(repository="test-repo")
    commit_id = "0123456789abcdef"
    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        meta = ops.pin_dataset_snapshot(commit_id)

    assert meta["repository"] == "test-repo"
    assert meta["commit_id"] == commit_id
    assert meta["tag"] == "snapshot-01234567"
    assert "pinned_at" in meta
    assert meta.get("simulated") is True  # T056: explicit simulation marker
    # T056: log messages now include [SIMULATED] prefix
    assert "[SIMULATED] lakeFS snapshot" in caplog.text


def test_lakefs_ops_pin_dataset_snapshot_custom_tag(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies pin_dataset_snapshot with explicit custom tag."""
    ops = LakeFSOps(repository="test-repo")
    commit_id = "0123456789abcdef"
    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        meta = ops.pin_dataset_snapshot(commit_id, tag_name="v1.0.0-gold")

    assert meta["tag"] == "v1.0.0-gold"
    # T056: log messages now include [SIMULATED] prefix
    assert "[SIMULATED] lakeFS snapshot 'v1.0.0-gold'" in caplog.text


def test_commit_scene_produces_deterministic_ids() -> None:
    """T056: Same inputs must produce the same commit ID for reproducibility.

    Prior to T056, commit_scene included time.time() in the hash payload,
    making IDs non-deterministic and breaking test reproducibility.
    """
    ops = LakeFSOps(repository="test-repo", main_branch="main")
    metadata = {"cloud_cover": 0.05}

    # Call twice with identical inputs
    id1 = ops.commit_scene("scene-001", metadata=metadata, branch="dev")
    id2 = ops.commit_scene("scene-001", metadata=metadata, branch="dev")

    # Must be identical
    assert id1 == id2, "commit IDs should be deterministic given same inputs"

    # Different inputs must produce different IDs
    id3 = ops.commit_scene("scene-002", metadata=metadata, branch="dev")
    assert id1 != id3, "different scene_id should produce different commit ID"

    id4 = ops.commit_scene("scene-001", metadata={"cloud_cover": 0.10}, branch="dev")
    assert id1 != id4, "different metadata should produce different commit ID"
