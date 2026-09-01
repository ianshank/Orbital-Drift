"""Emit redacted, correlation-aware logs suitable for automated gate evidence.

The configuration is intentionally scoped to the package logger: applications
retain ownership of their root logger while Orbital-Drift produces dependable
machine-readable events.
"""

from __future__ import annotations

import dataclasses
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

# Types that cannot themselves hold nested named fields, so passing one through
# _redact_value unchanged is safe by construction; anything outside this set that
# reaches the final fallback in _redact_value is logged (not silently accepted) so
# a future secret-bearing object type does not reproduce the OrbitalDriftConfig gap.
_SAFE_LEAF_TYPES: Final[tuple[type, ...]] = (str, int, float, bool, bytes, type(None))

# A plain stdlib logger, not get_logger(): this module IS the formatter, so its own
# self-diagnostic must not depend on (or recurse back through) the correlation-context
# machinery it may be reporting a gap in.
_INTERNAL_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


def _is_sensitive(field_name: str) -> bool:
    """Return whether a field name could expose protected operational material."""
    normalized_name = field_name.casefold()
    return any(redacted_key in normalized_name for redacted_key in redact_keys)


def _is_namedtuple_instance(value: object) -> bool:
    """Duck-type a NamedTuple instance: a tuple subclass exposing _fields/_make.

    NamedTuple has no common runtime base beyond ``tuple`` itself, so this is the
    standard detection idiom (mirrors what every ``typing.NamedTuple``-generated
    class exposes).
    """
    value_type = type(value)
    return (
        isinstance(value, tuple) and hasattr(value_type, "_fields") and hasattr(value_type, "_make")
    )


def _redact_namedtuple(value: object) -> object:
    """Rebuild a NamedTuple with each field redacted under its own field name.

    ``type(value)(<generator>)`` -- what the generic ``(list, tuple)`` branch below
    does -- fails for NamedTuples: ``__new__`` expects one positional argument per
    declared field, not a single iterable, so it raises ``TypeError``. Standard
    library ``logging`` swallows that exception and drops the whole log line
    rather than propagating it. ``_make`` is the documented reconstruction hook
    that takes a single iterable instead, which is why this check must run before
    the generic tuple branch rather than falling into it.
    """
    value_type = type(value)
    # NOTE: `value_type` is statically `type[object]`, which has no `_fields`/
    # `_make`. Plain attribute access would need `# type: ignore[attr-defined]`
    # on every use; getattr keeps mypy's inferred type `Any` here and lets the
    # `: tuple[str, ...]`/assignment-to-`object` annotations below re-anchor it
    # to a concrete type without an ignore comment. Suppressed below (not a style
    # choice, a mypy-narrowing workaround bugbear cannot see).
    field_names: tuple[str, ...] = getattr(value_type, "_fields")  # noqa: B009
    make = getattr(value_type, "_make")  # noqa: B009
    redacted_values = [_redact_value(name, getattr(value, name)) for name in field_names]
    rebuilt: object = make(redacted_values)
    return rebuilt


def _is_pydantic_model(value: object) -> bool:
    """Duck-type a pydantic v2 model/settings instance via its public ``model_dump``.

    Matches both ``BaseModel`` and ``BaseSettings`` (pydantic-settings' ``BaseSettings``
    extends pydantic's ``BaseModel`` in v2) without importing pydantic here, keeping
    this observability module dependency-light.
    """
    return not isinstance(value, type) and callable(getattr(value, "model_dump", None))


def _redact_pydantic_model(value: object) -> object:
    """Dump a pydantic model to a plain dict and redact it like any other Mapping.

    This is the fix for the confirmed ``OrbitalDriftConfig`` bypass: before this
    branch existed, a pydantic model matched none of ``_redact_value``'s isinstance
    checks and fell through to ``return value`` unchanged, so ``json.dumps``'s
    ``default=str`` fallback stringified its credential fields verbatim into the
    log line.
    """
    # Same getattr-for-mypy rationale as _redact_namedtuple above: `value` is
    # statically `object` here (narrowing from _is_pydantic_model's plain bool
    # return does not cross the function boundary).
    model_dump = getattr(value, "model_dump")  # noqa: B009
    dumped: dict[str, object] = model_dump()
    return _redact_fields(dumped)


def _warn_unrecognized_object_type(field_name: str, value: object) -> None:
    """Surface, at DEBUG, that an object type reached the formatter uninspected.

    ``_redact_value`` cannot enumerate every structured type a caller might log;
    for anything left over after the Mapping/NamedTuple/list/tuple/dataclass/
    pydantic checks, silently trusting it holds no secret-shaped attributes is
    exactly how the OrbitalDriftConfig bypass happened. This does not redact the
    value -- only ``_is_sensitive(field_name)`` and the structural checks above do
    that -- it only makes the residual gap observable instead of silent. Logs the
    type's name only, never the value itself, so the diagnostic cannot itself leak
    whatever the value holds.
    """
    _INTERNAL_LOGGER.debug(
        "Unredacted object type reached the log formatter for field %r: %s",
        field_name,
        type(value).__qualname__,
    )


def _redact_value(field_name: str, value: object) -> object:
    """Redact a single value, recursing into nested containers.

    Check order is deliberate: NamedTuple must be tested before the generic
    ``(list, tuple)`` branch because every NamedTuple IS a tuple subclass and would
    otherwise crash ``type(value)(<generator>)`` instead of being redacted;
    dataclass and pydantic-model detection must run before the final fallback so
    structured secret-bearing objects (e.g. ``OrbitalDriftConfig``) are inspected
    field-by-field instead of being passed through whole.
    """
    if _is_sensitive(field_name):
        return REDACTION_PLACEHOLDER
    if isinstance(value, Mapping):
        return _redact_fields(value)
    if _is_namedtuple_instance(value):
        return _redact_namedtuple(value)
    if isinstance(value, (list, tuple)):
        return type(value)(
            _redact_value(field_name, item)
            if not isinstance(item, Mapping)
            else _redact_fields(item)
            for item in value
        )
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _redact_fields(dataclasses.asdict(value))
    if _is_pydantic_model(value):
        return _redact_pydantic_model(value)
    if not isinstance(value, _SAFE_LEAF_TYPES):
        _warn_unrecognized_object_type(field_name, value)
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
