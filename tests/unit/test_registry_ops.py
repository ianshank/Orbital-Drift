"""Unit tests for ModelRegistryOps lifecycle transitions and rollback mechanics."""

from __future__ import annotations

import contextlib
import dataclasses
import itertools
import json
import logging
import pickle
import queue
import threading
import time
from collections.abc import Callable, ItemsView, Iterator
from typing import Any, Final, get_args

import pytest

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.observability.context import CORRELATION_ID_FIELD, correlation_scope
from orbital_drift.observability.logging import JsonFormatter
from orbital_drift.registry.ops import (
    ARCHIVED_STAGE,
    NONE_STAGE,
    PRODUCTION_STAGE,
    STAGING_STAGE,
    ModelRegistryOps,
    NoRollbackTargetError,
    PromotionRecord,
    StageName,
)

# Not real credentials -- fixed test doubles for the required lakeFS fields,
# matching tests/unit/test_config.py's `_construct_with_valid_credentials`
# pattern. Used only by TestMlflowTrackingUriConfigWiring below.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


def _build_config(**overrides: object) -> OrbitalDriftConfig:
    return OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        **overrides,  # type: ignore[arg-type]
    )


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


def test_rollback_production_restores_the_previous_production_version(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies rollback archives current Production and restores the version it replaced.

    The two-version happy path. The cases it cannot distinguish from "promote the
    highest-numbered Archived version" (D-016/03c) are in the T071 section below and in
    tests/unit/test_registry_rollback_conformance.py.
    """
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


def test_rollback_production_with_no_target_raises_and_keeps_production() -> None:
    """Regression (T071, D-016/03c): with one version in Production, rollback used to
    archive it and return None, leaving no Production model. It now raises and changes
    nothing; so does a rollback of a model that was never registered."""
    reg = ModelRegistryOps()
    with pytest.raises(NoRollbackTargetError):
        reg.rollback_production("nonexistent")

    v1 = reg.register_model_version("unet", "run-1")
    reg.transition_stage("unet", v1, "Production")
    with pytest.raises(NoRollbackTargetError):
        reg.rollback_production("unet")
    assert reg.get_stage_version("unet", "Production") == v1
    assert reg.get_stage_version("unet", "Archived") is None


# ═══════════════════════════════════════════════════════════════════════════════
# RB-010 Part 10 regressions: target_stage runtime validation, the two
# concurrency races sharing a single missing lock, and the archive_existing
# =False duplicate-Production bug. See docs/decision-log.md RB-010 and
# src/orbital_drift/eval/spatial.py's _MORAN_LOCK for the sibling race this
# fix mirrors.
# ═══════════════════════════════════════════════════════════════════════════════

_RACE_WINDOW_SECONDS: Final = 0.05
"""Deterministic sleep injected by the test doubles below, so thread
interleaving inside the exact non-atomic read each regression test targets
is forced rather than left to scheduler timing (which would make the tests
flaky). Large enough to reliably force a collision every run pre-fix; small
enough that the post-fix serialised total (thread_count * this value) stays
a fraction of a second.
"""


class _SlowLenDict(dict[int, dict[str, Any]]):
    """Test double, not a production pattern.

    Widens register_model_version's ``version = len(...) + 1`` read: the
    sleep happens *inside* ``len()``, after the count is read but before it
    is returned to the caller, so every thread released by a Barrier reads
    the same pre-write count while all are still asleep.
    """

    def __len__(self) -> int:
        count = super().__len__()
        time.sleep(_RACE_WINDOW_SECONDS)
        return count


class _SlowItemsDict(dict[int, dict[str, Any]]):
    """Test double, not a production pattern.

    Widens transition_stage's archive-then-set scan for an existing
    Production version. The per-version dicts are snapshotted (deep enough
    to freeze each entry's "stage" field) immediately on entry -- i.e.
    *before* sleeping -- so the delay widens the window between *reading*
    the current Production holder and *acting* on it, matching the actual
    check-then-act shape of the race. A naive ``time.sleep()`` placed
    before returning the live view (tried first) does NOT reproduce the
    bug: the view stays live, so a thread that wakes second still observes
    the other thread's already-completed write and correctly archives it,
    hiding the race instead of demonstrating it.
    """

    # Deliberately not LSP-compatible with dict.items()'s live-view contract
    # -- see the docstring above; the whole point of this double is to return
    # a frozen snapshot instead of a view onto (possibly still-mutating) live
    # state, which is exactly what makes the delay simulate a stale read.
    def items(self) -> ItemsView[int, dict[str, Any]]:  # type: ignore[override]
        snapshot = {version: dict(data) for version, data in dict.items(self)}
        time.sleep(_RACE_WINDOW_SECONDS)
        return snapshot.items()


class TestTargetStageRuntimeValidation:
    """StageName is a typing.Literal, which Python does not enforce at
    runtime; transition_stage must validate target_stage itself rather than
    silently succeeding on a typo'd or wrong-case value."""

    def test_wrong_case_target_stage_raises_instead_of_silently_succeeding(self) -> None:
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")

        with pytest.raises(ValueError, match="Invalid target_stage"):
            reg.transition_stage("unet-s2", v1, "production")  # type: ignore[arg-type]

        # The rejected call must not have mutated state: no version is
        # reachable under either the garbage string or the correctly-cased
        # stage it was meant to be.
        assert reg.get_stage_version("unet-s2", "Production") is None

    def test_garbage_target_stage_raises(self) -> None:
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")

        with pytest.raises(ValueError, match="Invalid target_stage"):
            reg.transition_stage("unet-s2", v1, "not-a-real-stage")  # type: ignore[arg-type]

    def test_valid_stage_names_are_still_accepted(self) -> None:
        """Positive control: every real StageName value must keep working."""
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")
        for stage in ("Staging", "Production", "Archived", "None"):
            assert reg.transition_stage("unet-s2", v1, stage) is True


class TestRegisterModelVersionConcurrency:
    """Regression test for the lost-update race in register_model_version's
    non-atomic ``version = len(...) + 1`` read-modify-write: two concurrent
    registrations for the same model could previously compute the same next
    version number, and the second write would clobber the first."""

    def test_concurrent_registrations_do_not_collide_on_version_number(self) -> None:
        thread_count = 8
        reg = ModelRegistryOps()
        # Pre-seed the per-model dict with the slow-len double so every
        # thread's length read is widened -- without this, catching the race
        # would depend on GIL scheduling luck.
        reg._mock_registry["concurrent-model"] = _SlowLenDict()
        barrier = threading.Barrier(thread_count)
        results: queue.Queue[int] = queue.Queue()

        def _register() -> None:
            barrier.wait()
            results.put(reg.register_model_version("concurrent-model", "run-x"))

        threads = [threading.Thread(target=_register) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive(), "thread did not complete within the join timeout"

        versions = sorted(results.get_nowait() for _ in range(thread_count))
        expected = list(range(1, thread_count + 1))
        assert versions == expected, f"lost update: expected {expected}, got {versions}"
        assert len(reg._mock_registry["concurrent-model"]) == thread_count


class TestTransitionStageProductionRace:
    """Regression test for the archive-then-set check-then-act race: two
    concurrent promotions of different versions to Production could
    previously both observe "no existing Production version" and both
    succeed, leaving two versions simultaneously Production with no error or
    warning -- get_stage_version would then silently return only the higher
    version."""

    def test_concurrent_promotions_never_leave_two_production_versions(self) -> None:
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")
        v2 = reg.register_model_version("unet-s2", "run-102")
        # Pre-seed with the slow-items double so both threads' scans for an
        # existing Production version are widened to overlap deterministically.
        reg._mock_registry["unet-s2"] = _SlowItemsDict(reg._mock_registry["unet-s2"])

        barrier = threading.Barrier(2)
        errors: queue.Queue[BaseException] = queue.Queue()

        def _promote(version: int) -> None:
            barrier.wait()
            try:
                reg.transition_stage("unet-s2", version, "Production")
            except BaseException as exc:  # surfaced via the queue below, not swallowed
                errors.put(exc)

        t1 = threading.Thread(target=_promote, args=(v1,))
        t2 = threading.Thread(target=_promote, args=(v2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive()
        assert not t2.is_alive()

        collected_errors: list[BaseException] = []
        while not errors.empty():
            collected_errors.append(errors.get_nowait())
        assert collected_errors == [], f"transition_stage raised unexpectedly: {collected_errors}"

        production_versions = [
            v for v, data in reg._mock_registry["unet-s2"].items() if data["stage"] == "Production"
        ]
        assert len(production_versions) == 1, (
            f"two versions simultaneously in Production: {production_versions}"
        )
        # get_stage_version must agree with the direct scan above -- this is
        # exactly the invariant the bug silently violated.
        assert reg.get_stage_version("unet-s2", "Production") == production_versions[0]


class TestArchiveExistingFalseDuplicateProduction:
    """archive_existing=False must not silently create two simultaneous
    Production versions."""

    def test_rejects_when_another_version_already_in_production(self) -> None:
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")
        v2 = reg.register_model_version("unet-s2", "run-102")
        reg.transition_stage("unet-s2", v1, "Production")

        with pytest.raises(ValueError, match="already in Production"):
            reg.transition_stage("unet-s2", v2, "Production", archive_existing=False)

        # The rejected call must not have mutated state.
        assert reg.get_stage_version("unet-s2", "Production") == v1
        assert reg._mock_registry["unet-s2"][v2]["stage"] != "Production"

    def test_allows_first_promotion_with_no_existing_production(self) -> None:
        """Positive control: archive_existing=False must still work when it
        would not create a duplicate."""
        reg = ModelRegistryOps()
        v1 = reg.register_model_version("unet-s2", "run-101")
        assert reg.transition_stage("unet-s2", v1, "Production", archive_existing=False) is True
        assert reg.get_stage_version("unet-s2", "Production") == v1


# ═══════════════════════════════════════════════════════════════════════════════
# T062: rollback_production concurrency regression test. The method has the
# same read-modify-write shape as register_model_version and transition_stage
# (read the current Production version and the newest promotion record, pop the
# record, archive the current version, promote the recorded previous one -- since
# T071; before it, the method scanned for the latest Archived version), but was
# not included in RB-010 Part 10's locking fix.
# ═══════════════════════════════════════════════════════════════════════════════


class TestRollbackProductionConcurrency:
    """Regression test (T062) for the read-modify-write race in rollback_production.

    Without locking, concurrent rollbacks could:
    1. Both see the same current Production version
    2. Both archive it
    3. Both promote a rollback target
    4. Leave registry in an inconsistent state (two Production versions)
    """

    def test_concurrent_rollbacks_maintain_single_production_invariant(self) -> None:
        """Concurrent rollbacks must not leave multiple Production versions.

        The critical invariant is that at most ONE version can be Production at
        any time. With locking, rollbacks serialize and each sees the result of
        the previous one. Without locking, two threads could both see the same
        Production version, both archive it, and both promote a target, leaving
        TWO versions in Production simultaneously.
        """
        thread_count = 2  # Two threads are sufficient to trigger the race
        reg = ModelRegistryOps()

        # Create versions: v1->Production, v2->Production (archives v1),
        # v3->Production (archives v2). End state: v3=Production, v2=Archived, v1=Archived
        for i in range(1, 4):
            v = reg.register_model_version("rollback-model", f"run-{i}")
            reg.transition_stage("rollback-model", v, "Production")

        # Slow down the scan to force thread interleaving
        reg._mock_registry["rollback-model"] = _SlowItemsDict(reg._mock_registry["rollback-model"])

        barrier = threading.Barrier(thread_count)
        results: queue.Queue[int] = queue.Queue()

        def _rollback() -> None:
            barrier.wait()
            results.put(reg.rollback_production("rollback-model"))

        threads = [threading.Thread(target=_rollback) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive(), "thread did not complete within the join timeout"

        # The critical invariant: AT MOST one Production version exists. (Since T071 a
        # rollback with no target raises instead of vacating Production, so "exactly
        # one" holds too; TestConcurrentPromoteAndRollback below asserts that.)
        production_versions = [
            v
            for v, data in reg._mock_registry["rollback-model"].items()
            if data["stage"] == "Production"
        ]
        assert len(production_versions) <= 1, (
            f"invariant violated: expected at most 1 Production version, "
            f"got {len(production_versions)}: {production_versions}. "
            f"This indicates concurrent rollbacks both promoted a version without "
            f"archiving the other, which is the race condition T062 fixes."
        )

        # Collect results - both threads returned (a rollback with no target would
        # have raised in its thread and put nothing, failing get_nowait below).
        returned_versions = [results.get_nowait() for _ in range(thread_count)]

        # With locking, rollbacks serialize: the promotion history v1 <- v2 <- v3 is
        # walked back one step per rollback (T071), so the two calls return v2 and v1
        # in some order. A duplicate would indicate a lost-update race.
        assert sorted(returned_versions) == [1, 2], (
            f"serialized rollbacks from v3 should restore v2 then v1: {returned_versions}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RB-010 Part 5: per-module config wiring. `tracking_uri` resolves with
# precedence: explicit constructor argument > `config.mlflow_tracking_uri` >
# the pre-existing hardcoded `"http://localhost:5000"` default -- so a caller
# that passes neither sees identical behaviour to before this fix. Purely
# additive on top of Part 10's locking/target_stage validation above; neither
# is touched here. See docs/decision-log.md RB-010.
# ═══════════════════════════════════════════════════════════════════════════════


class TestMlflowTrackingUriConfigWiring:
    def test_default_tracking_uri_unchanged_without_config(self) -> None:
        """Positive control: the exact pre-existing zero-arg construction."""
        reg = ModelRegistryOps()
        assert reg.tracking_uri == "http://localhost:5000"

    def test_config_supplies_tracking_uri_when_omitted(self) -> None:
        cfg = _build_config(mlflow_tracking_uri="http://mlflow.internal:5001")
        reg = ModelRegistryOps(config=cfg)
        assert reg.tracking_uri == "http://mlflow.internal:5001"

    def test_explicit_tracking_uri_overrides_config(self) -> None:
        cfg = _build_config(mlflow_tracking_uri="http://mlflow.internal:5001")
        reg = ModelRegistryOps(tracking_uri="http://explicit-override:9999", config=cfg)
        assert reg.tracking_uri == "http://explicit-override:9999"

    def test_config_wiring_does_not_disturb_lock_or_registry_state(self) -> None:
        """Guards the 'purely additive' claim: a config-constructed instance
        still has a working lock and an empty registry, exactly like the
        zero-arg constructor -- Part 10's `self._lock` is untouched."""
        cfg = _build_config(mlflow_tracking_uri="http://mlflow.internal:5001")
        reg = ModelRegistryOps(config=cfg)
        assert isinstance(reg._lock, type(threading.Lock()))
        assert reg._mock_registry == {}
        v1 = reg.register_model_version("unet-s2", "run-101")
        assert v1 == 1


# ═══════════════════════════════════════════════════════════════════════════════
# T071 (D-016/03c, D-016/08): rollback restores exactly the previous Production
# version, from an ordered promotion history. Behaviours shared with ports/
# registry.py's InMemoryModelRegistry live in test_registry_rollback_conformance.py;
# this section covers what only ModelRegistryOps exposes: the history accessor,
# NoRollbackTargetError's fields, decision logging (RB-021(a)), the metadata copy for
# code-hygiene review finding N-12 (RB-021), and concurrent promote/rollback under
# T062's lock.
# ═══════════════════════════════════════════════════════════════════════════════

_MODEL: Final = "unet-s2"
_LOGGER_NAME: Final = "orbital_drift.registry.ops"
_CORRELATION_ID: Final = "ct-run-42"
_DEPLOYMENT_VERSION_FIELD: Final = "version"
_DEPLOYMENT_VERSION: Final = "app-1.2.3"
_LOG_PIPELINE_FAULT: Final = "log pipeline fault"


def _registry_with_versions(count: int) -> tuple[ModelRegistryOps, list[int]]:
    reg = ModelRegistryOps()
    versions = [reg.register_model_version(_MODEL, f"run-{index}") for index in range(count)]
    return reg, versions


def _decision_records(
    caplog: pytest.LogCaptureFixture, registry_action: str
) -> list[logging.LogRecord]:
    """Records of one registry decision type, selected by their structured field."""
    return [
        record
        for record in caplog.records
        if record.name == _LOGGER_NAME
        and getattr(record, "registry_action", None) == registry_action
    ]


def _decision_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        field: getattr(record, field)
        for field in ("model_name", "model_version", "from_version", "to_version", "reason")
    }


class TestPromotionHistory:
    def test_history_is_ordered_and_records_each_replaced_production_version(self) -> None:
        reg, (v1, v2, rejected, v4) = _registry_with_versions(4)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, rejected, ARCHIVED_STAGE)
        reg.transition_stage(_MODEL, v4, PRODUCTION_STAGE)

        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
            PromotionRecord(version=v2, previous_production_version=v1),
            PromotionRecord(version=v4, previous_production_version=v2),
        )

    def test_rollback_discards_the_latest_record(self) -> None:
        reg, (v1, v2, v3) = _registry_with_versions(3)
        for version in (v1, v2, v3):
            reg.transition_stage(_MODEL, version, PRODUCTION_STAGE)

        assert reg.rollback_production(_MODEL) == v2
        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
            PromotionRecord(version=v2, previous_production_version=v1),
        )

    def test_unknown_model_has_an_empty_history(self) -> None:
        assert ModelRegistryOps().promotion_history("never-registered") == ()

    def test_each_model_has_its_own_history(self) -> None:
        """Both models number versions from 1; interleaving their promotions must not
        mix their records (a single shared history would return the same five records
        for both)."""
        other_model = f"{_MODEL}-coastal"
        reg, (a1, a2) = _registry_with_versions(2)
        b1, b2 = (reg.register_model_version(other_model, f"run-{i}") for i in range(2))
        reg.transition_stage(_MODEL, a1, PRODUCTION_STAGE)
        reg.transition_stage(other_model, b2, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, a2, PRODUCTION_STAGE)
        reg.transition_stage(other_model, b1, PRODUCTION_STAGE)

        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=a1, previous_production_version=None),
            PromotionRecord(version=a2, previous_production_version=a1),
        )
        assert reg.promotion_history(other_model) == (
            PromotionRecord(version=b2, previous_production_version=None),
            PromotionRecord(version=b1, previous_production_version=b2),
        )

    def test_history_is_an_immutable_snapshot(self) -> None:
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        snapshot = reg.promotion_history(_MODEL)

        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)

        assert snapshot == (PromotionRecord(version=v1, previous_production_version=None),)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot[0].version = v2  # type: ignore[misc]

    def test_transitions_outside_production_record_nothing(self) -> None:
        reg, (v1, v2, v3) = _registry_with_versions(3)
        reg.transition_stage(_MODEL, v1, STAGING_STAGE)
        reg.transition_stage(_MODEL, v2, ARCHIVED_STAGE)
        reg.transition_stage(_MODEL, v3, NONE_STAGE)

        assert reg.promotion_history(_MODEL) == ()

    def test_repromoting_the_production_version_records_nothing(self) -> None:
        reg, (v1,) = _registry_with_versions(1)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)

        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
        )
        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == v1

    def test_rejected_promotions_record_nothing(self) -> None:
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        before = reg.promotion_history(_MODEL)

        with pytest.raises(ValueError, match="already in Production"):
            reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE, archive_existing=False)
        with pytest.raises(ValueError, match="Invalid target_stage"):
            reg.transition_stage(_MODEL, v2, "production")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="not found in registry"):
            reg.transition_stage(_MODEL, 99, PRODUCTION_STAGE)

        assert reg.promotion_history(_MODEL) == before


class TestRollbackTargets:
    def test_promotion_into_a_vacated_production_records_no_rollback_target(self) -> None:
        """A rollback restores only the version that held Production at the instant
        the current one was promoted. After Production was vacated by an explicit
        demotion, nothing held it, so nothing is restorable."""
        reg, (v1, v2, v3) = _registry_with_versions(3)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, ARCHIVED_STAGE)  # explicit demotion

        # The vacated version's record stays (promotion_history's docstring).
        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
            PromotionRecord(version=v2, previous_production_version=v1),
        )

        reg.transition_stage(_MODEL, v3, PRODUCTION_STAGE)
        history = reg.promotion_history(_MODEL)
        assert history == (
            PromotionRecord(version=v1, previous_production_version=None),
            PromotionRecord(version=v2, previous_production_version=v1),
            PromotionRecord(version=v3, previous_production_version=None),
        )
        with pytest.raises(NoRollbackTargetError):
            reg.rollback_production(_MODEL)
        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == v3
        # A refused rollback leaves the history untouched (NoRollbackTargetError's
        # docstring).
        assert reg.promotion_history(_MODEL) == history

    def test_previous_version_is_restored_from_whatever_stage_it_now_holds(self) -> None:
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v1, STAGING_STAGE)

        assert reg.rollback_production(_MODEL) == v1
        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == v1
        assert reg.get_stage_version(_MODEL, STAGING_STAGE) is None
        assert reg.get_stage_version(_MODEL, ARCHIVED_STAGE) == v2

    def test_return_value_matches_the_registry_state(self) -> None:
        reg, (v1, v2, v3) = _registry_with_versions(3)
        for version in (v1, v2, v3):
            reg.transition_stage(_MODEL, version, PRODUCTION_STAGE)

        for expected in (v2, v1):
            assert reg.rollback_production(_MODEL) == expected
            assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == expected


class TestNoRollbackTargetError:
    @pytest.mark.parametrize(
        ("promote_first", "register_first", "expected_production", "expected_reason"),
        [
            (False, False, None, "model_not_registered"),
            (False, True, None, "no_production_version"),
            (True, True, 1, "no_recorded_previous_production"),
        ],
        ids=["unregistered-model", "nothing-in-production", "single-promotion"],
    )
    def test_fields_name_the_model_its_production_version_and_the_reason(
        self,
        promote_first: bool,
        register_first: bool,
        expected_production: int | None,
        expected_reason: str,
    ) -> None:
        reg = ModelRegistryOps()
        if register_first:
            version = reg.register_model_version(_MODEL, "run-1")
            if promote_first:
                reg.transition_stage(_MODEL, version, PRODUCTION_STAGE)
        history_before = reg.promotion_history(_MODEL)

        with pytest.raises(NoRollbackTargetError) as excinfo:
            reg.rollback_production(_MODEL)

        # The registry is unchanged, history included (the exception's docstring).
        assert reg.promotion_history(_MODEL) == history_before
        error = excinfo.value
        assert isinstance(error, LookupError)
        assert error.model_name == _MODEL
        assert error.production_version == expected_production
        assert error.reason == expected_reason
        assert _MODEL in str(error)
        assert expected_reason in str(error)
        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == expected_production

    def test_survives_a_pickle_round_trip_with_its_fields(self) -> None:
        """BaseException pickles as cls(*args); without __reduce__ this three-argument
        exception could not cross a process boundary."""
        original = NoRollbackTargetError(_MODEL, 3, "no_recorded_previous_production")

        restored = pickle.loads(pickle.dumps(original))  # noqa: S301 -- own object

        assert type(restored) is NoRollbackTargetError
        assert (restored.model_name, restored.production_version, restored.reason) == (
            _MODEL,
            3,
            "no_recorded_previous_production",
        )
        assert str(restored) == str(original)


def test_stage_constants_cover_stage_name_exactly() -> None:
    assert {NONE_STAGE, STAGING_STAGE, PRODUCTION_STAGE, ARCHIVED_STAGE} == set(get_args(StageName))


class TestRegistryDecisionLogging:
    """Each promotion, archive and rollback decision is one structured record on the
    module logger, carrying model name, from/to Production version and a reason in
    `extra=` fields (the eval/ pattern: observability's get_logger, which adds the
    active correlation context, plus `extra=`; RB-021(a)), so a soak-log query will need no
    message parsing once T054 wires configure_logging(); today the records carry the
    fields as LogRecord attributes, which is what these tests assert."""

    def test_rollback_logs_model_versions_and_reason_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A rollback is an incident action: WARNING keeps it visible under a root
        logger left at its WARNING default while T054's wiring is gated."""
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.rollback_production(_MODEL)

        (record,) = _decision_records(caplog, "rollback")
        assert record.levelno == logging.WARNING
        assert _decision_fields(record) == {
            "model_name": _MODEL,
            "model_version": v1,
            "from_version": v2,
            "to_version": v1,
            "reason": "restored_recorded_previous_production",
        }
        assert f"Rolled back model '{_MODEL}': promoted v{v1} to Production" in (
            record.getMessage()
        )

    def test_rejected_rollback_logs_a_warning_and_changes_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1,) = _registry_with_versions(1)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with (
            caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
            pytest.raises(NoRollbackTargetError),
        ):
            reg.rollback_production(_MODEL)

        (record,) = _decision_records(caplog, "rollback_rejected")
        assert record.levelno == logging.WARNING
        assert _decision_fields(record) == {
            "model_name": _MODEL,
            "model_version": None,
            "from_version": v1,
            "to_version": v1,
            "reason": "no_recorded_previous_production",
        }
        assert _decision_records(caplog, "rollback") == []

    def test_promotion_and_the_archive_it_causes_are_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1, v2) = _registry_with_versions(2)

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
            reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)

        assert [_decision_fields(record) for record in _decision_records(caplog, "promote")] == [
            {
                "model_name": _MODEL,
                "model_version": v1,
                "from_version": None,
                "to_version": v1,
                "reason": "promoted_with_no_previous_production",
            },
            {
                "model_name": _MODEL,
                "model_version": v2,
                "from_version": v1,
                "to_version": v2,
                "reason": "promoted_over_previous_production",
            },
        ]
        (archive,) = _decision_records(caplog, "archive")
        assert archive.levelno == logging.INFO
        assert _decision_fields(archive) == {
            "model_name": _MODEL,
            "model_version": v1,
            "from_version": v1,
            "to_version": v2,
            "reason": "superseded_by_promotion",
        }

    def test_archiving_a_rejected_challenger_leaves_production_in_the_record(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1, rejected) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.transition_stage(_MODEL, rejected, ARCHIVED_STAGE)

        (record,) = _decision_records(caplog, "archive")
        assert record.levelno == logging.INFO
        assert _decision_fields(record) == {
            "model_name": _MODEL,
            "model_version": rejected,
            "from_version": v1,
            "to_version": v1,
            "reason": "transition_outside_production",
        }

    def test_vacating_production_is_logged_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1,) = _registry_with_versions(1)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.transition_stage(_MODEL, v1, ARCHIVED_STAGE)

        (record,) = _decision_records(caplog, "archive")
        assert record.levelno == logging.WARNING
        assert _decision_fields(record) == {
            "model_name": _MODEL,
            "model_version": v1,
            "from_version": v1,
            "to_version": None,
            "reason": "production_vacated",
        }

    def test_repromotion_is_logged_as_a_no_op_promotion(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1,) = _registry_with_versions(1)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)

        (noop,) = _decision_records(caplog, "promote")
        assert _decision_fields(noop) == {
            "model_name": _MODEL,
            "model_version": v1,
            "from_version": v1,
            "to_version": v1,
            "reason": "already_in_production",
        }

    def test_none_and_staging_transitions_keep_the_plain_pre_t071_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """RB-021(a) covers promotion, archive and rollback decisions only, so a move to
        None or Staging logs exactly what it logged at c971603, with no decision fields,
        even when it moves the Production version out (Production is then empty)."""
        reg, (v1, v2, v3) = _registry_with_versions(3)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.transition_stage(_MODEL, v2, STAGING_STAGE)
            reg.transition_stage(_MODEL, v3, NONE_STAGE)
            reg.transition_stage(_MODEL, v1, STAGING_STAGE)

        records = [record for record in caplog.records if record.name == _LOGGER_NAME]
        assert [(record.levelno, record.getMessage()) for record in records] == [
            (logging.INFO, f"Transitioned model '{_MODEL}' v{v2} -> Staging"),
            (logging.INFO, f"Transitioned model '{_MODEL}' v{v3} -> None"),
            (logging.INFO, f"Transitioned model '{_MODEL}' v{v1} -> Staging"),
        ]
        assert not [record for record in records if hasattr(record, "registry_action")]
        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) is None

    def test_decision_records_carry_the_active_correlation_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The module logger is observability's get_logger, as in eval/, so a CT run's
        correlation_id joins its promote, archive and rollback records to its other
        evidence."""
        reg, (v1, v2) = _registry_with_versions(2)
        caplog.clear()  # registrations are not under test

        with (
            caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
            correlation_scope(_CORRELATION_ID),
        ):
            reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
            reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
            reg.rollback_production(_MODEL)
            with pytest.raises(NoRollbackTargetError):
                reg.rollback_production(_MODEL)

        decisions = [
            record
            for record in caplog.records
            if record.name == _LOGGER_NAME and hasattr(record, "registry_action")
        ]
        assert sorted({str(record.__dict__["registry_action"]) for record in decisions}) == [
            "archive",
            "promote",
            "rollback",
            "rollback_rejected",
        ]
        assert [getattr(record, CORRELATION_ID_FIELD, None) for record in decisions] == [
            _CORRELATION_ID
        ] * len(decisions)

    def test_decision_fields_do_not_shadow_a_deployment_wide_version_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """JsonFormatter lets record extras override its deployment-wide extra_fields;
        a decision field named "version" would replace the deployment's own version."""
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)
        caplog.clear()  # setup may already have been captured: the level is order-dependent

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            reg.rollback_production(_MODEL)

        (record,) = _decision_records(caplog, "rollback")
        payload = json.loads(
            JsonFormatter({_DEPLOYMENT_VERSION_FIELD: _DEPLOYMENT_VERSION}).format(record)
        )
        assert payload[_DEPLOYMENT_VERSION_FIELD] == _DEPLOYMENT_VERSION
        assert payload["model_version"] == v1


class _RaisingFilter(logging.Filter):
    """Test double: a logging Filter that raises on the decision record with one reason."""

    def __init__(self, reason: str) -> None:
        super().__init__()
        self._reason = reason

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "reason", None) == self._reason:
            raise RuntimeError(_LOG_PIPELINE_FAULT)
        return True


@contextlib.contextmanager
def _log_fault_on(reason: str) -> Iterator[None]:
    module_logger = logging.getLogger(_LOGGER_NAME)
    fault = _RaisingFilter(reason)
    module_logger.addFilter(fault)
    try:
        yield
    finally:
        module_logger.removeFilter(fault)


class TestStateIsWrittenBeforeDecisionsAreLogged:
    """Every state write of a promotion or rollback completes before its first decision
    record is emitted, so a fault raised by the logging pipeline (here a raising Filter)
    reaches the caller with the operation fully applied, never half-applied."""

    def test_a_promotion_is_fully_applied_when_logging_its_archive_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)

        with (
            caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
            _log_fault_on("superseded_by_promotion"),
            pytest.raises(RuntimeError, match=_LOG_PIPELINE_FAULT),
        ):
            reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)

        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == v2
        assert reg.get_stage_version(_MODEL, ARCHIVED_STAGE) == v1
        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
            PromotionRecord(version=v2, previous_production_version=v1),
        )

    def test_a_rollback_is_fully_applied_when_logging_it_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg, (v1, v2) = _registry_with_versions(2)
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        reg.transition_stage(_MODEL, v2, PRODUCTION_STAGE)

        with (
            caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
            _log_fault_on("restored_recorded_previous_production"),
            pytest.raises(RuntimeError, match=_LOG_PIPELINE_FAULT),
        ):
            reg.rollback_production(_MODEL)

        assert reg.get_stage_version(_MODEL, PRODUCTION_STAGE) == v1
        assert reg.get_stage_version(_MODEL, ARCHIVED_STAGE) == v2
        assert reg.promotion_history(_MODEL) == (
            PromotionRecord(version=v1, previous_production_version=None),
        )


class TestRegisterModelVersionMetadataIsolation:
    """Code-hygiene review finding N-12 (RB-021): register_model_version stored the
    caller's metadata dict by reference, so a caller mutating its own dict after
    registration rewrote the registry's record."""

    def test_metadata_that_cannot_be_deep_copied_raises_and_registers_nothing(self) -> None:
        reg = ModelRegistryOps()

        with pytest.raises(TypeError):
            reg.register_model_version(_MODEL, "run-1", metadata={"lock": threading.Lock()})

        assert _MODEL not in reg._mock_registry
        assert reg.register_model_version(_MODEL, "run-2") == 1

    def test_mutating_the_callers_metadata_after_registration_changes_nothing(self) -> None:
        metadata: dict[str, Any] = {"dataset": "ds-1", "metrics": {"iou": 0.5}}
        reg = ModelRegistryOps()
        version = reg.register_model_version(_MODEL, "run-1", metadata=metadata)

        metadata["dataset"] = "tampered"
        metadata["metrics"]["iou"] = 0.0
        metadata["added_later"] = True

        assert reg._mock_registry[_MODEL][version]["metadata"] == {
            "dataset": "ds-1",
            "metrics": {"iou": 0.5},
        }

    def test_omitted_metadata_is_a_fresh_empty_dict_per_version(self) -> None:
        reg, (v1, v2) = _registry_with_versions(2)

        first = reg._mock_registry[_MODEL][v1]["metadata"]
        second = reg._mock_registry[_MODEL][v2]["metadata"]
        assert first == second == {}
        assert first is not second


class TestConcurrentPromoteAndRollback:
    """Promotion-history updates happen under the same lock as the stage transitions
    (T062), so interleaved promotions and rollbacks never leave two Production
    versions, or none, once any of them has returned successfully."""

    def test_every_successful_operation_leaves_exactly_one_production_version(self) -> None:
        reg, versions = _registry_with_versions(5)
        v1, v2, v3, v4, v5 = versions
        reg.transition_stage(_MODEL, v1, PRODUCTION_STAGE)
        # Widen every scan inside the lock so the threads pile up on it rather than
        # finishing one at a time by scheduler luck.
        reg._mock_registry[_MODEL] = _SlowItemsDict(reg._mock_registry[_MODEL])

        def _production_versions() -> list[int]:
            # dict.items bypasses the slow double: observing must not widen the race.
            with reg._lock:
                return [
                    version
                    for version, data in dict.items(reg._mock_registry[_MODEL])
                    if data["stage"] == PRODUCTION_STAGE
                ]

        def _promote(version: int) -> Callable[[], object]:
            return lambda: reg.transition_stage(_MODEL, version, PRODUCTION_STAGE)

        def _rollback() -> object:
            return reg.rollback_production(_MODEL)

        schedules: list[list[Callable[[], object]]] = [
            [_promote(v2), _promote(v3)],
            [_rollback, _rollback],
            [_promote(v4), _rollback],
            [_rollback, _promote(v5)],
        ]
        promotions_scheduled = 4  # v2..v5; none can raise NoRollbackTargetError
        barrier = threading.Barrier(len(schedules))
        violations: queue.Queue[str] = queue.Queue()
        errors: queue.Queue[BaseException] = queue.Queue()
        successes: queue.Queue[int] = queue.Queue()

        def _run(schedule: list[Callable[[], object]]) -> None:
            barrier.wait()
            for operation in schedule:
                try:
                    operation()
                except NoRollbackTargetError:
                    continue  # a refused rollback is allowed; it is not a success
                except BaseException as exc:  # surfaced via the queue, not swallowed
                    errors.put(exc)
                    return
                successes.put(1)
                observed = _production_versions()
                if len(observed) != 1:
                    violations.put(f"Production holds {observed} after a successful operation")

        threads = [threading.Thread(target=_run, args=(schedule,)) for schedule in schedules]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "thread did not complete within the join timeout"

        assert list(errors.queue) == []
        assert list(violations.queue) == []
        # Which rollbacks find a target depends on lock order; the promotions do not.
        assert successes.qsize() >= promotions_scheduled

        production = _production_versions()
        assert len(production) == 1
        # The history stayed consistent with the stages: its newest record is the
        # Production version, and (Production was never vacated) each record names
        # the version of the record beneath it as the one it replaced.
        history = reg.promotion_history(_MODEL)
        assert history[0] == PromotionRecord(version=v1, previous_production_version=None)
        assert history[-1].version == production[0]
        for below, above in itertools.pairwise(history):
            assert above.previous_production_version == below.version
