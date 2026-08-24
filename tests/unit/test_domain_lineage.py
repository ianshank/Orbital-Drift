"""Canonical provenance, compatibility corpus, and remaining port fake tests."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orbital_drift.domain.errors import (
    InvalidLineageError,
    NonFiniteMetricError,
    UnsupportedSchemaVersionError,
)
from orbital_drift.domain.lineage import (
    DEFAULT_CODE_VERSION,
    DEFAULT_LABEL_STRATEGY,
    DEFAULT_METRICS,
    DEFAULT_NOTES,
    DEFAULT_SPATIAL_SPLIT_ID,
    DEFAULT_TOOLCHAIN,
    SCHEMA_VERSION,
    LineageEnvelope,
)
from orbital_drift.ports.compute import ComputePort, DeviceSpec, InMemoryCompute
from orbital_drift.ports.dataversion import DataVersionPort, InMemoryDataVersion
from orbital_drift.ports.registry import InMemoryModelRegistry, ModelRegistryPort


def _exact(message: str) -> str:
    """Build a regex that accepts one exact exception message."""
    return rf"^{re.escape(message)}$"


GOLDEN_DIRECTORY = Path(__file__).resolve().parents[1] / "golden" / "lineage"


def _envelope(**changes: object) -> LineageEnvelope:
    values: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "envelope_id": "env-001",
        "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "git_sha": "deadbeef",
        "config_hash": "config-abc",
        "data_commit": "commit-123",
        "data_repository": "lakefs://imagery",
        "data_branch": "main",
        "dataset_name": "sentinel-train",
        "model_name": "change-detector",
        "model_version": "2.1.0",
        "run_id": "run-001",
        "random_seed": 42,
        "python_version": "3.12.0",
        "platform_tag": "linux-x86_64",
        "toolchain": {"driver": "550", "cuda": "12.4", "framework": "torch-2.6"},
        "metrics": {"loss": 0.125, "accuracy": 0.99},
        "spatial_split_id": "split-west",
        "label_strategy": "human-reviewed",
        "code_version": "release-7",
        "notes": "golden full envelope",
    }
    values.update(changes)
    return LineageEnvelope(**values)  # type: ignore[arg-type]


def test_canonical_json_hash_is_independent_of_mapping_insertion_order_and_round_trips() -> None:
    first = _envelope()
    second = _envelope(
        toolchain={"framework": "torch-2.6", "cuda": "12.4", "driver": "550"},
        metrics={"accuracy": 0.99, "loss": 0.125},
    )

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.content_hash() == second.content_hash()
    assert first.to_canonical_json() == (
        '{"code_version":"release-7","config_hash":"config-abc","created_at":"2026-01-02T03:04:05Z",'
        '"data_branch":"main","data_commit":"commit-123","data_repository":"lakefs://imagery",'
        '"dataset_name":"sentinel-train","envelope_id":"env-001","git_sha":"deadbeef",'
        '"label_strategy":"human-reviewed","metrics":{"accuracy":0.99,"loss":0.125},'
        '"model_name":"change-detector","model_version":"2.1.0","notes":"golden full envelope",'
        '"platform_tag":"linux-x86_64","python_version":"3.12.0","random_seed":42,"run_id":"run-001",'
        '"schema_version":"1.0","spatial_split_id":"split-west",'
        '"toolchain":{"cuda":"12.4","driver":"550","framework":"torch-2.6"}}'
    )
    assert LineageEnvelope.from_canonical_json(first.to_canonical_json()) == first


@pytest.mark.parametrize(
    "field_change",
    [
        {"schema_version": "0.9"},
        {"envelope_id": "env-002"},
        {"created_at": datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)},
        {"git_sha": "other"},
        {"config_hash": "other"},
        {"data_commit": "other"},
        {"data_repository": "other"},
        {"data_branch": "other"},
        {"dataset_name": "other"},
        {"model_name": "other"},
        {"model_version": "other"},
        {"run_id": "other"},
        {"random_seed": 43},
        {"python_version": "other"},
        {"platform_tag": "other"},
        {"toolchain": {"driver": "other"}},
        {"metrics": {"loss": 0.25}},
        {"spatial_split_id": "other"},
        {"label_strategy": "other"},
        {"code_version": "other"},
        {"notes": "other"},
    ],
)
def test_changing_each_single_lineage_field_changes_the_hash(
    field_change: dict[str, object],
) -> None:
    original = _envelope()

    assert _envelope(**field_change).content_hash() != original.content_hash()


def test_golden_full_payload_parses_to_exact_values() -> None:
    envelope = LineageEnvelope.from_canonical_json(
        (GOLDEN_DIRECTORY / "v1_0_full.json").read_text()
    )

    assert envelope.envelope_id == "env-001"
    assert envelope.created_at == datetime(2026, 1, 2, 1, 4, 5, tzinfo=UTC)
    assert envelope.toolchain == {"cuda": "12.4", "framework": "torch-2.6", "driver": "550"}
    assert envelope.metrics == {"accuracy": 0.99, "loss": 0.125}
    assert envelope.spatial_split_id == "split-west"
    assert envelope.label_strategy == "human-reviewed"
    assert envelope.code_version == "release-7"
    assert envelope.notes == "golden full envelope"


def test_golden_minimal_legacy_payload_gets_documented_defaults() -> None:
    envelope = LineageEnvelope.from_canonical_json(
        (GOLDEN_DIRECTORY / "v1_0_minimal_legacy.json").read_text()
    )

    assert envelope.envelope_id == "env-legacy"
    assert envelope.toolchain == DEFAULT_TOOLCHAIN
    assert envelope.metrics == DEFAULT_METRICS
    assert envelope.spatial_split_id == DEFAULT_SPATIAL_SPLIT_ID
    assert envelope.label_strategy == DEFAULT_LABEL_STRATEGY
    assert envelope.code_version == DEFAULT_CODE_VERSION
    assert envelope.notes == DEFAULT_NOTES


def test_lineage_validation_and_parser_error_types_are_exact() -> None:
    with pytest.raises(InvalidLineageError, match=_exact("created_at must be timezone-aware")):
        _envelope(created_at=datetime(2026, 1, 1))
    with pytest.raises(InvalidLineageError, match=_exact("random_seed must be an integer")):
        _envelope(random_seed=True)
    with pytest.raises(InvalidLineageError, match=_exact("toolchain must map strings to strings")):
        _envelope(toolchain={"driver": 550})
    with pytest.raises(InvalidLineageError, match=_exact("metrics must map strings to floats")):
        _envelope(metrics={"loss": "bad"})
    with pytest.raises(NonFiniteMetricError, match=_exact("metric 'loss' must be finite")):
        _envelope(metrics={"loss": float("nan")})
    with pytest.raises(NonFiniteMetricError, match=_exact("metric 'loss' must be finite")):
        _envelope(metrics={"loss": float("inf")})
    with pytest.raises(InvalidLineageError, match=_exact("lineage payload is not valid JSON")):
        LineageEnvelope.from_canonical_json("{")
    with pytest.raises(InvalidLineageError, match=_exact("lineage payload must be a JSON object")):
        LineageEnvelope.from_canonical_json("[]")
    with pytest.raises(
        UnsupportedSchemaVersionError,
        match=_exact("unsupported schema version 2.0; parser supports 1.0"),
    ):
        LineageEnvelope.from_canonical_json(
            _envelope().to_canonical_json().replace('"1.0"', '"2.0"')
        )
    with pytest.raises(
        UnsupportedSchemaVersionError, match=_exact("invalid schema version: 'version-one'")
    ):
        LineageEnvelope.from_canonical_json(
            _envelope(schema_version="version-one").to_canonical_json()
        )


def test_lineage_parser_rejects_each_invalid_payload_shape() -> None:
    payload = json.loads(_envelope().to_canonical_json())

    payload_without_datetime = dict(payload)
    del payload_without_datetime["created_at"]
    with pytest.raises(InvalidLineageError, match=_exact("created_at must be an ISO-8601 string")):
        LineageEnvelope.from_canonical_json(json.dumps(payload_without_datetime))
    payload_invalid_datetime = dict(payload, created_at="not-a-datetime")
    with pytest.raises(
        InvalidLineageError, match=_exact("created_at must be an ISO-8601 datetime")
    ):
        LineageEnvelope.from_canonical_json(json.dumps(payload_invalid_datetime))
    payload_without_envelope_id = dict(payload)
    del payload_without_envelope_id["envelope_id"]
    with pytest.raises(InvalidLineageError, match=_exact("envelope_id must be a string")):
        LineageEnvelope.from_canonical_json(json.dumps(payload_without_envelope_id))
    with pytest.raises(InvalidLineageError, match=_exact("notes must be a string")):
        LineageEnvelope.from_canonical_json(json.dumps(dict(payload, notes=1)))
    with pytest.raises(
        InvalidLineageError, match=_exact("toolchain must be an object of string values")
    ):
        LineageEnvelope.from_canonical_json(json.dumps(dict(payload, toolchain={"cuda": 12})))
    with pytest.raises(
        InvalidLineageError, match=_exact("metrics must be an object of numeric values")
    ):
        LineageEnvelope.from_canonical_json(json.dumps(dict(payload, metrics={"loss": True})))
    with pytest.raises(InvalidLineageError, match=_exact("random_seed must be an integer")):
        LineageEnvelope.from_canonical_json(json.dumps(dict(payload, random_seed=False)))


def test_compute_port_fake_runtime_protocol_and_errors() -> None:
    cpu = DeviceSpec("cpu-0", "cpu", None, 16_000)
    compute = InMemoryCompute((cpu,), {"training": "cpu-0"}, {"cpu-0": 12_000})

    assert isinstance(compute, ComputePort)
    assert compute.devices() == (cpu,)
    assert compute.select("training") == cpu
    assert compute.memory_budget_bytes(cpu) == 12_000
    with pytest.raises(KeyError, match=_exact("'no device configured for role: inference'")):
        compute.select("inference")
    bad_assignment = InMemoryCompute((cpu,), {"training": "gpu-0"}, {})
    with pytest.raises(KeyError, match=_exact("'configured device not found: gpu-0'")):
        bad_assignment.select("training")
    with pytest.raises(KeyError, match=_exact("'no memory budget configured for device: cpu-0'")):
        bad_assignment.memory_budget_bytes(cpu)


def test_registry_port_fake_runtime_protocol_transitions_and_rolls_back() -> None:
    first = _envelope(model_version="1.0")
    second = _envelope(model_version="2.0")
    registry = InMemoryModelRegistry()

    assert isinstance(registry, ModelRegistryPort)
    assert registry.register(first) == first
    assert registry.register(second) == second
    assert registry.transition_stage("change-detector", "1.0", "production") == first
    assert registry.transition_stage("change-detector", "2.0", "production") == second
    assert registry.get_by_stage("change-detector", "production") == second
    assert registry.rollback("change-detector", "production") == first
    with pytest.raises(
        KeyError, match=_exact("'model version not registered: change-detector/3.0'")
    ):
        registry.transition_stage("change-detector", "3.0", "production")
    with pytest.raises(KeyError, match=_exact("'stage not assigned: other/production'")):
        registry.get_by_stage("other", "production")
    with pytest.raises(
        KeyError, match=_exact("'no rollback target for stage: change-detector/production'")
    ):
        registry.rollback("change-detector", "production")


def test_data_version_port_fake_runtime_protocol_and_branch_rules() -> None:
    data_version = InMemoryDataVersion()

    assert isinstance(data_version, DataVersionPort)
    assert data_version.create_branch("experiment", "main") == "experiment"
    assert data_version.resolve_commit("experiment") == "commit-0000"
    assert data_version.commit("experiment", "add labels") == "commit-0001"
    assert data_version.resolve_commit("commit-0001") == "commit-0001"
    assert data_version.merge("experiment", "main") == "commit-0001"
    assert data_version.protect_branch("main") == "main"
    with pytest.raises(ValueError, match=_exact("branch already exists: experiment")):
        data_version.create_branch("experiment", "main")
    with pytest.raises(ValueError, match=_exact("commit message must be non-empty")):
        data_version.commit("experiment", "")
    with pytest.raises(PermissionError, match=_exact("branch is protected: main")):
        data_version.commit("main", "blocked")
    with pytest.raises(PermissionError, match=_exact("branch is protected: main")):
        data_version.merge("experiment", "main")
    with pytest.raises(KeyError, match=_exact("'branch not found: absent'")):
        data_version.commit("absent", "new data")
    with pytest.raises(KeyError, match=_exact("'branch not found: absent'")):
        data_version.protect_branch("absent")
    with pytest.raises(KeyError, match=_exact("'reference not found: absent'")):
        data_version.resolve_commit("absent")
