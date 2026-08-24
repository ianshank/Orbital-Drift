"""Contract tests for the LakeFS dataset lifecycle wrapper."""

from __future__ import annotations

import logging

import pytest

from orbital_drift.data.lakefs_ops import LakeFSOps


@pytest.mark.contract
def test_lakefs_ops_creates_experiment_branch_from_requested_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Names the experiment branch and records the requested source branch."""
    lakefs = LakeFSOps(repository="scene-repository", main_branch="main")

    with caplog.at_level(logging.INFO, logger="orbital_drift.data.lakefs_ops"):
        branch_name = lakefs.create_experiment_branch(
            experiment_id="cloud-mask-v2",
            source_branch="validated-scenes",
        )

    assert branch_name == "exp-cloud-mask-v2"
    assert (
        "Created lakeFS experiment branch 'exp-cloud-mask-v2' from 'validated-scenes' "
        "on repository 'scene-repository'"
    ) in caplog.messages
