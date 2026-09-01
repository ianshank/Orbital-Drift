"""Model registry port and stdlib fake driven entirely by lineage envelopes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orbital_drift.domain.lineage import LineageEnvelope


@runtime_checkable
class ModelRegistryPort(Protocol):
    """Register and promote immutable model lineage without an ML registry SDK."""

    def register(self, envelope: LineageEnvelope) -> LineageEnvelope:
        """Register and return an immutable envelope."""

    def transition_stage(self, model_name: str, model_version: str, stage: str) -> LineageEnvelope:
        """Assign a registered version to a plain-string stage."""

    def get_by_stage(self, model_name: str, stage: str) -> LineageEnvelope:
        """Return the envelope currently assigned to a stage."""

    def rollback(self, model_name: str, stage: str) -> LineageEnvelope:
        """Restore the immediately preceding envelope for a stage."""


class InMemoryModelRegistry:
    """A deterministic registry fake that retains stage history for rollback tests."""

    def __init__(self) -> None:
        self._registered: dict[tuple[str, str], LineageEnvelope] = {}
        self._stages: dict[tuple[str, str], list[LineageEnvelope]] = {}

    def register(self, envelope: LineageEnvelope) -> LineageEnvelope:
        """Store a model version and return the passed envelope."""
        self._registered[(envelope.model_name, envelope.model_version)] = envelope
        return envelope

    def transition_stage(self, model_name: str, model_version: str, stage: str) -> LineageEnvelope:
        """Append a registered envelope to the stage's promotion history."""
        try:
            envelope = self._registered[(model_name, model_version)]
        except KeyError as error:
            raise KeyError(
                f"model version not registered: {model_name}/{model_version}"  # pin: not a path
            ) from error
        self._stages.setdefault((model_name, stage), []).append(envelope)
        return envelope

    def get_by_stage(self, model_name: str, stage: str) -> LineageEnvelope:
        """Return the latest promoted envelope for a stage."""
        try:
            return self._stages[(model_name, stage)][-1]
        except KeyError as error:
            raise KeyError(
                f"stage not assigned: {model_name}/{stage}"  # pin: separator, not a path
            ) from error

    def rollback(self, model_name: str, stage: str) -> LineageEnvelope:
        """Discard the latest promotion and return the preceding envelope."""
        key = (model_name, stage)
        history = self._stages.get(key)
        if history is None or len(history) < 2:
            raise KeyError(
                f"no rollback target for stage: {model_name}/{stage}"  # pin: separator, not a path
            )
        history.pop()
        return history[-1]
