"""Behavioural tests for package-scoped structured logs."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from datetime import UTC, datetime
from io import StringIO
from typing import cast

import pytest

from orbital_drift.observability.context import bind_context
from orbital_drift.observability.logging import (
    REDACTION_PLACEHOLDER,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Generator[None, None, None]:
    """Snapshot and restore logging handlers, level, and propagation around each test."""
    logger = logging.getLogger("orbital_drift")
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    yield
    logger.handlers.clear()
    for h in handlers:
        logger.addHandler(h)
    logger.setLevel(level)
    logger.propagate = propagate


def _logged_payload(stream: StringIO) -> dict[str, object]:
    """Decode exactly one log event so assertions inspect real formatter output."""
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return {str(key): value for key, value in parsed.items()}


def test_json_logging_includes_attribution_context_extras_and_redaction() -> None:
    """Structured events must retain useful attribution without retaining credentials."""
    stream = StringIO()
    configure_logging(
        level="INFO",
        stream=stream,
        extra_fields={"credential_source": "planted-static-secret", "service": "ledger"},
    )

    with bind_context(
        access_key="planted-context-secret", correlation_id="cid-123", tenant="north"
    ):
        get_logger("gate.runner").info(
            "gate %s",
            "complete",
            extra={"component": "runner", "api_token": "planted-secret"},
        )

    payload = _logged_payload(stream)
    assert set(payload) == {
        "api_token",
        "access_key",
        "component",
        "correlation_id",
        "credential_source",
        "function",
        "level",
        "line",
        "logger",
        "message",
        "module",
        "service",
        "tenant",
        "timestamp",
    }
    assert payload["api_token"] == REDACTION_PLACEHOLDER
    assert payload["access_key"] == REDACTION_PLACEHOLDER
    assert payload["component"] == "runner"
    assert payload["correlation_id"] == "cid-123"
    assert payload["credential_source"] == REDACTION_PLACEHOLDER
    assert (
        payload["function"] == "test_json_logging_includes_attribution_context_extras_and_redaction"
    )
    assert payload["level"] == "INFO"
    assert payload["logger"] == "orbital_drift.gate.runner"
    assert payload["message"] == "gate complete"
    assert payload["service"] == "ledger"
    assert payload["tenant"] == "north"
    assert datetime.fromisoformat(str(payload["timestamp"])).tzinfo == UTC


def test_json_logging_renders_exceptions_as_tracebacks() -> None:
    """Failure evidence needs the traceback in the same event as its attribution."""
    stream = StringIO()
    configure_logging(level="INFO", stream=stream)

    try:
        raise ValueError("planned failure")
    except ValueError:
        get_logger("gate").exception("gate failed")

    payload = _logged_payload(stream)
    assert payload["message"] == "gate failed"
    assert "ValueError: planned failure" in str(payload["exception"])


def test_configure_logging_replaces_handlers_instead_of_accumulating_them() -> None:
    """Repeated application startup hooks must not duplicate every emitted event."""
    first_stream = StringIO()
    second_stream = StringIO()
    configure_logging(level="INFO", stream=first_stream)
    configure_logging(level="INFO", stream=second_stream)

    package_logger = logging.getLogger("orbital_drift")
    assert len(package_logger.handlers) == 1
    handler = cast(logging.StreamHandler[StringIO], package_logger.handlers[0])
    assert handler.stream is second_stream
    assert package_logger.propagate is False

    get_logger("idempotent").info("emitted once")
    assert first_stream.getvalue() == ""
    assert _logged_payload(second_stream)["message"] == "emitted once"


def test_get_logger_preserves_qualified_names_and_plain_output() -> None:
    """Local debugging remains readable while names still stay inside the package."""
    stream = StringIO()
    configure_logging(level="INFO", stream=stream, json_output=False)

    logger = get_logger("orbital_drift.named")
    logger.info("human readable", extra={"access_key": "planted-secret"})

    line = stream.getvalue()
    assert logger.logger.name == "orbital_drift.named"
    assert "INFO orbital_drift.named" in line
    assert "human readable" in line
    assert REDACTION_PLACEHOLDER in line


def test_plain_logging_keeps_tracebacks_for_local_failure_diagnosis() -> None:
    """Human-readable local logs still need the exception that explains a failed gate."""
    stream = StringIO()
    configure_logging(level="INFO", stream=stream, json_output=False)

    try:
        raise RuntimeError("plain failure")
    except RuntimeError:
        get_logger("plain").exception("plain gate failed")

    assert "RuntimeError: plain failure" in stream.getvalue()
