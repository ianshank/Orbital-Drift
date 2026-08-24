"""Provide correlation-aware logs and durable gate-decision records.

The package keeps operational evidence in one deliberately small, stdlib-only
surface so gate outcomes can be inspected by both people and automation.
"""

from __future__ import annotations

from orbital_drift.observability.context import (
    bind_context,
    correlation_scope,
    current_context,
    new_correlation_id,
)
from orbital_drift.observability.logging import (
    JsonFormatter,
    PlainFormatter,
    configure_logging,
    get_logger,
)
from orbital_drift.observability.records import (
    RECORD_SCHEMA_VERSION,
    DecisionRecord,
    GateState,
    UnsupportedRecordSchema,
    emit_record,
)

__all__ = [
    "RECORD_SCHEMA_VERSION",
    "DecisionRecord",
    "GateState",
    "JsonFormatter",
    "PlainFormatter",
    "UnsupportedRecordSchema",
    "bind_context",
    "configure_logging",
    "correlation_scope",
    "current_context",
    "emit_record",
    "get_logger",
    "new_correlation_id",
]
