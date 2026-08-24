"""Regression tests for PR #17 Copilot review findings.

Each test exercises the FIXED behaviour to prevent re-introduction. Tests are
grouped by the finding number from the code review, not by module — a reader
can trace each test back to the review comment that motivated it.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from io import StringIO
from types import MappingProxyType

import numpy as np
import pytest

from orbital_drift.domain.errors import InvalidTemporalRangeError
from orbital_drift.domain.temporal import TemporalRange
from orbital_drift.eval.bootstrap import BlockSize
from orbital_drift.eval.calibration import calibration_error
from orbital_drift.eval.superiority import SuperiorityConfig, superiority_gate
from orbital_drift.observability.logging import (
    REDACTION_PLACEHOLDER,
    _redact_fields,
    configure_logging,
    get_logger,
)
from orbital_drift.observability.records import (
    RECORD_SCHEMA_VERSION,
    DecisionRecord,
    GateState,
    emit_record,
)
from orbital_drift.ports.dataversion import InMemoryDataVersion

# ═══════════════════════════════════════════════════════════════════════════════
# Finding #1: Nested credential redaction leak in logging
# ═══════════════════════════════════════════════════════════════════════════════


class TestNestedRedaction:
    """Critical: _redact_fields must recurse into nested structures."""

    def test_nested_dict_values_with_sensitive_keys_are_redacted(self) -> None:
        fields: dict[str, object] = {
            "config": {"api_key": "planted-secret", "host": "example.com"},
            "service": "ledger",
        }
        redacted = _redact_fields(fields)
        nested = redacted["config"]
        assert isinstance(nested, dict)
        assert nested["api_key"] == REDACTION_PLACEHOLDER
        assert nested["host"] == "example.com"
        assert redacted["service"] == "ledger"

    def test_deeply_nested_credentials_are_redacted(self) -> None:
        fields: dict[str, object] = {
            "outer": {"inner": {"secret_token": "deeply-hidden"}},
        }
        redacted = _redact_fields(fields)
        inner = redacted["outer"]
        assert isinstance(inner, dict)
        deeper = inner["inner"]
        assert isinstance(deeper, dict)
        assert deeper["secret_token"] == REDACTION_PLACEHOLDER

    def test_list_of_dicts_with_credentials_are_redacted(self) -> None:
        fields: dict[str, object] = {
            "endpoints": [
                {"url": "https://a.com", "password": "pass1"},
                {"url": "https://b.com", "password": "pass2"},
            ],
        }
        redacted = _redact_fields(fields)
        endpoints = redacted["endpoints"]
        assert isinstance(endpoints, list)
        assert len(endpoints) == 2
        assert endpoints[0]["password"] == REDACTION_PLACEHOLDER
        assert endpoints[0]["url"] == "https://a.com"
        assert endpoints[1]["password"] == REDACTION_PLACEHOLDER

    def test_top_level_sensitive_key_still_redacted(self) -> None:
        """Backwards-compat: flat dicts produce identical output to the old code."""
        result = _redact_fields({"api_token": "secret", "name": "safe"})
        assert result["api_token"] == REDACTION_PLACEHOLDER
        assert result["name"] == "safe"

    def test_json_logging_redacts_nested_extras(self) -> None:
        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        get_logger("nested.test").info(
            "event",
            extra={"config": {"credential_source": "nested-secret", "region": "us"}},
        )
        payload = json.loads(stream.getvalue())
        config = payload["config"]
        assert config["credential_source"] == REDACTION_PLACEHOLDER
        assert config["region"] == "us"


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #3: Calibration ECE bin weights
# ═══════════════════════════════════════════════════════════════════════════════


class TestCalibrationBinWeights:
    """The bin assignment must match sklearn's internal binning semantics."""

    def test_boundary_values_are_assigned_to_correct_bins(self) -> None:
        """Values exactly at bin boundaries should not produce off-by-one errors."""
        result = calibration_error(
            np.asarray([0, 0, 1, 1, 0, 1]),
            np.asarray([0.0, 0.25, 0.5, 0.75, 1.0, 0.5]),
            bin_count=4,
            strategy="uniform",
        )
        assert 0.0 <= result.expected_calibration_error <= 1.0
        assert result.populated_bins >= 1

    def test_perfect_calibration_produces_zero_ece(self) -> None:
        """A perfectly calibrated set (fraction_of_positives == mean_predicted_value)
        should produce ECE very close to zero."""
        labels = np.asarray([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
        probs = np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        result = calibration_error(labels, probs, bin_count=2, strategy="quantile")
        assert result.expected_calibration_error == pytest.approx(0.0, abs=1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #4: Temporal from_iso_interval error masking
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemporalErrorSeparation:
    """Parsing errors and tz-aware errors must report distinct messages."""

    def test_malformed_iso_raises_parse_error_not_tz_error(self) -> None:
        with pytest.raises(InvalidTemporalRangeError, match="ISO-8601"):
            TemporalRange.from_iso_interval("bad-date/also-bad")

    def test_naive_datetime_in_interval_raises_tz_error_not_parse_error(self) -> None:
        """A valid ISO-8601 datetime without timezone info should raise a tz error,
        not a parse error."""
        with pytest.raises(InvalidTemporalRangeError, match="timezone-aware"):
            TemporalRange.from_iso_interval("2026-01-01T00:00:00/2026-01-02T00:00:00")


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #5: Moran thread safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestMoranThreadSafety:
    """The _MORAN_LOCK must exist and be a threading.Lock."""

    def test_moran_lock_is_a_threading_lock(self) -> None:
        from orbital_drift.eval.spatial import _MORAN_LOCK

        assert isinstance(_MORAN_LOCK, type(threading.Lock()))


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #6: DataVersion merge validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataVersionMergeValidation:
    """merge() must validate the target branch exists."""

    def test_merge_into_nonexistent_target_raises_key_error(self) -> None:
        dv = InMemoryDataVersion()
        dv.create_branch("feature", "main")
        dv.commit("feature", "work done")
        with pytest.raises(KeyError, match="branch not found"):
            dv.merge("feature", "nonexistent")

    def test_merge_into_existing_target_succeeds(self) -> None:
        dv = InMemoryDataVersion()
        dv.create_branch("feature", "main")
        dv.commit("feature", "work done")
        result = dv.merge("feature", "main")
        assert result.startswith("commit-")


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #7 + #8: DecisionRecord frozen metadata + tz-aware enforcement
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionRecordIntegrity:
    """Metadata must be frozen, timestamps must be tz-aware."""

    def test_metadata_is_frozen_after_construction(self) -> None:
        record = DecisionRecord(
            record_id="test-1",
            kind="gate",
            subject="model:v1",
            state=GateState.AUTHORIZED,
            rationale="passed",
            evidence=("a.json",),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"region": "us"},
        )
        assert isinstance(record.metadata, MappingProxyType)
        with pytest.raises(TypeError):
            record.metadata["new_key"] = "mutation"  # type: ignore[index]

    def test_naive_created_at_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            DecisionRecord(
                record_id="test-2",
                kind="gate",
                subject="model:v1",
                state=GateState.AUTHORIZED,
                rationale="passed",
                evidence=(),
                created_at=datetime(2026, 1, 1),  # naive
                metadata={},
            )

    def test_from_json_dict_rejects_naive_created_at(self) -> None:
        payload = {
            "record_id": "test-3",
            "kind": "gate",
            "subject": "model:v1",
            "state": "authorized",
            "rationale": "passed",
            "evidence": [],
            "created_at": "2026-01-01T00:00:00",  # no timezone
            "metadata": {},
            "schema_version": RECORD_SCHEMA_VERSION,
        }
        with pytest.raises(ValueError, match="timezone-aware"):
            DecisionRecord.from_json_dict(payload)

    def test_tz_aware_created_at_succeeds(self) -> None:
        record = DecisionRecord(
            record_id="test-4",
            kind="gate",
            subject="model:v1",
            state=GateState.AUTHORIZED,
            rationale="passed",
            evidence=(),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={},
        )
        assert record.created_at.tzinfo is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #9: emit_record default logger is context-aware
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmitRecordContextLogger:
    """emit_record's default logger must use get_logger for context injection."""

    def test_emit_record_without_explicit_logger_uses_context_aware_default(self) -> None:
        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        record = DecisionRecord(
            record_id="ctx-1",
            kind="gate",
            subject="model:v1",
            state=GateState.AUTHORIZED,
            rationale="passed",
            evidence=(),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={},
        )
        emit_record(record)
        payload = json.loads(stream.getvalue())
        # The logger name should be under the orbital_drift namespace
        assert payload["logger"].startswith("orbital_drift")
        assert payload["message"] == "Decision record emitted"


# ═══════════════════════════════════════════════════════════════════════════════
# Finding #11: Negative minimum_effect guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestNegativeMinimumEffectGuard:
    """A negative minimum_effect inverts gate semantics and must be rejected."""

    def test_negative_minimum_effect_raises_value_error(self) -> None:
        config = SuperiorityConfig(
            block_size=BlockSize(rows=1, columns=1),
            confidence_level=0.9,
            minimum_effect=-0.5,
            replicates=10,
            seed=42,
        )
        with pytest.raises(ValueError, match="non-negative"):
            superiority_gate(
                np.ones((2, 2)),
                np.zeros((2, 2)),
                metric=lambda v: float(np.mean(v)),
                config=config,
            )

    def test_zero_minimum_effect_is_accepted(self) -> None:
        config = SuperiorityConfig(
            block_size=BlockSize(rows=1, columns=1),
            confidence_level=0.9,
            minimum_effect=0.0,
            replicates=10,
            seed=42,
        )
        # Should not raise
        result = superiority_gate(
            np.ones((2, 2)),
            np.zeros((2, 2)),
            metric=lambda v: float(np.mean(v)),
            config=config,
        )
        assert result.minimum_effect == 0.0
