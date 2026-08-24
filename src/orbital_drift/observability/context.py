"""Hold request correlation fields without sharing mutable process state.

``ContextVar`` preserves diagnostic identity across asynchronous work and keeps
one worker's fields from leaking into another worker's decision evidence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Final
from uuid import uuid4

CORRELATION_ID_FIELD: Final[str] = "correlation_id"
_EMPTY_CONTEXT: Final[Mapping[str, str]] = MappingProxyType({})
_CONTEXT: Final[ContextVar[Mapping[str, str]]] = ContextVar(
    "orbital_drift_observability_context",
    default=_EMPTY_CONTEXT,
)


def current_context() -> Mapping[str, str]:
    """Return an immutable snapshot so callers cannot corrupt active context."""
    return MappingProxyType(dict(_CONTEXT.get()))


@contextmanager
def _bound_context(**fields: str) -> Iterator[None]:
    """Temporarily add correlation fields and restore the exact prior scope."""
    merged_fields = dict(_CONTEXT.get())
    merged_fields.update(fields)
    token = _CONTEXT.set(MappingProxyType(merged_fields))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def bind_context(**fields: str) -> AbstractContextManager[None]:
    """Temporarily add correlation fields and restore the exact prior scope."""
    return _bound_context(**fields)


def new_correlation_id() -> str:
    """Create a compact random identifier that joins one operation's evidence."""
    return uuid4().hex


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[None]:
    """Bind a supplied or newly generated correlation identifier for one scope."""
    selected_id = correlation_id if correlation_id is not None else new_correlation_id()
    with bind_context(**{CORRELATION_ID_FIELD: selected_id}):
        yield
