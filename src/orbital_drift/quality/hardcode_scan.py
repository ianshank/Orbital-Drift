"""AST-based build gate for Constitution Principle III, "No Hardcoded Values".

The scanner deliberately walks :class:`ast.Constant` nodes instead of matching
text. Syntax makes annotations, docstrings, and exports visible as their own
contexts, avoiding the false positives that a line-oriented regular expression
would create. Its thresholds and match lists live in :class:`ScanPolicy` so the
project can tune enforcement in ``pyproject.toml`` rather than fork code.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import TextIO


class HardcodeScanError(Exception):
    """Raised when input cannot be analyzed, rather than silently weakening a gate."""


class LiteralCategory(StrEnum):
    """The configuration-oriented literal families the gate reports."""

    NUMERIC_LITERAL = "NUMERIC_LITERAL"
    URL_LITERAL = "URL_LITERAL"
    PATH_LITERAL = "PATH_LITERAL"
    HOST_LITERAL = "HOST_LITERAL"


@dataclass(frozen=True)
class ScanPolicy:
    """All policy choices controlling the scanner, including command-line defaults.

    Default pattern values are pinned because they are the scanner's own
    bootstrap policy. A checked-in ``[tool.orbital_drift.hardcode_scan]`` table
    can replace them without a code change; the defaults merely keep the tool
    useful before that table exists.
    """

    allowed_numbers: frozenset[float] = frozenset({0.0, 1.0, 2.0, -1.0})
    string_patterns: tuple[str, ...] = (
        "http://",  # pin: scanner bootstrap URL pattern
        "https://",  # pin: scanner bootstrap URL pattern
        "://",  # pin: scanner bootstrap URL scheme pattern
        "localhost",  # pin: scanner bootstrap local host pattern
        "127.0.0.1",  # pin: scanner bootstrap loopback host pattern
        "s3://",  # pin: scanner bootstrap object-store scheme pattern
        "cuda:",  # pin: scanner bootstrap compute-device pattern
        "/",  # pin: scanner bootstrap absolute-path indicator
    )
    exempt_paths: tuple[str, ...] = (
        "**/config.py",
        "**/projections.py",
        "**/*_data.py",
        "tests/**",
        "**/tests/**",
    )
    pin_marker: str = "# pin:"
    max_string_length: int = 160
    truncation_suffix: str = "..."
    allow_module_constants: bool = False
    paths: tuple[str, ...] = ("src/orbital_drift",)
    fail_on_findings: bool = True
    absolute_path_prefix: str = "/"
    url_patterns: tuple[str, ...] = (
        "http://",  # pin: scanner bootstrap URL classifier
        "https://",  # pin: scanner bootstrap URL classifier
        "://",  # pin: scanner bootstrap URL classifier
        "s3://",  # pin: scanner bootstrap URL classifier
    )
    host_patterns: tuple[str, ...] = (
        "localhost",  # pin: scanner bootstrap host classifier
        "127.0.0.1",  # pin: scanner bootstrap host classifier
        "cuda:",  # pin: scanner bootstrap device classifier
    )

    @classmethod
    def from_pyproject(cls, path: Path) -> ScanPolicy:
        """Load an optional policy table, preserving defaults for omitted keys.

        ``tomllib`` is used rather than a third-party parser because Python 3.12
        provides TOML support and policy loading should not add a runtime
        dependency to a quality gate.
        """
        with path.open("rb") as handle:
            document = tomllib.load(handle)
        tool = document.get("tool", {})
        if not isinstance(tool, dict):
            return cls()
        orbital_drift = tool.get("orbital_drift", {})
        if not isinstance(orbital_drift, dict):
            return cls()
        configured = orbital_drift.get("hardcode_scan", {})
        if not isinstance(configured, dict):
            return cls()

        defaults = cls()
        return cls(
            allowed_numbers=frozenset(
                float(value)
                for value in configured.get("allowed_numbers", defaults.allowed_numbers)
            ),
            string_patterns=tuple(
                str(value) for value in configured.get("string_patterns", defaults.string_patterns)
            ),
            exempt_paths=tuple(
                str(value) for value in configured.get("exempt_paths", defaults.exempt_paths)
            ),
            pin_marker=str(configured.get("pin_marker", defaults.pin_marker)),
            max_string_length=int(configured.get("max_string_length", defaults.max_string_length)),
            truncation_suffix=str(configured.get("truncation_suffix", defaults.truncation_suffix)),
            allow_module_constants=bool(
                configured.get("allow_module_constants", defaults.allow_module_constants)
            ),
            paths=tuple(str(value) for value in configured.get("paths", defaults.paths)),
            fail_on_findings=bool(configured.get("fail_on_findings", defaults.fail_on_findings)),
            absolute_path_prefix=str(
                configured.get("absolute_path_prefix", defaults.absolute_path_prefix)
            ),
            url_patterns=tuple(
                str(value) for value in configured.get("url_patterns", defaults.url_patterns)
            ),
            host_patterns=tuple(
                str(value) for value in configured.get("host_patterns", defaults.host_patterns)
            ),
        )


@dataclass(frozen=True)
class Finding:
    """A stable, source-located hardcoded literal suitable for CI output."""

    path: str
    line: int
    column: int
    value_repr: str
    category: LiteralCategory
    source_line: str

    def as_json(self) -> dict[str, int | str]:
        """Return JSON primitives so formatters never depend on enum internals."""
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "value_repr": self.value_repr,
            "category": self.category.value,
            "source_line": self.source_line,
        }


def _node_ids(node: ast.AST) -> set[int]:
    """Return identities for a whole AST context, avoiding value-based equality."""
    return {id(descendant) for descendant in ast.walk(node)}


def _is_docstring(node: ast.AST) -> bool:
    """Identify only leading string expressions, which Python defines as docstrings."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _annotation_context_ids(tree: ast.AST) -> set[int]:
    """Collect annotation subtrees so runtime subscripts remain visible to the gate."""
    identifiers: set[int] = set()
    function_nodes = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            identifiers.update(_node_ids(node.annotation))
        if isinstance(node, function_nodes):
            if node.returns is not None:
                identifiers.update(_node_ids(node.returns))
            arguments = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            if node.args.vararg is not None:
                arguments = (*arguments, node.args.vararg)
            if node.args.kwarg is not None:
                arguments = (*arguments, node.args.kwarg)
            for argument in arguments:
                if argument.annotation is not None:
                    identifiers.update(_node_ids(argument.annotation))
        if isinstance(node, ast.TypeAlias):
            identifiers.update(_node_ids(node.value))
    return identifiers


def _all_target(target: ast.AST) -> bool:
    """Return whether an assignment target is exactly the special export name."""
    return isinstance(target, ast.Name) and target.id == "__all__"


def _all_caps_assignment(node: ast.Assign, parent: ast.AST | None) -> bool:
    """Recognize direct module constants only when every target is conventionally constant."""
    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
    return isinstance(parent, ast.Module) and bool(names) and all(name.isupper() for name in names)


def _exempt_context_ids(
    tree: ast.AST,
    parents: dict[ast.AST, ast.AST],
    policy: ScanPolicy,
) -> set[int]:
    """Collect AST contexts whose literals are syntax metadata rather than configuration.

    We record node identities once before evaluating constants. This makes the
    exemptions explicit and avoids fragile checks based on source text or an
    incomplete visitor stack.
    """
    identifiers = _annotation_context_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and _is_docstring(node.body[0])
        ):
            identifiers.update(_node_ids(node.body[0]))
        if isinstance(node, ast.Assign) and any(_all_target(target) for target in node.targets):
            identifiers.update(_node_ids(node.value))
        if isinstance(node, ast.AnnAssign) and _all_target(node.target) and node.value is not None:
            identifiers.update(_node_ids(node.value))
        if (
            policy.allow_module_constants
            and isinstance(node, ast.Assign)
            and _all_caps_assignment(node, parents.get(node))
        ):
            identifiers.update(_node_ids(node.value))
    return identifiers


def _comments_by_line(source: str) -> dict[int, list[tuple[int, str]]]:
    """Return real comments by line using tokens, not raw ``#`` characters.

    A raw-line search would mistake a hash inside a string literal for a pin;
    ``tokenize`` identifies comments according to Python's lexer and therefore
    lets the exemption remain as mechanical as the AST scan itself.
    """
    comments: dict[int, list[tuple[int, str]]] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                comments.setdefault(token.start[0], []).append((token.start[1], token.string))
    except tokenize.TokenError as error:
        raise HardcodeScanError(f"Unable to tokenize source: {error}") from error
    return comments


def _has_justified_pin(
    node: ast.Constant,
    comments: dict[int, list[tuple[int, str]]],
    policy: ScanPolicy,
) -> bool:
    """Require a trailing marker plus a non-empty reason on the literal's line."""
    end_column = node.end_col_offset if node.end_col_offset is not None else node.col_offset
    for column, comment in comments.get(node.lineno, []):
        reason = comment.removeprefix(policy.pin_marker).strip()
        if column >= end_column and comment.startswith(policy.pin_marker) and reason:
            return True
    return False


def _numeric_value(node: ast.Constant, parents: dict[ast.AST, ast.AST]) -> int | float | None:
    """Interpret signed numeric constants while excluding booleans and complex numbers."""
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        return None
    parent = parents.get(node)
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.USub):
        return -node.value
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.UAdd):
        return node.value
    return node.value


def _string_category(value: str, policy: ScanPolicy) -> LiteralCategory | None:
    """Classify only configurable deployment-oriented strings, never arbitrary prose."""
    if value.startswith(policy.absolute_path_prefix):
        return LiteralCategory.PATH_LITERAL
    if any(pattern in value for pattern in policy.url_patterns):
        return LiteralCategory.URL_LITERAL
    if any(pattern in value for pattern in policy.host_patterns):
        return LiteralCategory.HOST_LITERAL
    if any(
        pattern in value
        for pattern in policy.string_patterns
        if pattern != policy.absolute_path_prefix
    ):
        return LiteralCategory.URL_LITERAL
    return None


def _value_repr(value: object, policy: ScanPolicy) -> str:
    """Bound rendered values so generated reports remain readable in CI logs."""
    rendered = repr(value)
    if len(rendered) > policy.max_string_length:
        return rendered[: policy.max_string_length] + policy.truncation_suffix
    return rendered


def _is_exempt_path(path: str, policy: ScanPolicy) -> bool:
    """Match paths through ``pathlib`` so policy globs work on every supported OS."""
    candidate = Path(path)
    return any(
        candidate.match(pattern) or fnmatch(path, pattern) for pattern in policy.exempt_paths
    )


def scan_source(source: str, *, path: str, policy: ScanPolicy) -> tuple[Finding, ...]:
    """Analyze source text and return byte-stably ordered Principle III findings."""
    if _is_exempt_path(path, policy):
        return ()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        location = f"{path}:{error.lineno}:{error.offset}"
        raise HardcodeScanError(f"Unable to parse {location}: {error.msg}") from error

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    exempt_ids = _exempt_context_ids(tree, parents, policy)
    comments = _comments_by_line(source)
    source_lines = source.splitlines()
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in exempt_ids:
            continue
        if _has_justified_pin(node, comments, policy):
            continue

        numeric_value = _numeric_value(node, parents)
        if numeric_value is not None:
            if float(numeric_value) in policy.allowed_numbers:
                continue
            category = LiteralCategory.NUMERIC_LITERAL
            value: object = numeric_value
        elif isinstance(node.value, str):
            string_category = _string_category(node.value, policy)
            if string_category is None:
                continue
            category = string_category
            value = node.value
        else:
            continue

        source_line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
        findings.append(
            Finding(
                path=path,
                line=node.lineno,
                column=node.col_offset,
                value_repr=_value_repr(value, policy),
                category=category,
                source_line=source_line,
            )
        )

    return tuple(sorted(findings, key=lambda finding: (finding.path, finding.line, finding.column)))


def _python_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Expand supplied files and directories deterministically before reading any source."""
    files: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise HardcodeScanError(f"Scan path does not exist: {path}")
        if path.is_dir():
            files.update(candidate for candidate in path.rglob("*.py") if candidate.is_file())
        elif path.suffix == ".py":
            files.add(path)
    return tuple(sorted(files, key=lambda candidate: str(candidate)))


def scan_paths(paths: Iterable[Path], policy: ScanPolicy) -> tuple[Finding, ...]:
    """Read Python paths and combine their individually stable findings in stable order."""
    findings: list[Finding] = []
    for path in _python_files(paths):
        findings.extend(
            scan_source(path.read_text(encoding="utf-8"), path=str(path), policy=policy)
        )
    return tuple(sorted(findings, key=lambda finding: (finding.path, finding.line, finding.column)))


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser separately so tests exercise the same production interface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="Python files or directories to scan")
    parser.add_argument(
        "--policy-file", default="pyproject.toml", help="TOML file containing scan policy"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    failure_group = parser.add_mutually_exclusive_group()
    failure_group.add_argument("--fail-on-findings", dest="fail_on_findings", action="store_true")
    failure_group.add_argument(
        "--no-fail-on-findings", dest="fail_on_findings", action="store_false"
    )
    parser.set_defaults(fail_on_findings=None)
    return parser


def _render_text(findings: Sequence[Finding]) -> str:
    """Render one greppable finding per line without relying on ``print``."""
    return "".join(
        f"{finding.path}:{finding.line}:{finding.column}: {finding.category.value} "
        f"{finding.value_repr}\n"
        for finding in findings
    )


def main(argv: Sequence[str] | None = None, *, stream: TextIO | None = None) -> int:
    """Run the build gate and return a shell-compatible success or failure status."""
    arguments = _build_parser().parse_args(argv)
    policy = ScanPolicy.from_pyproject(Path(arguments.policy_file))
    selected_paths = tuple(Path(value) for value in arguments.paths) or tuple(
        Path(value) for value in policy.paths
    )
    findings = scan_paths(selected_paths, policy)
    output = stream if stream is not None else sys.stdout
    if arguments.format == "json":
        output.write(json.dumps([finding.as_json() for finding in findings], sort_keys=True) + "\n")
    else:
        output.write(_render_text(findings))
    fail_on_findings = policy.fail_on_findings
    if arguments.fail_on_findings is not None:
        fail_on_findings = arguments.fail_on_findings
    return 1 if findings and fail_on_findings else 0  # pin: conventional process status


if __name__ == "__main__":
    raise SystemExit(main())
