"""Represent and emit durable four-state gate decisions.

Decision records make a gate result auditable after process exit, rather than
leaving a deployment decision implicit in an unstructured log sentence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

RECORD_SCHEMA_VERSION: Final[str] = "1.0"
EVENT_FIELD: Final[str] = "event"
EVENT_MESSAGE: Final[str] = "Decision record emitted"
_SCHEMA_SEPARATOR: Final[str] = "."


class GateState(StrEnum):
    """Name every terminal gate outcome so automation cannot collapse failures."""

    AUTHORIZED = "authorized"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNAUTHORIZED = "unauthorized"

    @property
    def description(self) -> str:
        """Explain why this outcome matters to an operator reviewing evidence."""
        descriptions: Final[dict[GateState, str]] = {
            GateState.AUTHORIZED: "Ran, produced evidence, and met all required thresholds.",
            GateState.FAILED: (
                "Ran to completion, but its evidence did not meet required thresholds."
            ),
            GateState.BLOCKED: "Could not execute because an external prerequisite was unmet.",
            GateState.UNAUTHORIZED: (
                "Detected a bypass attempt, unauthorized intervention, or defect-recurrence cap."
            ),
        }
        return descriptions[self]


class UnsupportedRecordSchema(ValueError):  # noqa: N818
    """Signal that a record requires a schema parser with newer compatible rules."""


def _schema_parts(version: str) -> tuple[int, int]:
    """Parse a two-part schema version before compatibility decisions are made."""
    major_text, separator, minor_text = version.partition(_SCHEMA_SEPARATOR)
    if not separator:
        raise ValueError(f"Invalid record schema version: {version}")
    major = int(major_text)
    minor = int(minor_text)
    if major < 0 or minor < 0:
        raise ValueError(f"Invalid record schema version: {version}")
    return major, minor


@dataclass(frozen=True)
class DecisionRecord:
    """Keep the evidence needed to reconstruct why one gate reached its state."""

    record_id: str
    kind: str
    subject: str
    state: GateState
    rationale: str
    evidence: tuple[str, ...]
    created_at: datetime
    metadata: Mapping[str, str]
    schema_version: str = RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Enforce tz-aware timestamps and freeze metadata after construction."""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_json_dict(self) -> dict[str, object]:
        """Produce sorted JSON-compatible fields so stored evidence has stable shape."""
        payload: dict[str, object] = {
            "created_at": self.created_at.isoformat(),
            "evidence": list(self.evidence),
            "kind": self.kind,
            "metadata": dict(sorted(self.metadata.items())),
            "rationale": self.rationale,
            "record_id": self.record_id,
            "schema_version": self.schema_version,
            "state": self.state.value,
            "subject": self.subject,
        }
        return dict(sorted(payload.items()))

    @classmethod
    def from_json_dict(cls, payload: Mapping[str, object]) -> DecisionRecord:
        """Load an older-compatible record without silently accepting newer semantics."""
        schema_version = _required_str(payload, "schema_version")
        payload_major, payload_minor = _schema_parts(schema_version)
        parser_major, parser_minor = _schema_parts(RECORD_SCHEMA_VERSION)
        if payload_major != parser_major or payload_minor > parser_minor:
            raise UnsupportedRecordSchema(
                f"Record schema {schema_version} is incompatible with parser "
                f"{RECORD_SCHEMA_VERSION}"
            )

        evidence_value = payload.get("evidence")
        metadata_value = payload.get("metadata")
        if not isinstance(evidence_value, list) or not all(
            isinstance(item, str) for item in evidence_value
        ):
            raise ValueError("Record evidence must be a list of strings.")
        if not isinstance(metadata_value, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata_value.items()
        ):
            raise ValueError("Record metadata must map strings to strings.")

        parsed_dt = datetime.fromisoformat(_required_str(payload, "created_at"))
        if parsed_dt.tzinfo is None or parsed_dt.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

        return cls(
            record_id=_required_str(payload, "record_id"),
            kind=_required_str(payload, "kind"),
            subject=_required_str(payload, "subject"),
            state=GateState(_required_str(payload, "state")),
            rationale=_required_str(payload, "rationale"),
            evidence=tuple(evidence_value),
            created_at=parsed_dt,
            metadata=dict(metadata_value),
            schema_version=schema_version,
        )


def _required_str(payload: Mapping[str, object], field_name: str) -> str:
    """Reject malformed payloads instead of admitting ambiguous audit evidence."""
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"Record field {field_name} must be a string.")
    return value


def emit_record(
    record: DecisionRecord,
    *,
    logger: logging.LoggerAdapter[logging.Logger] | logging.Logger | None = None,
) -> None:
    """Log a record under a stable event field for gate-ledger collection."""
    if logger is not None:
        target_logger = logger
    else:
        from orbital_drift.observability.logging import get_logger

        target_logger = get_logger("records")
    target_logger.info(EVENT_MESSAGE, extra={EVENT_FIELD: record.to_json_dict()})
