"""Drift Trigger Emitter with Hysteresis and Cooldown.

Prevents trigger storms by requiring N consecutive drifted scenes (hysteresis),
enforcing a cooldown window, and coalescing concurrent retrain requests (queue-depth-1).
"""

from __future__ import annotations

import logging
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
    ) -> None:
        self.hysteresis_window = hysteresis_window
        self.cooldown_scenes = cooldown_scenes
        self.consecutive_drifted_count: int = 0
        self.scenes_since_last_trigger: int = cooldown_scenes  # Start ready
        self._is_retraining_active: bool = False
        self.total_triggers_emitted: int = 0

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
        self.scenes_since_last_trigger += 1

        if is_drifted:
            self.consecutive_drifted_count += 1
        else:
            self.consecutive_drifted_count = 0

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
                f"Cooldown active ({self.scenes_since_last_trigger}/{self.cooldown_scenes} scenes)"
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

    def mark_retraining_completed(self) -> None:
        """Signals that retraining workflow has finished."""
        self.is_retraining_active = False
        self.consecutive_drifted_count = 0
        logger.info("Retraining marked completed; trigger state reset")
