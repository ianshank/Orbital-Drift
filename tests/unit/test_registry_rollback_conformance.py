"""T071 conformance suite: one set of registry behaviours, run over every implementation.

Audience: ml-engineer, spec-guardian, adversarial-reviewer, and whoever writes T059's
MLflow adapter (it joins this suite by adding one harness below).

The behaviours are T071's acceptance criteria (specs/001-orbital-drift-ct/tasks.md;
D-016/03c, D-016/08): rollback restores exactly the version that held Production
immediately before the current one, from an ordered promotion history; a version
that never held Production is unreachable by any rollback; a rollback with no
target raises and leaves Production unchanged; repeated rollbacks walk the history
back one promotion at a time (stack semantics, B-11's "rollback moves it to the
recorded previous_champion"), raising once it is exhausted.

The two implementations under test have different public interfaces:
``ModelRegistryOps`` (registry/ops.py) uses int versions, a validated ``StageName``
literal, and ``ValueError``/``NoRollbackTargetError``; ``InMemoryModelRegistry``
(ports/registry.py, kept while B-12 keeps the port) uses str versions inside
``LineageEnvelope`` values, free-form stage strings, and ``KeyError``. Each is
driven through a thin test-side harness that maps those interface differences
and nothing else. Where ``InMemoryModelRegistry`` does not meet a behaviour, its
harness declares a ``Divergence`` and the test asserts the divergent behaviour it
actually has -- never a skip (Constitution V, charter C-6). A declared divergence
that stops diverging, or an undeclared one that appears, fails a test.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, cast

import pytest

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
from orbital_drift.ports.registry import InMemoryModelRegistry
from orbital_drift.registry.ops import (
    ARCHIVED_STAGE,
    PRODUCTION_STAGE,
    STAGING_STAGE,
    ModelRegistryOps,
    NoRollbackTargetError,
    StageName,
)

MODEL: Final = "unet-s2"
UNREGISTERED_MODEL: Final = "never-registered"
UNREGISTERED_VERSION: Final = 99
WRONG_CASE_PRODUCTION: Final = PRODUCTION_STAGE.lower()


class Divergence(StrEnum):
    """Behaviours where InMemoryModelRegistry departs from the ModelRegistryOps reference.

    Each member is exercised by exactly one test below, which asserts both sides.
    """

    # ports/registry.py keeps one independent list per stage, so promoting a version
    # does not move the version it replaces into Archived.
    SUPERSEDED_VERSION_NOT_ARCHIVED = "superseded-version-not-archived"
    # Re-promoting the version already in Production appends a duplicate history
    # entry, so the next rollback "restores" the version that is already serving.
    REPROMOTION_DUPLICATES_HISTORY = "repromotion-duplicates-history"
    # Moving the Production version to another stage appends it to that stage's list
    # but leaves it at the top of Production's list, so Production is never vacated.
    DEMOTION_DOES_NOT_VACATE_PRODUCTION = "demotion-does-not-vacate-production"
    # Stages are free-form strings, so a wrong-case stage name is accepted silently.
    STAGE_NAMES_UNVALIDATED = "stage-names-unvalidated"


class RegistryHarness(Protocol):
    """The behaviour vocabulary T071's acceptance criteria are written in."""

    label: str
    divergences: frozenset[Divergence]
    no_target_error: type[LookupError]
    unknown_version_error: type[Exception]

    def register(self, model_name: str) -> int:
        """Register one new version and return its number (1, 2, ... per model)."""
        ...

    def transition(self, model_name: str, version: int, stage: str) -> None:
        """Move a registered version to a stage."""
        ...

    def holder(self, model_name: str, stage: str) -> int | None:
        """Return the version the registry reports for a stage, or None."""
        ...

    def rollback(self, model_name: str) -> int:
        """Roll Production back and return the restored version."""
        ...


class OpsHarness:
    """Drives registry/ops.py's ModelRegistryOps: the reference implementation."""

    label = "ModelRegistryOps"
    divergences: frozenset[Divergence] = frozenset()
    no_target_error: type[LookupError] = NoRollbackTargetError
    unknown_version_error: type[Exception] = ValueError

    def __init__(self) -> None:
        self.registry = ModelRegistryOps()

    def register(self, model_name: str) -> int:
        return self.registry.register_model_version(model_name, run_id=f"run-{model_name}")

    def transition(self, model_name: str, version: int, stage: str) -> None:
        # cast, not a check: an invalid stage must reach transition_stage's own
        # runtime validation, which is one of the behaviours under test.
        self.registry.transition_stage(model_name, version, cast(StageName, stage))

    def holder(self, model_name: str, stage: str) -> int | None:
        return self.registry.get_stage_version(model_name, cast(StageName, stage))

    def rollback(self, model_name: str) -> int:
        return self.registry.rollback_production(model_name)


_BASE_ENVELOPE: Final = LineageEnvelope(
    schema_version=SCHEMA_VERSION,
    envelope_id="conformance-envelope",
    created_at=datetime(2026, 10, 7, tzinfo=UTC),
    git_sha="conformance-sha",
    config_hash="conformance-config",
    data_commit="conformance-commit",
    data_repository="conformance-repository",
    data_branch="main",
    dataset_name="conformance-dataset",
    model_name=MODEL,
    model_version="0",
    run_id="conformance-run",
    random_seed=0,
    python_version="3.12",
    platform_tag="conformance-platform",
    toolchain=DEFAULT_TOOLCHAIN,
    metrics=DEFAULT_METRICS,
    spatial_split_id=DEFAULT_SPATIAL_SPLIT_ID,
    label_strategy=DEFAULT_LABEL_STRATEGY,
    code_version=DEFAULT_CODE_VERSION,
    notes=DEFAULT_NOTES,
)


class InMemoryHarness:
    """Drives ports/registry.py's InMemoryModelRegistry through the same vocabulary.

    Maps only interface differences: int <-> str versions, envelope construction,
    KeyError-for-absence on get_by_stage <-> None.
    """

    label = "InMemoryModelRegistry"
    divergences: frozenset[Divergence] = frozenset(Divergence)
    no_target_error: type[LookupError] = KeyError
    unknown_version_error: type[Exception] = KeyError

    def __init__(self) -> None:
        self.registry = InMemoryModelRegistry()
        self._last_version: dict[str, int] = {}

    def register(self, model_name: str) -> int:
        version = self._last_version.get(model_name, 0) + 1
        self._last_version[model_name] = version
        self.registry.register(
            dataclasses.replace(
                _BASE_ENVELOPE,
                envelope_id=f"envelope-{model_name}-{version}",
                model_name=model_name,
                model_version=str(version),
            )
        )
        return version

    def transition(self, model_name: str, version: int, stage: str) -> None:
        self.registry.transition_stage(model_name, str(version), stage)

    def holder(self, model_name: str, stage: str) -> int | None:
        try:
            return int(self.registry.get_by_stage(model_name, stage).model_version)
        except KeyError:
            return None

    def rollback(self, model_name: str) -> int:
        return int(self.registry.rollback(model_name, PRODUCTION_STAGE).model_version)


@pytest.fixture(params=[OpsHarness, InMemoryHarness], ids=lambda harness: harness.label)
def registry(request: pytest.FixtureRequest) -> RegistryHarness:
    harness_type = cast(type[RegistryHarness], request.param)
    return harness_type()


def _register(registry: RegistryHarness, count: int) -> list[int]:
    return [registry.register(MODEL) for _ in range(count)]


def _promote(registry: RegistryHarness, *versions: int) -> None:
    for version in versions:
        registry.transition(MODEL, version, PRODUCTION_STAGE)


def _rollback_until_exhausted(registry: RegistryHarness, promotions: int) -> list[int]:
    """Roll back until the registry raises its no-target error; return every target.

    Bounded: a history of `promotions` entries yields at most `promotions - 1` targets,
    so one call beyond that must raise. A registry that never runs out of targets (the
    pre-T071 ModelRegistryOps cycled between Archived versions forever) fails here
    instead of hanging the suite.
    """
    restored: list[int] = []
    for _ in range(promotions):
        try:
            restored.append(registry.rollback(MODEL))
        except registry.no_target_error:
            return restored
    pytest.fail(f"rollback still found targets after {promotions} calls: {restored}")


# --- Shared behaviours: both implementations must meet these exactly. -----------------


def test_rollback_restores_previous_production_not_a_rejected_challenger(
    registry: RegistryHarness,
) -> None:
    """D-016/08's reproduction: v1 then v2 promoted, v3 archived as a rejected challenger.

    Before T071, ModelRegistryOps promoted the highest-numbered Archived version, so
    this returned 3: a model that never served became Production.
    """
    v1, v2, v3 = _register(registry, 3)
    _promote(registry, v1, v2)
    registry.transition(MODEL, v3, ARCHIVED_STAGE)  # rejected challenger, never served

    assert registry.rollback(MODEL) == v1
    assert registry.holder(MODEL, PRODUCTION_STAGE) == v1


def test_rollback_with_a_single_promotion_raises_and_leaves_production_unchanged(
    registry: RegistryHarness,
) -> None:
    """D-016/03c's second defect: rollback used to archive the only Production version
    and return None, leaving no Production model at all."""
    (v1,) = _register(registry, 1)
    _promote(registry, v1)

    with pytest.raises(registry.no_target_error):
        registry.rollback(MODEL)

    assert registry.holder(MODEL, PRODUCTION_STAGE) == v1


def test_rollback_of_an_unregistered_model_raises(registry: RegistryHarness) -> None:
    with pytest.raises(registry.no_target_error):
        registry.rollback(UNREGISTERED_MODEL)

    assert registry.holder(UNREGISTERED_MODEL, PRODUCTION_STAGE) is None


def test_rollback_with_nothing_ever_promoted_raises_even_with_archived_versions(
    registry: RegistryHarness,
) -> None:
    _, rejected = _register(registry, 2)
    registry.transition(MODEL, rejected, ARCHIVED_STAGE)

    with pytest.raises(registry.no_target_error):
        registry.rollback(MODEL)

    assert registry.holder(MODEL, PRODUCTION_STAGE) is None


def test_repeated_rollback_walks_the_promotion_history_back_one_step_at_a_time(
    registry: RegistryHarness,
) -> None:
    """Stack semantics: each promotion records the version it replaced; each rollback
    restores that record and discards the current one; once the oldest promotion is
    reached, a further rollback raises and leaves the oldest version in Production."""
    v1, v2, v3, v4 = _register(registry, 4)
    _promote(registry, v1, v2, v3, v4)

    assert _rollback_until_exhausted(registry, promotions=4) == [v3, v2, v1]
    assert registry.holder(MODEL, PRODUCTION_STAGE) == v1


def test_versions_that_never_held_production_are_unreachable_by_any_rollback(
    registry: RegistryHarness,
) -> None:
    v1, rejected_a, v3, rejected_b, staged = _register(registry, 5)
    _promote(registry, v1)
    registry.transition(MODEL, rejected_a, ARCHIVED_STAGE)
    _promote(registry, v3)
    registry.transition(MODEL, rejected_b, ARCHIVED_STAGE)
    registry.transition(MODEL, staged, STAGING_STAGE)

    restored = _rollback_until_exhausted(registry, promotions=2)

    assert restored == [v1]
    assert not {rejected_a, rejected_b, staged} & set(restored)


def test_a_rolled_back_version_is_not_restored_by_a_later_rollback(
    registry: RegistryHarness,
) -> None:
    """Rollback discards the promotion it undoes: the version rolled away from is gone
    from the history, so a later promotion's rollback chain never returns to it."""
    v1, v2, rolled_away, v4 = _register(registry, 4)
    _promote(registry, v1, v2, rolled_away)
    assert registry.rollback(MODEL) == v2

    _promote(registry, v4)

    assert _rollback_until_exhausted(registry, promotions=4) == [v2, v1]


def test_explicitly_repromoting_an_older_version_is_a_new_history_entry(
    registry: RegistryHarness,
) -> None:
    v1, v2 = _register(registry, 2)
    _promote(registry, v1, v2, v1)

    assert _rollback_until_exhausted(registry, promotions=3) == [v2, v1]
    assert registry.holder(MODEL, PRODUCTION_STAGE) == v1


def test_promoting_an_unregistered_version_raises_and_changes_nothing(
    registry: RegistryHarness,
) -> None:
    (v1,) = _register(registry, 1)
    _promote(registry, v1)

    with pytest.raises(registry.unknown_version_error):
        registry.transition(MODEL, UNREGISTERED_VERSION, PRODUCTION_STAGE)

    assert registry.holder(MODEL, PRODUCTION_STAGE) == v1
    # The failed promotion recorded no history entry: v1 is still the only one.
    with pytest.raises(registry.no_target_error):
        registry.rollback(MODEL)


def test_the_no_target_error_is_a_lookup_error(registry: RegistryHarness) -> None:
    """Common contract for callers that drive either implementation: catching
    LookupError handles a missing rollback target from both."""
    assert issubclass(registry.no_target_error, LookupError)


# --- Divergent behaviours: asserted on both sides, never skipped. ---------------------


def test_promotion_archives_the_superseded_production_version(
    registry: RegistryHarness,
) -> None:
    v1, v2 = _register(registry, 2)
    _promote(registry, v1, v2)

    assert registry.holder(MODEL, PRODUCTION_STAGE) == v2
    if Divergence.SUPERSEDED_VERSION_NOT_ARCHIVED in registry.divergences:
        assert registry.holder(MODEL, ARCHIVED_STAGE) is None
    else:
        assert registry.holder(MODEL, ARCHIVED_STAGE) == v1


def test_repromoting_the_production_version_is_idempotent(registry: RegistryHarness) -> None:
    v1, v2 = _register(registry, 2)
    _promote(registry, v1, v2, v2)

    if Divergence.REPROMOTION_DUPLICATES_HISTORY in registry.divergences:
        # The duplicate entry makes the next rollback a no-op restore of v2.
        assert registry.rollback(MODEL) == v2
    else:
        assert registry.rollback(MODEL) == v1


def test_demoting_the_production_version_vacates_production_and_disarms_rollback(
    registry: RegistryHarness,
) -> None:
    v1, v2 = _register(registry, 2)
    _promote(registry, v1, v2)
    registry.transition(MODEL, v2, ARCHIVED_STAGE)

    if Divergence.DEMOTION_DOES_NOT_VACATE_PRODUCTION in registry.divergences:
        assert registry.holder(MODEL, PRODUCTION_STAGE) == v2
        assert registry.rollback(MODEL) == v1
    else:
        assert registry.holder(MODEL, PRODUCTION_STAGE) is None
        with pytest.raises(registry.no_target_error):
            registry.rollback(MODEL)
        assert registry.holder(MODEL, PRODUCTION_STAGE) is None


def test_a_wrong_case_stage_name_is_rejected(registry: RegistryHarness) -> None:
    (v1,) = _register(registry, 1)

    if Divergence.STAGE_NAMES_UNVALIDATED in registry.divergences:
        registry.transition(MODEL, v1, WRONG_CASE_PRODUCTION)
        assert registry.holder(MODEL, WRONG_CASE_PRODUCTION) == v1
    else:
        with pytest.raises(ValueError, match="Invalid target_stage"):
            registry.transition(MODEL, v1, WRONG_CASE_PRODUCTION)
    assert registry.holder(MODEL, PRODUCTION_STAGE) is None


def test_declared_divergences_are_exact() -> None:
    """ModelRegistryOps is the reference, so it may declare none; InMemoryModelRegistry
    declares every member. Adding a divergence to the reference to make a failing
    behaviour pass is the regression this test exists to catch."""
    assert OpsHarness.divergences == frozenset()
    assert InMemoryHarness.divergences == frozenset(Divergence)
