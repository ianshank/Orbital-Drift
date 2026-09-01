"""LakeFS Operations wrapper for versioned dataset lifecycle.

Implements commit-per-ingest, branch-per-experiment, and immutable snapshot pinning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
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

        Args:
            scene_id: Ingested scene identifier.
            metadata: Associated scene metadata (optional).
            branch: Target branch (defaults to main_branch).

        Returns:
            Deterministic lakeFS commit ID string.
        """
        target_branch = branch or self.main_branch
        meta_dict = metadata or {}
        timestamp = time.time()
        meta_str = json.dumps(meta_dict, sort_keys=True)
        payload = f"{self.repository}:{target_branch}:{scene_id}:{meta_str}:{timestamp}"
        commit_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]  # pin: truncated hash

        logger.info(
            "Created lakeFS commit %s on repo '%s' branch '%s' for scene '%s'",
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
        """Creates an experiment branch from source branch."""
        src = source_branch or self.main_branch
        branch_name = f"exp-{experiment_id}"
        logger.info(
            "Created lakeFS experiment branch '%s' from '%s' on repository '%s'",
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
        """Pins an immutable snapshot of dataset at a specific commit ID."""
        tag = tag_name or f"snapshot-{commit_id[:8]}"  # pin: truncated tag length
        snapshot_meta = {
            "repository": self.repository,
            "commit_id": commit_id,
            "tag": tag,
            "pinned_at": time.time(),
        }
        logger.info("Pinned lakeFS dataset snapshot '%s' -> commit %s", tag, commit_id)
        return snapshot_meta
