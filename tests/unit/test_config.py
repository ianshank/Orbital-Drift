"""Unit tests for orbital_drift configuration and validation (Constitution Principle III).

Also covers two defects fixed under RB-010 part 4 (see docs/decision-log.md):

1. Credential placeholder defaults (Major security finding). `lakefs_access_key`
   and `lakefs_secret_key` previously defaulted to the plausible-looking
   placeholder strings "lakefs_access_key"/"lakefs_secret_key", so a
   misconfigured deployment would silently start with a fake, guessable
   "credential" instead of failing fast. Both fields are now required (no
   `default=`), so constructing `OrbitalDriftConfig` without them set raises a
   `pydantic.ValidationError` at startup.
2. Missing numeric bounds (Major finding). None of the numeric `Field`s
   declared `ge`/`le`/`gt`/`lt` despite several having documented bounded
   semantics in their own `description=` (e.g. "0.0 to 1.0"). Out-of-range
   values (a canary ratio of 5.0, a negative patch size, ...) previously
   passed validation silently; they now raise `pydantic.ValidationError`.

A styling note that explains most of this file's shape: this repo's mypy
config has no pydantic plugin, so type-checking relies on mypy's native PEP
681 `dataclass_transform` support -- it synthesizes `OrbitalDriftConfig`'s
`__init__` purely from the model's fields. Now that the two lakeFS fields are
required, that synthesized signature requires `lakefs_access_key`/
`lakefs_secret_key` as keyword arguments, and it does not know about
`BaseSettings`-only constructor kwargs like `_env_file` at all (those come
from a hand-written `__init__` the synthesized signature does not see).
Tests that are not actually about credentials pass them as plain, fully
type-checked keyword arguments via `_construct_with_valid_credentials()`
below. Only the small handful of tests whose entire point is to exercise
`pydantic-settings`' runtime env-var sourcing of those two fields construct
`OrbitalDriftConfig` directly with zero credential kwargs; those need a
narrowly-scoped `# type: ignore[call-arg]`, same as this module's existing
`get_config(**overrides)` helper already carries for the same underlying
reason.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orbital_drift.config import OrbitalDriftConfig

# Not real credentials -- fixed test doubles for the now-required lakeFS
# fields, so every other test in this module can construct a config without
# separately re-deriving the missing-credential defect under test below.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


def _construct_with_valid_credentials() -> OrbitalDriftConfig:
    """Builds a config with valid-but-fake lakeFS credentials as explicit,
    fully type-checked keyword arguments (not env vars). Every caller here is
    testing something OTHER than credential sourcing itself -- see the module
    docstring for why this avoids `type: ignore` where the tests below it
    cannot.
    """
    return OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
    )


def test_config_defaults_and_validation() -> None:
    """Verifies default parameters and custom overrides."""
    cfg = _construct_with_valid_credentials()

    assert cfg.aoi_name == "default-aoi"
    assert len(cfg.bands) == 4
    assert "B02" in cfg.bands
    assert "B08" in cfg.bands
    assert 0.0 <= cfg.cloud_cover_max_threshold <= 1.0
    assert cfg.train_device == "cuda:0"
    assert cfg.serve_device == "cuda:1"
    assert cfg.psi_threshold == 0.25
    assert cfg.drift_hysteresis_window == 3


def test_config_environment_variable_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that environment variables override defaults."""
    monkeypatch.setenv("ORBITAL_DRIFT_AOI_NAME", "midwest-croplands")
    monkeypatch.setenv("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "0.10")
    monkeypatch.setenv("ORBITAL_DRIFT_DRIFT_HYSTERESIS_WINDOW", "5")

    cfg = _construct_with_valid_credentials()
    assert cfg.aoi_name == "midwest-croplands"
    assert cfg.cloud_cover_max_threshold == 0.10
    assert cfg.drift_hysteresis_window == 5


# -----------------------------------------------------------------------------
# Defect 1: lakeFS credential placeholder defaults (Major security finding)
# -----------------------------------------------------------------------------


def test_lakefs_credential_fields_have_no_placeholder_default() -> None:
    """Regression guard for the exact defect: no string default at all.

    A previous version defaulted `lakefs_access_key`/`lakefs_secret_key` to
    the plausible-looking placeholders "lakefs_access_key"/"lakefs_secret_key".
    Asserting `is_required()` (rather than instantiating) pins the field
    metadata directly, so a future edit that reintroduces ANY default --
    placeholder or otherwise -- fails this test even if it happens to also
    satisfy the other tests below.
    """
    fields = OrbitalDriftConfig.model_fields
    assert fields["lakefs_access_key"].is_required()
    assert fields["lakefs_secret_key"].is_required()


def test_missing_lakefs_credentials_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing settings without lakeFS credentials set must fail fast.

    `_env_file=None` disables the model's `.env` source for this call only,
    so the assertion holds regardless of any local, gitignored `.env` a
    developer machine might have. Constructing with zero credential kwargs is
    the entire point of this test, so -- unlike the rest of this file -- it
    cannot use `_construct_with_valid_credentials()` and needs the
    `type: ignore` the module docstring explains.
    """
    monkeypatch.delenv("ORBITAL_DRIFT_LAKEFS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("ORBITAL_DRIFT_LAKEFS_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        OrbitalDriftConfig(_env_file=None)  # type: ignore[call-arg]

    missing_fields = {
        error["loc"][0] for error in exc_info.value.errors() if error["type"] == "missing"
    }
    assert "lakefs_access_key" in missing_fields
    assert "lakefs_secret_key" in missing_fields


def test_missing_lakefs_access_key_alone_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single missing credential is already enough to fail validation."""
    monkeypatch.delenv("ORBITAL_DRIFT_LAKEFS_ACCESS_KEY", raising=False)
    monkeypatch.setenv("ORBITAL_DRIFT_LAKEFS_SECRET_KEY", _TEST_SECRET_KEY)

    with pytest.raises(ValidationError):
        OrbitalDriftConfig(_env_file=None)  # type: ignore[call-arg]


def test_missing_lakefs_secret_key_alone_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single missing credential is already enough to fail validation."""
    monkeypatch.setenv("ORBITAL_DRIFT_LAKEFS_ACCESS_KEY", _TEST_ACCESS_KEY)
    monkeypatch.delenv("ORBITAL_DRIFT_LAKEFS_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError):
        OrbitalDriftConfig(_env_file=None)  # type: ignore[call-arg]


def test_lakefs_credentials_set_via_env_var_construct_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting both required env vars round-trips the values correctly."""
    monkeypatch.setenv("ORBITAL_DRIFT_LAKEFS_ACCESS_KEY", "prod-style-access-value")
    monkeypatch.setenv("ORBITAL_DRIFT_LAKEFS_SECRET_KEY", "prod-style-secret-value")

    cfg = OrbitalDriftConfig(_env_file=None)  # type: ignore[call-arg]

    assert cfg.lakefs_access_key == "prod-style-access-value"
    assert cfg.lakefs_secret_key == "prod-style-secret-value"  # noqa: S105 -- test double


# -----------------------------------------------------------------------------
# Defect 2: missing numeric bounds (Major finding)
# -----------------------------------------------------------------------------

# (env var, out-of-range value) -- each pair must raise ValidationError now
# that its field declares a bound. One field per documented semantic:
# canary_ratio/cloud_cover_max_threshold/ks_alpha are fractions (0.0-1.0);
# psi_threshold/auto_promote_margin are non-negative; the remaining fields are
# positive-integer counts whose own semantics break at zero (see
# src/orbital_drift/config.py field comments for the file:line evidence for
# each bound).
_OUT_OF_RANGE_CASES: tuple[tuple[str, str], ...] = (
    ("ORBITAL_DRIFT_CANARY_RATIO", "5.0"),
    ("ORBITAL_DRIFT_CANARY_RATIO", "-0.1"),
    ("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "1.2"),
    ("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "-0.01"),
    ("ORBITAL_DRIFT_PSI_THRESHOLD", "-0.1"),
    ("ORBITAL_DRIFT_KS_ALPHA", "-0.05"),
    ("ORBITAL_DRIFT_KS_ALPHA", "1.5"),
    ("ORBITAL_DRIFT_DRIFT_HYSTERESIS_WINDOW", "0"),
    ("ORBITAL_DRIFT_DRIFT_HYSTERESIS_WINDOW", "-1"),
    ("ORBITAL_DRIFT_DRIFT_COOLDOWN_SCENES", "0"),
    ("ORBITAL_DRIFT_DRIFT_COOLDOWN_SCENES", "-3"),
    ("ORBITAL_DRIFT_BATCH_SIZE", "0"),
    ("ORBITAL_DRIFT_BATCH_SIZE", "-16"),
    ("ORBITAL_DRIFT_PATCH_SIZE", "-1"),
    ("ORBITAL_DRIFT_PATCH_SIZE", "0"),
    ("ORBITAL_DRIFT_NUM_CLASSES", "0"),
    ("ORBITAL_DRIFT_NUM_CLASSES", "-2"),
    ("ORBITAL_DRIFT_INGEST_RETRY_BUDGET", "0"),
    ("ORBITAL_DRIFT_GRADIENT_ACCUMULATION_STEPS", "0"),
    ("ORBITAL_DRIFT_GRADIENT_ACCUMULATION_STEPS", "-2"),
    ("ORBITAL_DRIFT_AUTO_PROMOTE_MARGIN", "-0.01"),
)


@pytest.mark.parametrize("env_var,invalid_value", _OUT_OF_RANGE_CASES)
def test_out_of_range_numeric_field_raises_validation_error(
    env_var: str,
    invalid_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-range value for a now-bounded numeric field fails validation.

    Before the fix, none of these fields declared `ge`/`le`/`gt`/`lt`, so e.g.
    `ORBITAL_DRIFT_CANARY_RATIO=5.0` or a negative `patch_size` passed
    validation silently.
    """
    monkeypatch.setenv(env_var, invalid_value)

    with pytest.raises(ValidationError):
        _construct_with_valid_credentials()


# Boundary values themselves must remain valid -- the bounds documented in
# each field's own description ("0.0 to 1.0") are inclusive.
_INCLUSIVE_BOUNDARY_CASES: tuple[tuple[str, str], ...] = (
    ("ORBITAL_DRIFT_CANARY_RATIO", "0.0"),
    ("ORBITAL_DRIFT_CANARY_RATIO", "1.0"),
    ("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "0.0"),
    ("ORBITAL_DRIFT_CLOUD_COVER_MAX_THRESHOLD", "1.0"),
    ("ORBITAL_DRIFT_PSI_THRESHOLD", "0.0"),
    ("ORBITAL_DRIFT_KS_ALPHA", "0.0"),
    ("ORBITAL_DRIFT_KS_ALPHA", "1.0"),
    ("ORBITAL_DRIFT_DRIFT_HYSTERESIS_WINDOW", "1"),
    ("ORBITAL_DRIFT_DRIFT_COOLDOWN_SCENES", "1"),
    ("ORBITAL_DRIFT_BATCH_SIZE", "1"),
    ("ORBITAL_DRIFT_PATCH_SIZE", "1"),
    ("ORBITAL_DRIFT_NUM_CLASSES", "1"),
    ("ORBITAL_DRIFT_INGEST_RETRY_BUDGET", "1"),
    ("ORBITAL_DRIFT_GRADIENT_ACCUMULATION_STEPS", "1"),
    ("ORBITAL_DRIFT_AUTO_PROMOTE_MARGIN", "0.0"),
)


@pytest.mark.parametrize("env_var,boundary_value", _INCLUSIVE_BOUNDARY_CASES)
def test_inclusive_boundary_numeric_value_is_accepted(
    env_var: str,
    boundary_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented bounds are inclusive: the edge value itself is valid."""
    monkeypatch.setenv(env_var, boundary_value)

    _construct_with_valid_credentials()  # must not raise


# -----------------------------------------------------------------------------
# New config fields flagged by the RB-010 audit as absent from config.py.
# Only the fields are added here; wiring them into their consumer modules
# (drift/metrics.py, ingest/stac_client.py, data/dataset.py) is a separate
# RB-010 part -- these tests pin the field, its default, and its bound only.
# -----------------------------------------------------------------------------


def test_psi_moderate_threshold_default_matches_hardcoded_literal_it_will_replace() -> None:
    """Default mirrors the bare `0.10` literal at drift/metrics.py:99.

    `psi_moderate_threshold` is not yet consumed there (that wiring is a
    separate RB-010 part); this test only pins this field's own default.
    """
    cfg = _construct_with_valid_credentials()
    assert cfg.psi_moderate_threshold == 0.10


def test_stac_and_dataset_config_fields_have_expected_defaults() -> None:
    """Defaults mirror the currently-hardcoded values in their future consumers:
    stac_client.py's `backoff_factor=1.5`/`timeout=30.0` and dataset.py's
    `normalize_max=10000.0` (Sentinel-2 L2A reflectance scale).
    """
    cfg = _construct_with_valid_credentials()
    assert cfg.stac_backoff_factor == 1.5
    assert cfg.stac_request_timeout_seconds == 30.0
    assert cfg.dataset_normalize_max == 10000.0


def test_new_numeric_fields_accept_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The newly-added fields participate in the same env-override mechanism."""
    monkeypatch.setenv("ORBITAL_DRIFT_PSI_MODERATE_THRESHOLD", "0.15")
    monkeypatch.setenv("ORBITAL_DRIFT_STAC_BACKOFF_FACTOR", "2.0")
    monkeypatch.setenv("ORBITAL_DRIFT_STAC_REQUEST_TIMEOUT_SECONDS", "45.0")
    monkeypatch.setenv("ORBITAL_DRIFT_DATASET_NORMALIZE_MAX", "65535.0")

    cfg = _construct_with_valid_credentials()

    assert cfg.psi_moderate_threshold == 0.15
    assert cfg.stac_backoff_factor == 2.0
    assert cfg.stac_request_timeout_seconds == 45.0
    assert cfg.dataset_normalize_max == 65535.0


@pytest.mark.parametrize(
    "env_var,invalid_value",
    [
        ("ORBITAL_DRIFT_PSI_MODERATE_THRESHOLD", "-0.1"),
        ("ORBITAL_DRIFT_PSI_MODERATE_THRESHOLD", "1.5"),
        ("ORBITAL_DRIFT_STAC_BACKOFF_FACTOR", "0"),
        ("ORBITAL_DRIFT_STAC_BACKOFF_FACTOR", "-1.0"),
        ("ORBITAL_DRIFT_STAC_REQUEST_TIMEOUT_SECONDS", "0"),
        ("ORBITAL_DRIFT_STAC_REQUEST_TIMEOUT_SECONDS", "-5.0"),
        ("ORBITAL_DRIFT_DATASET_NORMALIZE_MAX", "0"),
        ("ORBITAL_DRIFT_DATASET_NORMALIZE_MAX", "-10000.0"),
    ],
)
def test_new_numeric_fields_reject_out_of_range_values(
    env_var: str,
    invalid_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_var, invalid_value)

    with pytest.raises(ValidationError):
        _construct_with_valid_credentials()
