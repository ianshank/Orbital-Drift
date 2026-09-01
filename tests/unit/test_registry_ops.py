"""Unit tests for ModelRegistryOps lifecycle transitions and rollback mechanics."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import ItemsView
from typing import Any, Final

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
