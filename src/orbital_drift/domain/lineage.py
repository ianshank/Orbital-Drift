"""Canonical, portable provenance envelopes for trained artifacts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Final, cast

from orbital_drift.domain.errors import (
    InvalidLineageError,
    NonFiniteMetricError,
    UnsupportedSchemaVersionError,
)

SCHEMA_VERSION: Final = "1.0"
DEFAULT_TOOLCHAIN: Final[Mapping[str, str]] = MappingProxyType({})
DEFAULT_METRICS: Final[Mapping[str, float]] = MappingProxyType({})
DEFAULT_SPATIAL_SPLIT_ID: Final = "unknown"
DEFAULT_LABEL_STRATEGY: Final = "unspecified"
DEFAULT_CODE_VERSION: Final = "unknown"
DEFAULT_NOTES: Final = ""


def _require_aware(instant: datetime, field_name: str) -> None:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidLineageError(f"{field_name} must be timezone-aware")


def _iso_utc(instant: datetime) -> str:
    """Render an aware datetime in a byte-stable UTC form ending in ``Z``."""
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_schema_version(schema_version: str) -> tuple[int, int]:
    """Parse the deliberately small major.minor compatibility identifier."""
    parts = schema_version.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise UnsupportedSchemaVersionError(f"invalid schema version: {schema_version!r}")
    return int(parts[0]), int(parts[1])


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidLineageError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidLineageError(f"{field_name} must be an ISO-8601 datetime") from error
    _require_aware(parsed, field_name)
    return parsed


def _required_string(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise InvalidLineageError(f"{field_name} must be a string")
    return value


def _optional_string(payload: Mapping[str, object], field_name: str, default: str) -> str:
    value = payload.get(field_name, default)
    if not isinstance(value, str):
        raise InvalidLineageError(f"{field_name} must be a string")
    return value


def _string_mapping(value: object, field_name: str) -> Mapping[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise InvalidLineageError(f"{field_name} must be an object of string values")
    return cast(Mapping[str, str], value)


def _float_mapping(value: object, field_name: str) -> Mapping[str, float]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, (int, float)) and not isinstance(item, bool)
        for key, item in value.items()
    ):
        raise InvalidLineageError(f"{field_name} must be an object of numeric values")
    return {key: float(item) for key, item in value.items()}


@dataclass(frozen=True)
class LineageEnvelope:
    """Complete provenance required to reproduce or audit a trained artifact.

    Mapping fields are copied into read-only mapping proxies. Canonical JSON uses the
    standard library's shortest ``repr``-stable float rendering through ``json.dumps``;
    NaN and infinities are rejected instead of being emitted as non-standard JSON tokens.
    Older payloads accepted by :meth:`from_canonical_json` default omitted optional
    provenance fields to the documented ``DEFAULT_*`` constants above.
    """

    schema_version: str
    envelope_id: str
    created_at: datetime
    git_sha: str
    config_hash: str
    data_commit: str
    data_repository: str
    data_branch: str
    dataset_name: str
    model_name: str
    model_version: str
    run_id: str
    random_seed: int
    python_version: str
    platform_tag: str
    toolchain: Mapping[str, str]
    metrics: Mapping[str, float]
    spatial_split_id: str
    label_strategy: str
    code_version: str
    notes: str

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")
        if not isinstance(self.random_seed, int) or isinstance(self.random_seed, bool):
            raise InvalidLineageError("random_seed must be an integer")
        copied_toolchain = dict(self.toolchain)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in copied_toolchain.items()
        ):
            raise InvalidLineageError("toolchain must map strings to strings")
        copied_metrics = dict(self.metrics)
        for key, value in copied_metrics.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, (int, float))
                or isinstance(value, bool)
            ):
                raise InvalidLineageError("metrics must map strings to floats")
            if not math.isfinite(value):
                raise NonFiniteMetricError(f"metric '{key}' must be finite")
        object.__setattr__(self, "toolchain", MappingProxyType(copied_toolchain))
        object.__setattr__(self, "metrics", MappingProxyType(copied_metrics))

    def to_canonical_json(self) -> str:
        """Return sorted, compact, ASCII-safe, valid JSON whose bytes are stable."""
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "envelope_id": self.envelope_id,
            "created_at": _iso_utc(self.created_at),
            "git_sha": self.git_sha,
            "config_hash": self.config_hash,
            "data_commit": self.data_commit,
            "data_repository": self.data_repository,
            "data_branch": self.data_branch,
            "dataset_name": self.dataset_name,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "run_id": self.run_id,
            "random_seed": self.random_seed,
            "python_version": self.python_version,
            "platform_tag": self.platform_tag,
            "toolchain": dict(self.toolchain),
            "metrics": dict(self.metrics),
            "spatial_split_id": self.spatial_split_id,
            "label_strategy": self.label_strategy,
            "code_version": self.code_version,
            "notes": self.notes,
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    def content_hash(self) -> str:
        """Return the SHA-256 hash of this envelope's canonical JSON bytes."""
        return sha256(self.to_canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_canonical_json(cls, encoded: str) -> LineageEnvelope:
        """Parse a compatible canonical payload and fill documented legacy defaults."""
        try:
            decoded = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise InvalidLineageError("lineage payload is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise InvalidLineageError("lineage payload must be a JSON object")
        payload = cast(dict[str, object], decoded)
        schema_version = _required_string(payload, "schema_version")
        incoming_major, incoming_minor = _parse_schema_version(schema_version)
        parser_major, parser_minor = _parse_schema_version(SCHEMA_VERSION)
        if incoming_major != parser_major or incoming_minor > parser_minor:
            raise UnsupportedSchemaVersionError(
                f"unsupported schema version {schema_version}; parser supports {SCHEMA_VERSION}"
            )
        random_seed = payload.get("random_seed")
        if not isinstance(random_seed, int) or isinstance(random_seed, bool):
            raise InvalidLineageError("random_seed must be an integer")
        return cls(
            schema_version=schema_version,
            envelope_id=_required_string(payload, "envelope_id"),
            created_at=_parse_datetime(payload.get("created_at"), "created_at"),
            git_sha=_required_string(payload, "git_sha"),
            config_hash=_required_string(payload, "config_hash"),
            data_commit=_required_string(payload, "data_commit"),
            data_repository=_required_string(payload, "data_repository"),
            data_branch=_required_string(payload, "data_branch"),
            dataset_name=_required_string(payload, "dataset_name"),
            model_name=_required_string(payload, "model_name"),
            model_version=_required_string(payload, "model_version"),
            run_id=_required_string(payload, "run_id"),
            random_seed=random_seed,
            python_version=_required_string(payload, "python_version"),
            platform_tag=_required_string(payload, "platform_tag"),
            toolchain=_string_mapping(
                payload.get("toolchain", dict(DEFAULT_TOOLCHAIN)), "toolchain"
            ),
            metrics=_float_mapping(payload.get("metrics", dict(DEFAULT_METRICS)), "metrics"),
            spatial_split_id=_optional_string(
                payload, "spatial_split_id", DEFAULT_SPATIAL_SPLIT_ID
            ),
            label_strategy=_optional_string(payload, "label_strategy", DEFAULT_LABEL_STRATEGY),
            code_version=_optional_string(payload, "code_version", DEFAULT_CODE_VERSION),
            notes=_optional_string(payload, "notes", DEFAULT_NOTES),
        )
