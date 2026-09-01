"""Drift Trigger Emitter with Hysteresis and Cooldown.

Prevents trigger storms by requiring N consecutive drifted scenes (hysteresis),
enforcing a cooldown window, and coalescing concurrent retrain requests (queue-depth-1).

Thread-safety: ``DriftTriggerManager`` mutates several instance attributes across
multiple statements per call (read, decide, then write), so ``process_scene_verdict``
and every method that mutates the retraining-active flag serialise on a per-instance
``threading.Lock`` (``self._lock``). This mirrors the ``_MORAN_LOCK`` pattern in
``orbital_drift.eval.spatial`` -- an explicit ``Lock``, a comment explaining the
hazard, and a dedicated regression test -- but is scoped per-instance rather than
module-level, because the guarded state here (``consecutive_drifted_count``,
``scenes_since_last_trigger``, the active flag) is this instance's own mutable
state, not a shared process-global resource the way Moran's monkeypatched RNG is.

Failure-path contract: a dispatched retrain that fails MUST be reported via
``mark_retraining_failed()`` from the caller's own ``except`` block --
``DriftTriggerManager`` has no way to observe a caller's retrain job failing on
its own. Skipping that call leaves ``is_retraining_active`` stuck ``True`` and
silently coalesces every future drift signal for the rest of the process's life.
Passing ``max_retraining_scenes`` to the constructor adds an opt-in, scene-counted
(not wall-clock) staleness safety net so ``process_scene_verdict`` can self-heal
the flag even if a caller never calls ``mark_retraining_completed()`` /
``mark_retraining_failed()`` at all -- defense in depth for exactly that gap.
"""

from __future__ import annotations

import logging
import threading
from typing import NamedTuple

logger = logging.getLogger(__name__)


class TriggerDecision(NamedTuple):
    """Result of drift trigger evaluation."""

    should_trigger: bool
    reason: str
    consecutive_drifted_count: int
    scenes_since_last_trigger: int


class DriftTriggerManager:
    """Manages drift state machine, hysteresis window, and retrain trigger emission."""

    def __init__(
        self,
        hysteresis_window: int = 3,
        cooldown_scenes: int = 5,
        max_retraining_scenes: int | None = None,
    ) -> None:
        """Configures the hysteresis/cooldown state machine.

        Args:
            hysteresis_window: consecutive drifted scenes required before a
                trigger fires.
            cooldown_scenes: scenes required between trigger episodes.
            max_retraining_scenes: opt-in staleness safety net. If set, and
                ``is_retraining_active`` is still True after this many scenes
                have been processed since the trigger fired,
                ``process_scene_verdict`` treats the flag as stale, logs a
                warning, and clears it automatically -- defense in depth for
                callers that never invoke ``mark_retraining_completed()`` /
                ``mark_retraining_failed()`` at all. Counted in scenes actually
                processed, not wall-clock time, so a prolonged data-starvation
                gap (scenes not arriving at all, e.g. an extended cloudy
                period) cannot spuriously trip it -- see the spec's
                starvation-vs-drift edge case. Defaults to ``None`` (disabled):
                this manager does not invent a production duration default;
                callers that want the safety net should size it to their own
                retrain cadence.
        """
        self.hysteresis_window = hysteresis_window
        self.cooldown_scenes = cooldown_scenes
        self.max_retraining_scenes = max_retraining_scenes
        self.consecutive_drifted_count: int = 0
        self.scenes_since_last_trigger: int = cooldown_scenes  # Start ready
        self._is_retraining_active: bool = False
        self.total_triggers_emitted: int = 0
        # Guards every read-then-write of the attributes above.
        # process_scene_verdict is a multi-statement check-then-act (observe
        # is_retraining_active, decide, then write): without this lock, two
        # near-simultaneous calls can both observe False before either writes
        # True, producing a double-trigger that violates this module's own
        # queue-depth-1 coalescing claim. mark_retraining_completed() and
        # mark_retraining_failed() take the same lock so a completion/failure
        # signal can never interleave with an in-flight process_scene_verdict
        # call on another thread.
        self._lock: threading.Lock = threading.Lock()

    @property
    def is_retraining_active(self) -> bool:
        """Indicates whether a retraining workflow is currently in progress."""
        return self._is_retraining_active

    @is_retraining_active.setter
    def is_retraining_active(self, active: bool) -> None:
        self._is_retraining_active = active

    def process_scene_verdict(
        self,
        is_drifted: bool,
        scene_id: str,
    ) -> TriggerDecision:
        """Processes a new scene's drift verdict and determines retrain trigger state."""
        with self._lock:
            self.scenes_since_last_trigger += 1

            if is_drifted:
                self.consecutive_drifted_count += 1
            else:
                self.consecutive_drifted_count = 0

            if self.is_retraining_active and self._is_stale_locked():
                logger.warning(
                    "Retraining flagged STALE while processing scene '%s': "
                    "is_retraining_active has been True for %d scenes "
                    "(max_retraining_scenes=%d) with no mark_retraining_completed()/"
                    "mark_retraining_failed() call observed; auto-clearing so drift "
                    "signals are not silently swallowed indefinitely",
                    scene_id,
                    self.scenes_since_last_trigger,
                    self.max_retraining_scenes,
                )
                self.is_retraining_active = False

            # Check conditions
            if self.is_retraining_active:
                return TriggerDecision(
                    should_trigger=False,
                    reason="Retraining job already in progress (queue-depth-1 coalescing)",
                    consecutive_drifted_count=self.consecutive_drifted_count,
                    scenes_since_last_trigger=self.scenes_since_last_trigger,
                )

            if self.consecutive_drifted_count < self.hysteresis_window:
                reason = (
                    f"Hysteresis threshold not met "
                    f"({self.consecutive_drifted_count}/{self.hysteresis_window})"
                )
                return TriggerDecision(
                    should_trigger=False,
                    reason=reason,
                    consecutive_drifted_count=self.consecutive_drifted_count,
                    scenes_since_last_trigger=self.scenes_since_last_trigger,
                )

            # 3. Check cooldown
            if self.scenes_since_last_trigger < self.cooldown_scenes:
                reason = (
                    f"Cooldown active ({self.scenes_since_last_trigger}/"
                    f"{self.cooldown_scenes} scenes)"
                )
                return TriggerDecision(
                    should_trigger=False,
                    reason=reason,
                    consecutive_drifted_count=self.consecutive_drifted_count,
                    scenes_since_last_trigger=self.scenes_since_last_trigger,
                )

            # Trigger fired
            self.is_retraining_active = True
            self.scenes_since_last_trigger = 0
            self.total_triggers_emitted += 1
            logger.info(
                "Emitted retrain trigger for scene '%s' (consecutive drifted: %d)",
                scene_id,
                self.consecutive_drifted_count,
            )
            return TriggerDecision(
                should_trigger=True,
                reason=f"Drift confirmed over {self.consecutive_drifted_count} consecutive scenes",
                consecutive_drifted_count=self.consecutive_drifted_count,
                scenes_since_last_trigger=self.scenes_since_last_trigger,
            )

    def _is_stale_locked(self) -> bool:
        """True when the active flag has outlived ``max_retraining_scenes``.

        Caller must already hold ``self._lock``. Compares against scenes
        actually processed since the trigger fired (not wall-clock time), so a
        data-starvation gap -- scenes not arriving at all -- cannot spuriously
        trip this.
        """
        return (
            self.max_retraining_scenes is not None
            and self.scenes_since_last_trigger >= self.max_retraining_scenes
        )

    def mark_retraining_completed(self) -> None:
        """Signals that a dispatched retraining workflow finished successfully."""
        with self._lock:
            self.is_retraining_active = False
            self.consecutive_drifted_count = 0
        logger.info("Retraining marked completed; trigger state reset")

    def mark_retraining_failed(self, reason: str = "") -> None:
        """Signals that a dispatched retraining workflow failed or was aborted.

        Callers MUST invoke this from their retrain-dispatch ``except`` block
        (or equivalent failure/timeout path) -- this manager has no way to
        observe a caller's retrain job failing on its own. Skipping this call
        leaves ``is_retraining_active`` stuck ``True``, silently coalescing
        every future drift signal, until either the process restarts or (if
        ``max_retraining_scenes`` was configured) ``process_scene_verdict``
        self-heals the flag after enough scenes have elapsed.

        Args:
            reason: optional free-text cause, included in the emitted log line
                for operator triage. Never included in the returned state.
        """
        with self._lock:
            self.is_retraining_active = False
            self.consecutive_drifted_count = 0
        logger.error(
            "Retraining marked FAILED; trigger state reset so future drift "
            "signals are not silently swallowed%s",
            f" (reason: {reason})" if reason else "",
        )
