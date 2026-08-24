"""MLflow Model Registry operations and lifecycle state transitions.

Stages: None -> Staging -> Production -> Archived.
Supports rollback to prior Production version and shadow evaluation comparisons.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

StageName = Literal["None", "Staging", "Production", "Archived"]


class ModelRegistryOps:
    """Manages MLflow model stages and rollback operations."""

    def __init__(self, tracking_uri: str = "http://localhost:5000") -> None:
        self.tracking_uri = tracking_uri
        self._mock_registry: dict[str, dict[int, dict[str, Any]]] = {}

    def register_model_version(
        self,
        model_name: str,
        run_id: str,
        artifact_path: str = "model",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Registers a new model version from an MLflow run.

        Returns:
            Assigned integer version number.
        """
        if model_name not in self._mock_registry:
            self._mock_registry[model_name] = {}

        version = len(self._mock_registry[model_name]) + 1
        self._mock_registry[model_name][version] = {
            "run_id": run_id,
            "artifact_path": artifact_path,
            "stage": "None",
            "metadata": metadata or {},
        }
        logger.info("Registered model '%s' version %d from run %s", model_name, version, run_id)
        return version

    def transition_stage(
        self,
        model_name: str,
        version: int,
        target_stage: StageName,
        archive_existing: bool = True,
    ) -> bool:
        """Transitions model version to a target stage.

        If target_stage is 'Production' and archive_existing is True,
        any existing Production version is transitioned to 'Archived'.
        """
        if model_name not in self._mock_registry or version not in self._mock_registry[model_name]:
            raise ValueError(f"Model {model_name} v{version} not found in registry")

        if target_stage == "Production" and archive_existing:
            for v, data in self._mock_registry[model_name].items():
                if data["stage"] == "Production" and v != version:
                    data["stage"] = "Archived"
                    logger.info("Archived prior Production model '%s' v%d", model_name, v)

        self._mock_registry[model_name][version]["stage"] = target_stage
        logger.info("Transitioned model '%s' v%d -> %s", model_name, version, target_stage)
        return True

    def get_stage_version(self, model_name: str, stage: StageName) -> int | None:
        """Finds the current version number for a given stage."""
        if model_name not in self._mock_registry:
            return None
        for v, data in sorted(self._mock_registry[model_name].items(), reverse=True):
            if data["stage"] == stage:
                return v
        return None

    def rollback_production(self, model_name: str) -> int | None:
        """Rolls back Production stage to the most recent Archived version."""
        if model_name not in self._mock_registry:
            return None
        curr_prod = self.get_stage_version(model_name, "Production")

        # Find latest archived version to promote
        target_v: int | None = None
        for v, data in sorted(self._mock_registry[model_name].items(), reverse=True):
            if data["stage"] == "Archived" and v != curr_prod:
                target_v = v
                break

        if target_v is None:
            return None

        if curr_prod is not None:
            self._mock_registry[model_name][curr_prod]["stage"] = "Archived"

        self._mock_registry[model_name][target_v]["stage"] = "Production"
        logger.info("Rolled back model '%s': promoted v%d to Production", model_name, target_v)
        return target_v
