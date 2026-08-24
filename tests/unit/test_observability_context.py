"""Behavioural tests for isolated correlation context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

import pytest

from orbital_drift.observability.context import (
    bind_context,
    correlation_scope,
    current_context,
    new_correlation_id,
)


def test_bind_context_nests_and_restores_the_prior_mapping() -> None:
    """Nested work must inherit fields without permanently changing its caller."""
    assert dict(current_context()) == {}

    with bind_context(request_id="outer", tenant="north"):
        outer_context = current_context()
        assert dict(outer_context) == {"request_id": "outer", "tenant": "north"}
        with bind_context(request_id="inner", operation="train"):
            assert dict(current_context()) == {
                "request_id": "inner",
                "tenant": "north",
                "operation": "train",
            }
        assert current_context() == outer_context

    assert dict(current_context()) == {}


def test_bind_context_restores_state_when_scoped_work_raises() -> None:
    """An exception cannot leak a failed operation's identity into later events."""
    with bind_context(request_id="stable"):
        with (
            pytest.raises(RuntimeError, match="expected failure"),
            bind_context(job_id="transient"),
        ):
            raise RuntimeError("expected failure")
        assert dict(current_context()) == {"request_id": "stable"}

    assert dict(current_context()) == {}


def test_current_context_is_an_immutable_snapshot() -> None:
    """Consumers can inspect context but cannot mutate a scope owned by another layer."""
    with bind_context(job_id="immutable"):
        context = current_context()
        with pytest.raises(TypeError):
            cast(dict[str, str], context)["job_id"] = "changed"
        assert dict(current_context()) == {"job_id": "immutable"}


def test_correlation_scope_uses_supplied_or_generated_uuid_hex_identity() -> None:
    """Convenience scopes make every operation traceable even without a caller ID."""
    with correlation_scope("provided-id"):
        assert dict(current_context()) == {"correlation_id": "provided-id"}

    generated_id = new_correlation_id()
    assert UUID(generated_id).hex == generated_id

    with correlation_scope() as scope:
        assert scope is None
        generated_scope_id = current_context()["correlation_id"]
        assert UUID(generated_scope_id).hex == generated_scope_id
    assert dict(current_context()) == {}


def test_current_context_returns_mapping_interface() -> None:
    """The public snapshot remains usable through the immutable Mapping contract."""
    context: Mapping[str, str] = current_context()
    assert len(context) == 0
