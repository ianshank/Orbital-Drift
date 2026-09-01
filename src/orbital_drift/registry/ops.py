"""MLflow Model Registry operations and lifecycle state transitions.

Stages: None -> Staging -> Production -> Archived.
Supports rollback to prior Production version and shadow evaluation comparisons.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Final, Literal, get_args

logger = logging.getLogger(__name__)

StageName = Literal["None", "Staging", "Production", "Archived"]

# typing.Literal is not enforced at runtime: a caller passing a typo'd or
# wrong-case string (e.g. "production") for target_stage previously succeeded
# silently, leaving the registry in a state get_stage_version's exact-string
# match can never find while a stale version keeps "serving". Derived from
# StageName itself (not hand-duplicated) so the two cannot drift apart.
_VALID_STAGE_NAMES: Final[frozenset[str]] = frozenset(get_args(StageName))


class ModelRegistryOps:
    """Manages MLflow model stages and rollback operations."""

    def __init__(self, tracking_uri: str = "http://localhost:5000") -> None:
        self.tracking_uri = tracking_uri
        self._mock_registry: dict[str, dict[int, dict[str, Any]]] = {}
        # register_model_version's version-number assignment (read len(),
        # then write len()+1) and transition_stage's archive-then-set stage
        # transition (scan for the current Production version, then write)
        # are each a non-atomic read-modify-write against self._mock_registry.
        # Concurrent callers can interleave inside either method: two
        # concurrent registrations for the same model can compute the same
        # next version number (lost update), and two concurrent promotions
        # of different versions to Production can both observe "no other
        # Production version yet" and both end up Production simultaneously.
        # get_stage_version would then silently return only the higher
        # version, masking the anomaly with no error or warning. Same bug
        # class, same fix shape, as _MORAN_LOCK in eval/spatial.py.
        #
        # Instance-level (not module-level, unlike _MORAN_LOCK): the hazard
        # there is esda.Moran monkeypatching genuinely process-global state
        # (numpy's global random module), so every caller regardless of
        # instance must serialise. Here the mutable state is
        # self._mock_registry, which is itself instance-scoped — two
        # ModelRegistryOps instances share no state, so a module-level lock
        # would serialise unrelated registries (e.g. independent tests or
        # independent model catalogs running in the same process) for no
        # safety benefit. One lock per instance guards exactly the state it
        # protects and no more.
        self._lock: Final = threading.Lock()

    def register_model_version(
        self,
        model_name: str,
        run_id: str,
        artifact_path: str = "model",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Registers a new model version from an MLflow run.

        Thread-safe: the version-number read-modify-write is serialised by
        self._lock so concurrent registrations for the same model cannot be
        assigned the same version number.

        Returns:
            Assigned integer version number.
        """
        with self._lock:
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

        If target_stage is 'Production' and archive_existing is False while
        another version already holds 'Production', this raises ValueError
        instead of silently leaving two versions simultaneously in
        Production: get_stage_version's reverse-sorted exact-string match
        would then return only the higher version, masking the duplicate
        with no error or warning. A caller that genuinely needs to promote
        without archiving must first archive (or otherwise vacate) the
        prior Production version explicitly.

        Thread-safe: the existence check and the archive-then-set sequence
        are serialised by self._lock, so two concurrent promotions of
        different versions to Production cannot both observe "no other
        Production version yet" and both succeed.

        Raises:
            ValueError: target_stage is not a valid StageName (Literal is
                not enforced at runtime -- see _VALID_STAGE_NAMES); the
                model or version is not registered; or archive_existing is
                False and promoting `version` to Production would create a
                second, simultaneous Production version.
        """
        if target_stage not in _VALID_STAGE_NAMES:
            raise ValueError(
                f"Invalid target_stage {target_stage!r}; must be one of "
                f"{sorted(_VALID_STAGE_NAMES)}"
            )

        with self._lock:
            if (
                model_name not in self._mock_registry
                or version not in self._mock_registry[model_name]
            ):
                raise ValueError(f"Model {model_name} v{version} not found in registry")

            if target_stage == "Production":
                other_production = sorted(
                    v
                    for v, data in self._mock_registry[model_name].items()
                    if data["stage"] == "Production" and v != version
                )
                if other_production and not archive_existing:
                    raise ValueError(
                        f"Model {model_name} version(s) {other_production} already in "
                        "Production; pass archive_existing=True (default) or archive "
                        "them first -- archive_existing=False here would leave two "
                        "versions simultaneously in Production"
                    )
                for v in other_production:
                    self._mock_registry[model_name][v]["stage"] = "Archived"
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
        """Rolls back Production stage to the most recent Archived version.

        Not guarded by self._lock. RB-010 Part 10 scopes locking to the two
        confirmed races in register_model_version and transition_stage; this
        method has the same read-then-write shape against self._mock_registry
        but concurrent-safety here is out of scope for that fix and is not
        claimed.
        """
        curr_prod = self.get_stage_version(model_name, "Production")
        if curr_prod is not None:
            self._mock_registry[model_name][curr_prod]["stage"] = "Archived"

        # Find latest archived version to promote
        if model_name in self._mock_registry:
            for v, data in sorted(self._mock_registry[model_name].items(), reverse=True):
                if data["stage"] == "Archived" and v != curr_prod:
                    data["stage"] = "Production"
                    logger.info("Rolled back model '%s': promoted v%d to Production", model_name, v)
                    return v

        return None
