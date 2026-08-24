"""Emit redacted, correlation-aware logs suitable for automated gate evidence.

The configuration is intentionally scoped to the package logger: applications
retain ownership of their root logger while Orbital-Drift produces dependable
machine-readable events.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from orbital_drift.observability.context import current_context

PACKAGE_LOGGER_NAME: Final[str] = "orbital_drift"
LOGGER_NAME_SEPARATOR: Final[str] = "."
TIMESTAMP_FIELD: Final[str] = "timestamp"
LEVEL_FIELD: Final[str] = "level"
LOGGER_FIELD: Final[str] = "logger"
MESSAGE_FIELD: Final[str] = "message"
MODULE_FIELD: Final[str] = "module"
FUNCTION_FIELD: Final[str] = "function"
LINE_FIELD: Final[str] = "line"
EXCEPTION_FIELD: Final[str] = "exception"
REDACTION_PLACEHOLDER: Final[str] = "[REDACTED]"
PLAIN_EXTRA_PREFIX: Final[str] = " | "
PLAIN_TIMESTAMP_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"
JSON_ENCODING: Final[str] = "utf-8"

# These substrings are deliberately broad: observability must fail closed when
# an operator invents a new secret-bearing field name.
redact_keys: Final[frozenset[str]] = frozenset({"secret", "token", "password", "key", "credential"})

_LOG_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _is_sensitive(field_name: str) -> bool:
    """Return whether a field name could expose protected operational material."""
    normalized_name = field_name.casefold()
    return any(redacted_key in normalized_name for redacted_key in redact_keys)


def _redact_value(field_name: str, value: object) -> object:
    """Redact a single value, recursing into nested containers."""
    if _is_sensitive(field_name):
        return REDACTION_PLACEHOLDER
    if isinstance(value, Mapping):
        return _redact_fields(value)
    if isinstance(value, (list, tuple)):
        return type(value)(
            _redact_value(field_name, item)
            if not isinstance(item, Mapping)
            else _redact_fields(item)
            for item in value
        )
    return value


def _redact_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Copy fields while replacing values whose names indicate credentials.

    Recurses into nested Mappings and lists/tuples so that structures like
    ``{"config": {"api_key": "secret"}}`` are redacted at every depth.
    """
    return {field_name: _redact_value(field_name, value) for field_name, value in fields.items()}


def _record_extras(record: logging.LogRecord) -> dict[str, object]:
    """Extract only caller-supplied values rather than logging implementation data."""
    return {
        field_name: value
        for field_name, value in record.__dict__.items()
        if field_name not in _LOG_RECORD_FIELDS
    }


class JsonFormatter(logging.Formatter):
    """Render each event as sorted JSON so log processors can preserve evidence."""

    def __init__(self, extra_fields: Mapping[str, str] | None = None) -> None:
        """Keep deployment-wide fields near the formatter that serializes them."""
        super().__init__()
        self._extra_fields = dict(extra_fields) if extra_fields is not None else {}

    def format(self, record: logging.LogRecord) -> str:
        """Serialize standard attribution, caller fields, and any traceback safely."""
        payload = _redact_fields(self._extra_fields)
        payload.update(_redact_fields(_record_extras(record)))
        payload.update(
            {
                FUNCTION_FIELD: record.funcName,
                LEVEL_FIELD: record.levelname,
                LINE_FIELD: record.lineno,
                LOGGER_FIELD: record.name,
                MESSAGE_FIELD: record.getMessage(),
                MODULE_FIELD: record.module,
                TIMESTAMP_FIELD: datetime.fromtimestamp(record.created, UTC).isoformat(),
            }
        )
        if record.exc_info is not None:
            payload[EXCEPTION_FIELD] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Render readable local diagnostics while applying the same credential redaction."""

    def __init__(self, extra_fields: Mapping[str, str] | None = None) -> None:
        """Retain deployment-wide fields in non-JSON diagnostics as well."""
        super().__init__()
        self._extra_fields = dict(extra_fields) if extra_fields is not None else {}

    def format(self, record: logging.LogRecord) -> str:
        """Format a concise local line, appending deterministic structured context."""
        timestamp = datetime.fromtimestamp(record.created, UTC).strftime(PLAIN_TIMESTAMP_FORMAT)
        line = (
            f"{timestamp} {record.levelname} {record.name} "
            f"{record.module}.{record.funcName}:{record.lineno} {record.getMessage()}"
        )
        fields = _redact_fields(self._extra_fields)
        fields.update(_redact_fields(_record_extras(record)))
        if fields:
            line = f"{line}{PLAIN_EXTRA_PREFIX}{json.dumps(fields, default=str, sort_keys=True)}"
        if record.exc_info is not None:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class _ContextLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Merge the context active at emission time with explicit log event fields."""

    def process(
        self,
        msg: Any,
        kwargs: MutableMapping[str, Any],
    ) -> tuple[Any, MutableMapping[str, Any]]:
        """Give explicit event fields precedence without dropping correlation identity."""
        explicit_extra = kwargs.get("extra")
        merged_extra: dict[str, object] = dict(current_context())
        if self.extra is not None:
            merged_extra.update(self.extra)
        if isinstance(explicit_extra, Mapping):
            merged_extra.update({str(key): value for key, value in explicit_extra.items()})
        kwargs["extra"] = merged_extra
        return msg, kwargs


def configure_logging(
    *,
    level: str,
    stream: TextIO | None = None,
    json_output: bool = True,
    extra_fields: Mapping[str, str] | None = None,
) -> None:
    """Configure exactly one package-owned handler without altering host logging."""
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    package_logger.setLevel(level)
    package_logger.propagate = False
    for existing_handler in tuple(package_logger.handlers):
        package_logger.removeHandler(existing_handler)

    handler = logging.StreamHandler(stream)
    formatter: logging.Formatter = (
        JsonFormatter(extra_fields) if json_output else PlainFormatter(extra_fields)
    )
    handler.setFormatter(formatter)
    package_logger.addHandler(handler)


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a package-namespaced logger that binds current correlation fields."""
    qualified_name = (
        name
        if name == PACKAGE_LOGGER_NAME
        or name.startswith(f"{PACKAGE_LOGGER_NAME}{LOGGER_NAME_SEPARATOR}")
        else f"{PACKAGE_LOGGER_NAME}{LOGGER_NAME_SEPARATOR}{name}"
    )
    return _ContextLoggerAdapter(logging.getLogger(qualified_name), {})
