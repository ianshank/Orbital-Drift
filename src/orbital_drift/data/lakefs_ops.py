"""LakeFS Operations wrapper for versioned dataset lifecycle.

Implements commit-per-ingest, branch-per-experiment, and immutable snapshot pinning.

T056 SIMULATION NOTICE: This module is a pure simulation with NO lakefs-sdk
dependency. All operations are in-memory and no actual lakeFS server is contacted.
Log messages are prefixed with [SIMULATED] to prevent confusion with real operations.
Commit IDs are deterministic (reproducible given the same inputs) for testing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LakeFSOps:
    """Client wrapper for managing lakeFS branches, commits, and dataset snapshots."""

    def __init__(
        self,
        endpoint_url: str = "http://localhost:8000",  # pin: follow-up D-012 F4
        repository: str = "orbital-drift",
        main_branch: str = "main",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        self.endpoint_url = endpoint_url
        self.repository = repository
        self.main_branch = main_branch
        self.access_key = access_key
        self.secret_key = secret_key

    def commit_scene(
        self,
        scene_id: str,
        metadata: dict[str, Any] | None = None,
        branch: str | None = None,
    ) -> str:
        """Records a versioned commit for an ingested scene.

        T056: This is a SIMULATION. No actual lakeFS commit is created.
        Commit IDs are deterministic hashes of (repo, branch, scene_id, metadata)
        so the same inputs always produce the same ID, enabling reproducible tests.

        Args:
            scene_id: Ingested scene identifier.
            metadata: Associated scene metadata (optional).
            branch: Target branch (defaults to main_branch).

        Returns:
            Deterministic simulated commit ID string (first 16 hex chars of SHA-256).
        """
        target_branch = branch or self.main_branch
        meta_dict = metadata or {}
        # T056: Deterministic payload - no timestamp, same inputs = same ID
        meta_str = json.dumps(meta_dict, sort_keys=True)
        payload = f"{self.repository}:{target_branch}:{scene_id}:{meta_str}"
        commit_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        logger.info(
            "[SIMULATED] lakeFS commit %s on repo '%s' branch '%s' for scene '%s'",
            commit_id,
            self.repository,
            target_branch,
            scene_id,
        )
        return commit_id

    def create_experiment_branch(
        self,
        experiment_id: str,
        source_branch: str | None = None,
    ) -> str:
        """Creates an experiment branch from source branch.

        T056: This is a SIMULATION. No actual lakeFS branch is created.
        """
        src = source_branch or self.main_branch
        branch_name = f"exp-{experiment_id}"
        logger.info(
            "[SIMULATED] lakeFS experiment branch '%s' from '%s' on repository '%s'",
            branch_name,
            src,
            self.repository,
        )
        return branch_name

    def pin_dataset_snapshot(
        self,
        commit_id: str,
        tag_name: str | None = None,
    ) -> dict[str, Any]:
        """Pins an immutable snapshot of dataset at a specific commit ID.

        T056: This is a SIMULATION. No actual lakeFS tag is created.
        The pinned_at timestamp IS included because it's metadata about when
        the simulation was called, not part of the determinism guarantee for
        commit IDs (which represent the "what", not "when").
        """
        tag = tag_name or f"snapshot-{commit_id[:8]}"
        snapshot_meta = {
            "repository": self.repository,
            "commit_id": commit_id,
            "tag": tag,
            "pinned_at": _simulation_timestamp(),
            "simulated": True,  # T056: explicit marker for downstream code
        }
        logger.info("[SIMULATED] lakeFS snapshot '%s' -> commit %s", tag, commit_id)
        return snapshot_meta


def _simulation_timestamp() -> float:
    """Returns current time for simulation metadata.

    T056: Factored out so tests can mock this if they need deterministic
    timestamps in snapshot metadata. The commit ID determinism guarantee
    (same inputs = same ID) does NOT extend to snapshot_meta["pinned_at"]
    because that's "when was this simulation called", not "what data was
    committed".
    """
    import time

    return time.time()
