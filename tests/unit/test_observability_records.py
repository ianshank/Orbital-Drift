"""Behavioural tests for durable four-state decision records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO

import pytest

from orbital_drift.observability import records
from orbital_drift.observability.logging import configure_logging
from orbital_drift.observability.records import (
    RECORD_SCHEMA_VERSION,
    DecisionRecord,
    GateState,
    UnsupportedRecordSchema,
    emit_record,
)


def _record(schema_version: str = RECORD_SCHEMA_VERSION) -> DecisionRecord:
    """Build stable evidence so serialization assertions are not time-dependent."""
    return DecisionRecord(
        record_id="record-1",
        kind="deployment-gate",
        subject="model:v2",
        state=GateState.AUTHORIZED,
        rationale="all thresholds met",
        evidence=("metrics.json", "attestation.json"),
        created_at=datetime(2026, 8, 23, 16, 0, tzinfo=UTC),
        metadata={"region": "us-east-1", "run": "42"},
        schema_version=schema_version,
    )


def test_gate_states_are_exact_and_explain_distinct_operational_outcomes() -> None:
    """Automation needs four non-interchangeable terminal outcomes for safe handling."""
    assert list(GateState) == [
        GateState.AUTHORIZED,
        GateState.FAILED,
        GateState.BLOCKED,
        GateState.UNAUTHORIZED,
    ]
    assert "met all required thresholds" in GateState.AUTHORIZED.description
    assert "did not meet" in GateState.FAILED.description
    assert "external prerequisite" in GateState.BLOCKED.description
    assert "bypass attempt" in GateState.UNAUTHORIZED.description


def test_decision_record_json_round_trip_preserves_canonical_evidence() -> None:
    """A stored ledger record must reconstruct the same immutable gate decision."""
    record = _record()
    payload = record.to_json_dict()

    assert list(payload) == sorted(payload)
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    assert list(metadata) == ["region", "run"]
    assert payload["created_at"] == "2026-08-23T16:00:00+00:00"
    assert payload["evidence"] == ["metrics.json", "attestation.json"]
    assert payload["state"] == "authorized"
    assert json.loads(json.dumps(payload)) == payload
    assert DecisionRecord.from_json_dict(payload) == record


def test_schema_version_accepts_older_minor_and_equal_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compatible historical records remain readable after a parser minor upgrade."""
    equal_payload = _record().to_json_dict()
    assert DecisionRecord.from_json_dict(equal_payload).schema_version == RECORD_SCHEMA_VERSION

    monkeypatch.setattr(records, "RECORD_SCHEMA_VERSION", "1.1")
    older_payload = _record("1.0").to_json_dict()
    assert DecisionRecord.from_json_dict(older_payload).schema_version == "1.0"


def test_schema_version_rejects_higher_major_and_minor_versions() -> None:
    """Newer semantics must fail loudly rather than be silently misinterpreted."""
    higher_major_payload = _record("2.0").to_json_dict()
    with pytest.raises(UnsupportedRecordSchema, match="incompatible"):
        DecisionRecord.from_json_dict(higher_major_payload)

    higher_minor_payload = _record("1.1").to_json_dict()
    with pytest.raises(UnsupportedRecordSchema, match="incompatible"):
        DecisionRecord.from_json_dict(higher_minor_payload)


def test_record_parser_rejects_malformed_versions_and_evidence_shapes() -> None:
    """Bad ledger data must fail at the boundary instead of becoming ambiguous evidence."""
    malformed_version = _record().to_json_dict()
    malformed_version["schema_version"] = "1"
    with pytest.raises(ValueError, match="Invalid record schema version"):
        DecisionRecord.from_json_dict(malformed_version)

    negative_version = _record().to_json_dict()
    negative_version["schema_version"] = "1.-1"
    with pytest.raises(ValueError, match="Invalid record schema version"):
        DecisionRecord.from_json_dict(negative_version)

    malformed_evidence = _record().to_json_dict()
    malformed_evidence["evidence"] = ["metrics.json", 7]
    with pytest.raises(ValueError, match="evidence"):
        DecisionRecord.from_json_dict(malformed_evidence)

    malformed_metadata = _record().to_json_dict()
    malformed_metadata["metadata"] = {"run": 42}
    with pytest.raises(ValueError, match="metadata"):
        DecisionRecord.from_json_dict(malformed_metadata)

    missing_string_field = _record().to_json_dict()
    missing_string_field["record_id"] = 42
    with pytest.raises(ValueError, match="record_id"):
        DecisionRecord.from_json_dict(missing_string_field)


def test_emit_record_logs_a_machine_readable_event() -> None:
    """Ledger collectors depend on one stable event key instead of parsing prose."""
    stream = StringIO()
    configure_logging(level="INFO", stream=stream)
    record = _record()

    emit_record(record)

    payload = json.loads(stream.getvalue())
    assert payload["event"] == record.to_json_dict()
    assert payload["message"] == "Decision record emitted"
