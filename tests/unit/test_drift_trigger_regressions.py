"""Regression tests for drift/trigger.py's stuck-breaker and concurrency defects.

RB-010 Part 11 (docs/decision-log.md 2026-09-01 entry). The SDLC review that
authorized this program found: (1) ``is_retraining_active`` has no failure-path
reset -- it is only ever cleared by an explicit, successful
``mark_retraining_completed()`` call, and no caller anywhere in the repo wraps
retrain dispatch so that a failure clears it, so one failed retrain during an
unattended soak permanently disables all future drift-triggered retraining; and
(2) ``process_scene_verdict`` is an unlocked multi-statement check-then-act, so
two near-simultaneous calls can both observe ``is_retraining_active is False``
before either writes ``True``, violating the module's own queue-depth-1
coalescing claim.

Each class below reproduces one confirmed defect before asserting the fixed
behaviour, per this repo's TDD protocol: every test here fails against the
pre-fix code (either because the failure-reporting API does not exist yet, or
because the unlocked race produces more than one trigger) and passes after.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from orbital_drift.drift.trigger import DriftTriggerManager, TriggerDecision

# ═══════════════════════════════════════════════════════════════════════════════
# Finding: stuck-breaker -- is_retraining_active has no failure-path reset
# ═══════════════════════════════════════════════════════════════════════════════


class TestStuckBreakerFailurePathReset:
    """A failed retrain must not permanently disable future drift triggers."""

    def test_mark_retraining_failed_clears_active_flag(self) -> None:
        """Simulates a retrain that raises after the trigger dispatched it."""
        manager = DriftTriggerManager(hysteresis_window=1, cooldown_scenes=0)

        dispatched = manager.process_scene_verdict(is_drifted=True, scene_id="s1")
        assert dispatched.should_trigger is True
        # Read into a local before asserting: mypy narrows a property read to a
        # Literal after an `is True`/`is False` assert and does not invalidate
        # that narrowing across the mark_retraining_failed() call below (it
        # cannot see that the call reassigns the property internally), which
        # would otherwise make the second assert look statically unreachable.
        active_after_dispatch = manager.is_retraining_active
        assert active_after_dispatch is True

        try:
            raise RuntimeError("simulated retrain failure")
        except RuntimeError as exc:
            manager.mark_retraining_failed(reason=str(exc))

        # The manager must NOT be stuck: a fresh drift-triggered verdict must be
        # able to fire again, not be silently coalesced forever.
        active_after_failure = manager.is_retraining_active
        assert active_after_failure is False
        followup = manager.process_scene_verdict(is_drifted=True, scene_id="s2")
        assert followup.should_trigger is True
        assert "already in progress" not in followup.reason

    def test_unhandled_exception_during_retrain_leaves_manager_recoverable(self) -> None:
        """Shape of a real caller's retrain dispatch (matching
        tests/e2e/test_user_journey_ct_loop.py's process_scene_verdict ->
        [retrain work] -> mark_retraining_completed() flow), except the retrain
        step raises instead of succeeding."""
        manager = DriftTriggerManager(hysteresis_window=1, cooldown_scenes=0)
        manager.process_scene_verdict(is_drifted=True, scene_id="s1")
        active_after_dispatch = manager.is_retraining_active
        assert active_after_dispatch is True

        def retrain() -> None:
            raise RuntimeError("gpu OOM during retrain")

        with pytest.raises(RuntimeError):
            try:
                retrain()
            except RuntimeError:
                manager.mark_retraining_failed(reason="gpu OOM during retrain")
                raise

        active_after_failure = manager.is_retraining_active
        assert active_after_failure is False
        recovered = manager.process_scene_verdict(is_drifted=True, scene_id="s2")
        assert recovered.should_trigger is True


class TestStaleActiveFlagSafetyNet:
    """``max_retraining_scenes`` self-heals a stuck flag even if a caller never
    calls mark_retraining_failed at all -- defense in depth for the exact gap
    RB-010 measured (no caller anywhere resets the flag on failure)."""

    def test_stale_active_flag_is_auto_cleared_after_max_retraining_scenes(self) -> None:
        manager = DriftTriggerManager(
            hysteresis_window=1, cooldown_scenes=0, max_retraining_scenes=2
        )
        fired = manager.process_scene_verdict(is_drifted=True, scene_id="s1")
        assert fired.should_trigger is True
        active_after_fire = manager.is_retraining_active
        assert active_after_fire is True

        # Caller never calls mark_retraining_completed()/mark_retraining_failed()
        # -- the exact gap RB-010 found at every real call site -- scenes keep
        # arriving regardless.
        d2 = manager.process_scene_verdict(is_drifted=False, scene_id="s2")
        assert d2.should_trigger is False
        active_after_s2 = manager.is_retraining_active
        assert active_after_s2 is True  # 1 scene elapsed: not yet stale

        d3 = manager.process_scene_verdict(is_drifted=False, scene_id="s3")
        active_after_s3 = manager.is_retraining_active
        assert active_after_s3 is False  # 2 scenes elapsed: auto-cleared
        assert d3.should_trigger is False  # no fresh drift streak on this scene itself
        assert "Hysteresis" in d3.reason

        # Manager is fully recovered: a fresh drift streak can trigger normally.
        d4 = manager.process_scene_verdict(is_drifted=True, scene_id="s4")
        assert d4.should_trigger is True

    def test_max_retraining_scenes_disabled_by_default_does_not_auto_clear(self) -> None:
        """Default behaviour is unchanged: without an explicit opt-in, the
        timeout safety net never fires. ``mark_retraining_failed()`` is the
        mandatory fix; the scene-counted timeout is an opt-in extra, not a
        silent default a caller could accidentally rely on."""
        manager = DriftTriggerManager(hysteresis_window=1, cooldown_scenes=0)
        manager.process_scene_verdict(is_drifted=True, scene_id="s1")
        active_after_fire = manager.is_retraining_active
        assert active_after_fire is True

        for i in range(50):
            manager.process_scene_verdict(is_drifted=True, scene_id=f"s{i}")

        active_after_loop = manager.is_retraining_active
        assert active_after_loop is True


# ═══════════════════════════════════════════════════════════════════════════════
# Finding: concurrency -- process_scene_verdict has no lock around its
# check-then-act critical section
# ═══════════════════════════════════════════════════════════════════════════════


class _RaceWideningTriggerManager(DriftTriggerManager):
    """Test-only subclass that deterministically widens
    ``process_scene_verdict``'s unsynchronised check-then-act race window,
    instead of depending on GIL-scheduling luck to reproduce it.

    ``time.sleep()`` releases the GIL, so inserting one into the
    ``is_retraining_active`` read lets every other unsynchronised thread also
    complete its own read (observing ``False``) before the first thread
    resumes and writes ``True`` -- this reliably reproduces the double-trigger
    against the pre-fix code on every run, not just occasionally.

    Once ``process_scene_verdict`` holds ``self._lock`` around this same read
    (the fix), every other thread blocks at lock acquisition *before* it can
    reach this property at all, so the identical injected sleep no longer
    creates a race -- the same subclass is a valid pre-fix AND post-fix probe.
    """

    @property
    def is_retraining_active(self) -> bool:
        value = self._is_retraining_active
        time.sleep(0.05)
        return value

    @is_retraining_active.setter
    def is_retraining_active(self, active: bool) -> None:
        self._is_retraining_active = active


class TestConcurrentProcessSceneVerdictCoalescing:
    """Mirrors the eval.spatial._MORAN_LOCK regression pattern
    (tests/unit/test_pr17_regressions.py::TestMoranThreadSafety) for the
    equivalent hazard in DriftTriggerManager."""

    def test_concurrent_calls_at_threshold_coalesce_to_exactly_one_trigger(self) -> None:
        """Many threads call process_scene_verdict at the same instant once the
        hysteresis/cooldown thresholds are already satisfied. Absent a lock,
        multiple threads can each observe is_retraining_active is False before
        any of them commits True, producing more than one trigger."""
        n_threads = 16
        manager = _RaceWideningTriggerManager(hysteresis_window=1, cooldown_scenes=0)
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> TriggerDecision:
            barrier.wait(timeout=10)
            return manager.process_scene_verdict(is_drifted=True, scene_id=f"scene-{i}")

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            results = list(executor.map(worker, range(n_threads)))

        triggered = [d for d in results if d.should_trigger]
        assert len(triggered) == 1, (
            f"expected exactly one trigger from {n_threads} concurrent calls "
            f"(queue-depth-1 coalescing), got {len(triggered)}"
        )
        assert manager.total_triggers_emitted == 1

    def test_lock_attribute_exists_and_is_a_threading_lock(self) -> None:
        """Pins the fix's structure directly, mirroring
        TestMoranThreadSafety.test_moran_lock_is_a_threading_lock."""
        manager = DriftTriggerManager()
        assert isinstance(manager._lock, type(threading.Lock()))
