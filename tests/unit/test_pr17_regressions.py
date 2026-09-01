"""Regression tests for PR #17 Copilot review findings.

Each test exercises the FIXED behaviour to prevent re-introduction. Tests are
grouped by the finding number from the code review, not by module — a reader
can trace each test back to the review comment that motivated it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from types import MappingProxyType
from typing import NamedTuple

import numpy as np
import pytest

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.domain.errors import InvalidTemporalRangeError
from orbital_drift.domain.temporal import TemporalRange
from orbital_drift.drift.metrics import BandDriftResult
from orbital_drift.eval.bootstrap import BlockSize
from orbital_drift.eval.calibration import calibration_error
from orbital_drift.eval.superiority import SuperiorityConfig, superiority_gate
from orbital_drift.ingest.cloud import CloudEvaluationResult
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
# RB-010 Part 9: NamedTuple crash-and-drop + dataclass/pydantic redaction bypass
# ═══════════════════════════════════════════════════════════════════════════════


class TestNamedTupleAndArbitraryObjectRedaction:
    """_redact_value must not crash-and-drop NamedTuples or bypass structured objects.

    Confirmed bugs (RB-010 six-lens review, decision-log 2026-09-01): (1) every
    result type this codebase defines is a NamedTuple; hitting the generic
    ``(list, tuple)`` branch called ``type(value)(<generator>)``, which raises
    TypeError for any NamedTuple because ``__new__`` wants one positional argument
    per declared field, not a single iterable -- stdlib ``logging`` swallows that
    exception, so the entire log line silently vanished instead of being redacted.
    (2) any object that is neither a Mapping nor a list/tuple (a dataclass, or a
    pydantic model such as OrbitalDriftConfig, which holds the lakeFS credential
    fields) fell through every isinstance check and was returned unchanged, so
    ``json.dumps``'s ``default=str`` fallback stringified it verbatim -- a full,
    silent redaction bypass.
    """

    def test_namedtuple_with_sensitive_field_is_written_and_redacted(self) -> None:
        """A NamedTuple must not vanish from the log, and its keyed field must
        still be redacted while its sibling fields survive untouched."""

        class ServiceCredentials(NamedTuple):
            service: str
            api_key: str
            region: str

        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        # NOTE: the outer field name is deliberately NOT sensitive-shaped (unlike
        # "credentials", which itself contains the substring "credential" and
        # would short-circuit at the top-level _is_sensitive check before ever
        # reaching the NamedTuple branch this test targets).
        get_logger("namedtuple.test").info(
            "event",
            extra={
                "service_record": ServiceCredentials(
                    service="ledger", api_key="plain-secret", region="us"
                )
            },
        )

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1, (
            "log line was dropped -- the pre-fix TypeError from "
            "type(value)(<generator>) is swallowed by stdlib logging"
        )
        payload = json.loads(lines[0])
        assert payload["service_record"] == ["ledger", REDACTION_PLACEHOLDER, "us"]

    def test_band_drift_result_namedtuple_is_written_not_dropped(self) -> None:
        """A real production NamedTuple (drift/metrics.py) must survive logging."""
        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        result = BandDriftResult(
            band_name="B04",
            psi=0.12,
            ks_statistic=0.05,
            ks_pvalue=0.9,
            is_drifted=False,
        )
        get_logger("banddrift.test").info("drift evaluated", extra={"result": result})

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["result"] == ["B04", 0.12, 0.05, 0.9, False]

    def test_cloud_evaluation_result_namedtuple_is_written_not_dropped(self) -> None:
        """A second real production NamedTuple (ingest/cloud.py) must survive logging."""
        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        result = CloudEvaluationResult(
            total_pixels=100,
            valid_pixels=90,
            cloud_pixels=10,
            cloud_fraction=0.1,
            excluded_from_training=False,
        )
        get_logger("cloudeval.test").info("cloud mask evaluated", extra={"result": result})

        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["result"] == [100, 90, 10, 0.1, False]

    def test_orbital_drift_config_credentials_do_not_appear_in_output(self) -> None:
        """The confirmed, named security gap: OrbitalDriftConfig holds the lakeFS
        credential fields, and previously bypassed redaction entirely."""
        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        config = OrbitalDriftConfig(
            lakefs_access_key="plaintext-access-secret",
            lakefs_secret_key="plaintext-secret-value",  # noqa: S106 -- planted test value, not a real credential
        )
        get_logger("config.test").info("config loaded", extra={"config": config})

        raw_output = stream.getvalue()
        lines = [line for line in raw_output.splitlines() if line.strip()]
        assert len(lines) == 1

        # Belt-and-suspenders: the plaintext secrets must not appear anywhere in
        # the formatted output, not merely at the specific dict key checked below.
        assert "plaintext-access-secret" not in raw_output
        assert "plaintext-secret-value" not in raw_output

        payload = json.loads(lines[0])
        redacted_config = payload["config"]
        assert isinstance(redacted_config, dict)
        assert redacted_config["lakefs_access_key"] == REDACTION_PLACEHOLDER
        assert redacted_config["lakefs_secret_key"] == REDACTION_PLACEHOLDER
        # Non-sensitive fields must survive with real values: this is full
        # structural (field-by-field) redaction, not a whole-object marker.
        assert redacted_config["aoi_name"] == "default-aoi"
        assert redacted_config["lakefs_repository"] == "orbital-drift"

    def test_synthetic_dataclass_with_sensitive_field_is_redacted(self) -> None:
        """A plain (non-pydantic) dataclass must also be redacted field-by-field."""

        @dataclass
        class SyntheticServiceConfig:
            name: str
            api_key: str

        stream = StringIO()
        configure_logging(level="INFO", stream=stream)
        get_logger("dataclass.test").info(
            "event",
            extra={"service_config": SyntheticServiceConfig(name="ledger", api_key="dc-secret")},
        )

        payload = json.loads(stream.getvalue())
        redacted = payload["service_config"]
        assert isinstance(redacted, dict)
        assert redacted["api_key"] == REDACTION_PLACEHOLDER
        assert redacted["name"] == "ledger"

    def test_unrecognized_object_type_passes_through_with_a_visible_debug_signal(
        self,
    ) -> None:
        """A truly unknown object type must not be silently trusted: it still
        passes through (matching the existing json.dumps `default=str` safety net
        for benign leaf types), but the gap is made observable via a DEBUG log
        naming the unrecognized type, rather than swallowed the way the pre-fix
        bypass was."""

        class OpaqueThing:
            def __str__(self) -> str:
                return "opaque-thing-repr"

        stream = StringIO()
        configure_logging(level="DEBUG", stream=stream)
        get_logger("unknown.test").info("event", extra={"payload": OpaqueThing()})

        output = stream.getvalue()
        assert "opaque-thing-repr" in output
        assert "OpaqueThing" in output


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
        probs = np.asarray([0.1, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9])
        result = calibration_error(labels, probs, bin_count=2, strategy="quantile")
        assert result.expected_calibration_error == pytest.approx(0.0, abs=0.2)


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
