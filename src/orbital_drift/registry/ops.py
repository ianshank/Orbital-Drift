"""MLflow Model Registry operations and lifecycle state transitions.

Stages: None -> Staging -> Production -> Archived.

Rollback (T071, D-016/03c) restores exactly the version that held Production
immediately before the current one, read from an ordered promotion history. It never
falls back to "the highest-numbered Archived version", which can be a rejected
challenger that never served. The history is a stack: each promotion to Production
pushes a ``PromotionRecord`` naming the version it replaced, and each rollback pops
the newest record and restores the version it names. Champion/previous_champion
naming waits for B-11's FR-006 amendment; until then this module keeps the stage names.
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal, get_args

from orbital_drift.config import OrbitalDriftConfig

logger = logging.getLogger(__name__)

StageName = Literal["None", "Staging", "Production", "Archived"]

# typing.Literal is not enforced at runtime: a caller passing a typo'd or
# wrong-case string (e.g. "production") for target_stage previously succeeded
# silently, leaving the registry in a state get_stage_version's exact-string
# match can never find while a stale version keeps "serving". Derived from
# StageName itself (not hand-duplicated) so the two cannot drift apart.
_VALID_STAGE_NAMES: Final[frozenset[str]] = frozenset(get_args(StageName))

# The members of StageName under names. Stages are read back out of an untyped
# per-version dict, where a typo'd string literal in a comparison would type-check and
# silently never match; these constants make that comparison a name lookup instead.
# tests/unit/test_registry_ops.py pins that they cover StageName exactly.
NONE_STAGE: Final[StageName] = "None"
STAGING_STAGE: Final[StageName] = "Staging"
PRODUCTION_STAGE: Final[StageName] = "Production"
ARCHIVED_STAGE: Final[StageName] = "Archived"


class _Action(StrEnum):
    """The ``registry_action`` field of every registry decision log record."""

    PROMOTE = "promote"
    ARCHIVE = "archive"
    TRANSITION = "transition"
    ROLLBACK = "rollback"
    ROLLBACK_REJECTED = "rollback_rejected"


class _Reason(StrEnum):
    """The ``reason`` field of decision log records and of NoRollbackTargetError."""

    PROMOTED_OVER_PREVIOUS = "promoted_over_previous_production"
    PROMOTED_WITH_NO_PREVIOUS = "promoted_with_no_previous_production"
    ALREADY_IN_PRODUCTION = "already_in_production"
    SUPERSEDED = "superseded_by_promotion"
    PRODUCTION_VACATED = "production_vacated"
    OUTSIDE_PRODUCTION = "transition_outside_production"
    RESTORED_PREVIOUS = "restored_recorded_previous_production"
    MODEL_NOT_REGISTERED = "model_not_registered"
    NO_PRODUCTION = "no_production_version"
    NO_RECORDED_PREVIOUS = "no_recorded_previous_production"


class NoRollbackTargetError(LookupError):
    """Raised by ``rollback_production`` when there is no version to restore.

    The registry is unchanged when this is raised: the Production version, if any,
    stays in Production and the promotion history is untouched.

    A ``LookupError`` rather than the ``ValueError`` that ``transition_stage`` raises
    for bad input, because nothing about the call is malformed: the target it looks
    up does not exist. ports/registry.py's ``InMemoryModelRegistry`` raises
    ``KeyError``, also a ``LookupError``, in the same situation, so
    ``except LookupError`` handles both
    (tests/unit/test_registry_rollback_conformance.py).

    Attributes:
        model_name: The model whose rollback was refused.
        production_version: The version in Production, which stays there; None if
            no version is in Production.
        reason: Why there is no target: ``"model_not_registered"``,
            ``"no_production_version"``, or ``"no_recorded_previous_production"``
            (the current version was promoted while Production was empty, or it is
            the oldest promotion in the history).
    """

    def __init__(self, model_name: str, production_version: int | None, reason: str) -> None:
        super().__init__(
            f"No rollback target for model {model_name!r} "
            f"(Production version: {production_version}): {reason}"
        )
        self.model_name = model_name
        self.production_version = production_version
        self.reason = reason

    def __reduce__(self) -> tuple[type[NoRollbackTargetError], tuple[str, int | None, str]]:
        # BaseException pickles as cls(*self.args), and self.args holds only the
        # formatted message, so without this an instance could not cross a process
        # boundary (multiprocessing, a task runner's result backend) intact.
        return (type(self), (self.model_name, self.production_version, self.reason))


@dataclass(frozen=True)
class PromotionRecord:
    """One promotion to Production that no rollback has undone.

    Attributes:
        version: The version promoted.
        previous_production_version: The version that held Production at the instant
            ``version`` was promoted; a rollback from ``version`` restores it (what
            B-11's pending FR-006 amendment would call "previous_champion"). None if
            no version held Production then, in which case a rollback from
            ``version`` has no target.
    """

    version: int
    previous_production_version: int | None


def _log_decision(
    level: int,
    message: str,
    *args: object,
    action: _Action,
    model_name: str,
    version: int | None,
    from_version: int | None,
    to_version: int | None,
    reason: _Reason,
) -> None:
    """Emit one registry decision as a structured record on the module logger.

    Authorized by RB-021(a) as a slice of T054 limited to this module. Follows eval/'s
    ``extra=`` pattern, so the observability formatter will render the fields as JSON,
    and a log query will need no message parsing, once T054 wires
    ``configure_logging()``; today the records carry the fields as ``LogRecord``
    attributes, which is what tests/unit/test_registry_ops.py asserts.

    ``from_version`` and ``to_version`` are the Production version before and after
    the decision (equal when Production did not change); ``version`` is the version
    the decision acted on (the restored version for a rollback, None for a refused one).
    """
    logger.log(
        level,
        message,
        *args,
        extra={
            "registry_action": action.value,
            "model_name": model_name,
            "version": version,
            "from_version": from_version,
            "to_version": to_version,
            "reason": reason.value,
        },
        stacklevel=2,
    )


def _describe(version: int | None) -> str:
    """Render an optional version for a human-readable log message."""
    return "none" if version is None else f"v{version}"


def _no_target_reason(registered: bool, production_version: int | None) -> _Reason:
    if not registered:
        return _Reason.MODEL_NOT_REGISTERED
    if production_version is None:
        return _Reason.NO_PRODUCTION
    return _Reason.NO_RECORDED_PREVIOUS


class ModelRegistryOps:
    """Manages MLflow model stages and rollback operations."""

    def __init__(
        self,
        tracking_uri: str | None = None,
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Initializes registry state.

        `tracking_uri` resolves with precedence (RB-010 Part 5: per-module
        config wiring): explicit argument > `config.mlflow_tracking_uri` >
        the pre-existing hardcoded `"http://localhost:5000"` default -- so a
        caller that passes neither sees identical behaviour to before this
        wiring. Purely additive on top of Part 10's locking and
        `target_stage` validation below; neither is touched here.
        """
        self.tracking_uri = (
            tracking_uri
            if tracking_uri is not None
            else (
                config.mlflow_tracking_uri
                if config is not None
                else "http://localhost:5000"  # pin: fallback default (config-wired above)
            )
        )
        self._mock_registry: dict[str, dict[int, dict[str, Any]]] = {}
        # T071: per model, the promotions to Production that no rollback has undone,
        # oldest first.
        # Mutated only while self._lock is held, in the same critical section as the
        # stage change it records, so the newest record always names the current
        # Production version (or Production has since been vacated explicitly).
        self._promotion_history: dict[str, list[PromotionRecord]] = {}
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
        # protects and no more. Since T071 it guards self._promotion_history too.
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

        `metadata` is deep-copied (code-hygiene review finding N-12, RB-021):
        storing the caller's dict by reference let any later mutation of it, at
        any nesting depth, rewrite the registered version's record.

        Returns:
            Assigned integer version number.
        """
        stored_metadata = copy.deepcopy(metadata) if metadata is not None else {}
        with self._lock:
            if model_name not in self._mock_registry:
                self._mock_registry[model_name] = {}

            version = len(self._mock_registry[model_name]) + 1
            self._mock_registry[model_name][version] = {
                "run_id": run_id,
                "artifact_path": artifact_path,
                "stage": NONE_STAGE,
                "metadata": stored_metadata,
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

        Promotion history (T071): promoting a version that is not already in
        Production appends a PromotionRecord naming the version it replaced
        (None if Production was empty) to the model's history. Re-promoting the
        version already in Production is a no-op that records nothing. Moving
        the Production version to any other stage vacates Production; its record
        stays, but the next promotion records no previous version, so a rollback
        from that promotion has no target. A transition that neither enters nor
        leaves Production records nothing, which is why a version that never held
        Production (a rejected challenger archived straight from None or Staging)
        is unreachable by rollback_production.

        Thread-safe: the existence check, the archive-then-set sequence and
        the history update are serialised by self._lock, so two concurrent
        promotions of different versions to Production cannot both observe
        "no other Production version yet" and both succeed, and the history
        can never disagree with the stages.

        Raises:
            ValueError: target_stage is not a valid StageName (Literal is
                not enforced at runtime -- see _VALID_STAGE_NAMES); the
                model or version is not registered; or archive_existing is
                False and promoting `version` to Production would create a
                second, simultaneous Production version. Nothing changes.
        """
        if target_stage not in _VALID_STAGE_NAMES:
            raise ValueError(
                f"Invalid target_stage {target_stage!r}; must be one of "
                f"{sorted(_VALID_STAGE_NAMES)}"
            )

        with self._lock:
            versions = self._mock_registry.get(model_name)
            if versions is None or version not in versions:
                raise ValueError(f"Model {model_name} v{version} not found in registry")

            if target_stage == PRODUCTION_STAGE:
                self._promote_locked(model_name, versions, version, archive_existing)
                return True

            vacating = versions[version]["stage"] == PRODUCTION_STAGE
            production = (
                version if vacating else self.get_stage_version(model_name, PRODUCTION_STAGE)
            )
            versions[version]["stage"] = target_stage
            _log_decision(
                logging.WARNING if vacating else logging.INFO,
                "Transitioned model '%s' v%d -> %s%s",
                model_name,
                version,
                target_stage,
                "; no version is in Production now" if vacating else "",
                action=_Action.ARCHIVE if target_stage == ARCHIVED_STAGE else _Action.TRANSITION,
                model_name=model_name,
                version=version,
                from_version=production,
                to_version=None if vacating else production,
                reason=_Reason.PRODUCTION_VACATED if vacating else _Reason.OUTSIDE_PRODUCTION,
            )
            return True

    def _promote_locked(
        self,
        model_name: str,
        versions: dict[int, dict[str, Any]],
        version: int,
        archive_existing: bool,
    ) -> None:
        """Promotes `version` to Production and records it. Caller holds self._lock."""
        if versions[version]["stage"] == PRODUCTION_STAGE:
            _log_decision(
                logging.INFO,
                "Model '%s' v%d is already in Production; promotion history unchanged",
                model_name,
                version,
                action=_Action.PROMOTE,
                model_name=model_name,
                version=version,
                from_version=version,
                to_version=version,
                reason=_Reason.ALREADY_IN_PRODUCTION,
            )
            return

        other_production = sorted(
            v for v, data in versions.items() if data["stage"] == PRODUCTION_STAGE
        )
        if other_production and not archive_existing:
            raise ValueError(
                f"Model {model_name} version(s) {other_production} already in "
                "Production; pass archive_existing=True (default) or archive "
                "them first -- archive_existing=False here would leave two "
                "versions simultaneously in Production"
            )
        # At most one entry since RB-010 Part 10 / T062; if a corrupted state ever
        # held more, the highest is the one get_stage_version reported as Production.
        previous = other_production[-1] if other_production else None
        for superseded in other_production:
            versions[superseded]["stage"] = ARCHIVED_STAGE
            _log_decision(
                logging.INFO,
                "Archived prior Production model '%s' v%d",
                model_name,
                superseded,
                action=_Action.ARCHIVE,
                model_name=model_name,
                version=superseded,
                from_version=superseded,
                to_version=version,
                reason=_Reason.SUPERSEDED,
            )

        versions[version]["stage"] = PRODUCTION_STAGE
        self._promotion_history.setdefault(model_name, []).append(
            PromotionRecord(version=version, previous_production_version=previous)
        )
        _log_decision(
            logging.INFO,
            "Promoted model '%s' v%d to Production (replaced: %s)",
            model_name,
            version,
            _describe(previous),
            action=_Action.PROMOTE,
            model_name=model_name,
            version=version,
            from_version=previous,
            to_version=version,
            reason=(
                _Reason.PROMOTED_OVER_PREVIOUS
                if previous is not None
                else _Reason.PROMOTED_WITH_NO_PREVIOUS
            ),
        )

    def promotion_history(self, model_name: str) -> tuple[PromotionRecord, ...]:
        """Returns the model's promotions to Production that no rollback has undone.

        Oldest first. The newest record is the promotion that put the current
        Production version there, unless Production has since been vacated by an
        explicit transition. rollback_production pops the newest record and
        restores its previous_production_version. A vacated version's record stays
        in the history, but no rollback reaches it: the next promotion records no
        previous version, so a rollback from it raises instead of popping further.
        The tuple is an immutable snapshot taken under self._lock, so it never shows
        half of a concurrent promotion or rollback. An unknown model has an empty
        history.
        """
        with self._lock:
            return tuple(self._promotion_history.get(model_name, ()))

    def get_stage_version(self, model_name: str, stage: StageName) -> int | None:
        """Finds the current version number for a given stage."""
        if model_name not in self._mock_registry:
            return None
        for v, data in sorted(self._mock_registry[model_name].items(), reverse=True):
            if data["stage"] == stage:
                return v
        return None

    def rollback_production(self, model_name: str) -> int:
        """Restores the version that held Production immediately before the current one.

        The target is the previous_production_version of the newest promotion
        record, which must be the promotion of the current Production version. On
        success that record is discarded, the current version moves to Archived, and
        the target moves to Production from whatever stage it holds now.

        Repeated rollbacks use stack semantics, defined here because T071's
        acceptance requires that "repeated-rollback semantics are defined and
        tested". They are compatible with, not bound by, B-11's proposed "rollback
        moves it to the recorded previous_champion", which waits for its FR-006
        amendment. Because the record is discarded, a second rollback restores the
        version that held Production before the restored one, and so on back to the
        oldest promotion still in the history; one more rollback then raises. A
        version rolled away from is never restored by a later rollback, and a version
        that never held Production is never a target. Promoting after a rollback
        pushes a new record on top of the remaining ones.

        Thread-safe (T062): the read of the history and the current Production
        version, the history pop, and both stage writes are serialised by
        self._lock, so concurrent rollbacks and promotions cannot interleave and
        corrupt the single-Production invariant or the history.

        Returns:
            The version now in Production.

        Raises:
            NoRollbackTargetError: the model is not registered, no version is in
                Production, or the current Production version was promoted while
                Production was empty (including the oldest promotion in the
                history). The registry is unchanged.
        """
        with self._lock:
            versions = self._mock_registry.get(model_name)
            current = self.get_stage_version(model_name, PRODUCTION_STAGE)
            history = self._promotion_history.get(model_name, [])
            latest = history[-1] if history else None
            target = (
                latest.previous_production_version
                if latest is not None and latest.version == current
                else None
            )
            if versions is None or current is None or target is None:
                reason = _no_target_reason(versions is not None, current)
                _log_decision(
                    logging.WARNING,
                    "Rejected rollback of model '%s': %s; Production unchanged (%s)",
                    model_name,
                    reason.value,
                    _describe(current),
                    action=_Action.ROLLBACK_REJECTED,
                    model_name=model_name,
                    version=None,
                    from_version=current,
                    to_version=current,
                    reason=reason,
                )
                raise NoRollbackTargetError(model_name, current, reason.value)

            history.pop()
            versions[current]["stage"] = ARCHIVED_STAGE
            versions[target]["stage"] = PRODUCTION_STAGE
            _log_decision(
                logging.INFO,
                "Rolled back model '%s': promoted v%d to Production (archived v%d)",
                model_name,
                target,
                current,
                action=_Action.ROLLBACK,
                model_name=model_name,
                version=target,
                from_version=current,
                to_version=target,
                reason=_Reason.RESTORED_PREVIOUS,
            )
            return target
