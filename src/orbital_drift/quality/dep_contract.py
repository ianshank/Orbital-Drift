"""Dependency contract for Constitution Principle IV (Reproducibility).

PR#16's failure mode, restated as a mechanism instead of a hand-review habit:
``torch``/``torchgeo``/``mlflow``/``lakefs-client`` (and, measured directly,
``scikit-learn``/``esda``/``libpysal``/``fastapi``) landed as real ``import``
statements under ``src/orbital_drift`` with no matching declaration anywhere in
``pyproject.toml``. ``pip install -e ".[dev]"`` -- the one documented bootstrap
command (README.md, Principle IV) -- then produced a tree that could not import
its own modules, and nothing in CI said so, because nothing reconciled the two
sides.

This module is that reconciliation, AST-based for the same reason
``orbital_drift.quality.hardcode_scan`` is: syntax makes an import statement
visible as itself, rather than as a string a regular expression might also
match inside a docstring or a comment.

Two independent facts are compared:

* DECLARED -- every requirement in ``pyproject.toml``'s ``[project.dependencies]``
  and ``[project.optional-dependencies]`` tables (``tomllib``, stdlib), each
  reduced from a PyPI *distribution* name (``scikit-learn``) to the *import*
  name it actually provides (``sklearn``) via :attr:`DepContractPolicy.import_aliases`
  -- a small, overridable table, because the two names differ for a known,
  finite set of packages and no purely mechanical rule derives one from the
  other.
* IMPORTED -- every top-level ``import`` / ``from ... import`` target actually
  present under ``src/orbital_drift``, with the standard library
  (``sys.stdlib_module_names``, stdlib since Python 3.10) and the package's own
  name filtered out.

An import with no declaration anywhere is the exact PR#16 failure mode and is
reported as a Blocker; the module exits non-zero whenever one exists. A
declaration that nothing ever imports is reported too, but never fails the
gate on its own -- it is normal for a ``[dev]`` tool (``ruff``, ``pytest``, ...)
to be a distribution with no corresponding ``import`` under ``src/``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TextIO


class DepContractError(Exception):
    """Raised when input cannot be reconciled, rather than silently weakening a gate."""


@dataclass(frozen=True)
class DepContractPolicy:
    """All policy choices controlling the gate, including command-line defaults.

    Default values are pinned because they are the gate's own bootstrap policy.
    A checked-in ``[tool.orbital_drift.dep_contract]`` table can replace them
    without a code change; the defaults merely keep the tool useful before that
    table exists (mirrors ``orbital_drift.quality.hardcode_scan.ScanPolicy``).
    """

    #: Where IMPORTED is scanned from.
    source_paths: tuple[str, ...] = ("src/orbital_drift",)
    #: PyPI distribution name -> the top-level name it is actually imported as,
    #: for the finite set of packages where the two differ. Every name absent
    #: from this table is assumed to import as itself with `-` folded to `_`
    #: (PyPI's own packaging convention), which is correct for the large
    #: majority of distributions (``numpy``, ``requests``, ``torch``, ...).
    import_aliases: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType(
            {
                # pin: PyPI distribution name differs from its import name
                "scikit-learn": "sklearn",
                # pin: PyPI distribution uses a hyphen, the import uses an underscore
                # (the generic hyphen->underscore fold below gets this one right too,
                # but it is listed explicitly because it is the one this repo's own
                # [project.dependencies] core table actually relies on).
                "pydantic-settings": "pydantic_settings",
            }
        )
    )
    #: Import names that may be undeclared without failing the gate -- a named,
    #: reviewable exception list (Constitution III: no unexplained magic
    #: behaviour), for a legitimate case such as an optional, try/except-guarded
    #: import. Empty by default: every current import in src/orbital_drift is
    #: declared, so no exception is needed yet.
    allowed_undeclared: tuple[str, ...] = ()
    #: Whether a declared dependency nothing imports is computed and reported.
    #: Never controls the exit code -- see fail_on_undeclared.
    report_dead_declarations: bool = True
    #: Exit non-zero when an undeclared import is found. The one finding this
    #: gate exists to make non-optional; kept as a policy field (rather than a
    #: bare constant) only so a CLI/CI override is auditable the same way
    #: hardcode_scan's fail_on_findings is, not because relaxing it is expected.
    fail_on_undeclared: bool = True

    @classmethod
    def from_pyproject(cls, path: Path) -> DepContractPolicy:
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
        configured = orbital_drift.get("dep_contract", {})
        if not isinstance(configured, dict):
            return cls()

        defaults = cls()
        configured_aliases = configured.get("import_aliases", defaults.import_aliases)
        if not isinstance(configured_aliases, dict):
            configured_aliases = defaults.import_aliases

        return cls(
            source_paths=tuple(
                str(value) for value in configured.get("source_paths", defaults.source_paths)
            ),
            import_aliases=MappingProxyType(
                {str(key): str(value) for key, value in configured_aliases.items()}
            ),
            allowed_undeclared=tuple(
                str(value)
                for value in configured.get("allowed_undeclared", defaults.allowed_undeclared)
            ),
            report_dead_declarations=bool(
                configured.get("report_dead_declarations", defaults.report_dead_declarations)
            ),
            fail_on_undeclared=bool(
                configured.get("fail_on_undeclared", defaults.fail_on_undeclared)
            ),
        )


@dataclass(frozen=True)
class ImportSite:
    """One top-level, third-party, non-``orbital_drift`` import, located for diagnostics."""

    import_name: str
    path: str
    line: int
    column: int


@dataclass(frozen=True)
class UndeclaredImport:
    """An import present under ``src/`` with no declaration anywhere in pyproject.toml.

    The exact PR#16 failure mode -- a Blocker-shaped finding.
    """

    import_name: str
    path: str
    line: int
    column: int

    def as_json(self) -> dict[str, int | str]:
        """Return JSON primitives so formatters never depend on dataclass internals."""
        return {
            "import_name": self.import_name,
            "path": self.path,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class DeadDeclaration:
    """A pyproject.toml dependency that nothing under ``src/`` imports.

    Reported, never a failure on its own: a ``[dev]`` tool distribution
    (``ruff``, ``pytest-cov``, ...) is expected to have no ``import`` under
    ``src/orbital_drift`` at all.
    """

    import_name: str
    declared_by: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        """Return JSON primitives so formatters never depend on dataclass internals."""
        return {"import_name": self.import_name, "declared_by": list(self.declared_by)}


@dataclass(frozen=True)
class ContractReport:
    """The two sides of the reconciliation, both stably ordered."""

    undeclared: tuple[UndeclaredImport, ...]
    dead: tuple[DeadDeclaration, ...]


def stdlib_module_names() -> frozenset[str]:
    """The clean, stdlib-only way to tell a standard-library import from a real one.

    ``sys.stdlib_module_names`` (Python 3.10+; this repo pins 3.12) is
    generated from CPython's own build, so it needs no hand-kept list and
    cannot drift from the interpreter actually running the gate.
    """
    return frozenset(sys.stdlib_module_names)


#: A distribution name in a requirement string, stopping before a version
#: specifier, an environment marker or an extras bracket -- ``"pydantic==2.13.4"``
#: -> ``"pydantic"``, ``"types-requests==2.32.4.20250913"`` -> ``"types-requests"``,
#: ``"foo[extra]>=1"`` -> ``"foo"``.
_DISTRIBUTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _distribution_name(requirement: str) -> str:
    """Extract the PyPI distribution name from one ``[project]`` requirement string."""
    match = _DISTRIBUTION_NAME_RE.match(requirement.strip())
    if not match:
        raise DepContractError(
            f"Could not parse a distribution name from requirement: {requirement!r}"
        )
    return match.group(0)


def _import_name(distribution: str, policy: DepContractPolicy) -> str:
    """Map a PyPI distribution name to the top-level name it is imported as."""
    alias = policy.import_aliases.get(distribution)
    if alias is not None:
        return alias
    return distribution.replace("-", "_")


def _dependency_entries(project: Mapping[str, object]) -> list[tuple[object, str]]:
    """Every declared requirement paired with its declaring location ("core" or an extra name)."""
    entries: list[tuple[object, str]] = []
    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        entries.extend((requirement, "core") for requirement in dependencies)

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for extra_name, requirements in optional.items():
            if isinstance(requirements, list):
                entries.extend((requirement, str(extra_name)) for requirement in requirements)
    return entries


def declared_import_map(
    pyproject_path: Path, policy: DepContractPolicy
) -> dict[str, frozenset[str]]:
    """``{import name -> {"core", extra name, ...}}`` for every declared dependency.

    Reads ``[project.dependencies]`` (label ``"core"``) and every table under
    ``[project.optional-dependencies]`` (label = the extra's own name, ``dev``
    included -- a dev-only distribution still counts as "declared somewhere").
    """
    with pyproject_path.open("rb") as handle:
        document = tomllib.load(handle)
    project = document.get("project")
    if not isinstance(project, dict):
        raise DepContractError(
            f"{pyproject_path} has no [project] table to reconcile dependencies against"
        )

    declared: dict[str, set[str]] = {}
    for requirement, location in _dependency_entries(project):
        distribution = _distribution_name(str(requirement))
        import_name = _import_name(distribution, policy)
        declared.setdefault(import_name, set()).add(location)

    return {name: frozenset(locations) for name, locations in declared.items()}


def _python_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Expand supplied files and directories deterministically before reading any source."""
    files: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise DepContractError(f"Scan path does not exist: {path}")
        if path.is_dir():
            files.update(candidate for candidate in path.rglob("*.py") if candidate.is_file())
        elif path.suffix == ".py":
            files.add(path)
    return tuple(sorted(files, key=lambda candidate: str(candidate)))


def _top_level_third_party_imports(
    source: str, *, path: str, stdlib: frozenset[str]
) -> tuple[ImportSite, ...]:
    """Every non-stdlib, non-``orbital_drift`` import target in one module's source.

    Walks the whole tree (not only module-level statements), matching
    ``tests/architecture/test_import_boundaries.py``'s own AST convention: a
    third-party import inside a function body is exactly as real a dependency
    as one at module scope. Relative imports (``from . import x``,
    ``from .metrics import y``) are excluded outright -- by construction they
    can only resolve inside ``orbital_drift`` itself, so they are never a
    candidate for "undeclared".
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        location = f"{path}:{error.lineno}:{error.offset}"
        raise DepContractError(f"Unable to parse {location}: {error.msg}") from error

    sites: list[ImportSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in stdlib or top == "orbital_drift":
                    continue
                sites.append(ImportSite(top, path, node.lineno, node.col_offset))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: necessarily intra-package
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top in stdlib or top == "orbital_drift":
                continue
            sites.append(ImportSite(top, path, node.lineno, node.col_offset))
    return tuple(sites)


def scan_imports(paths: Iterable[Path]) -> tuple[ImportSite, ...]:
    """Read Python paths and return every third-party import site, stably ordered."""
    stdlib = stdlib_module_names()
    sites: list[ImportSite] = []
    for path in _python_files(paths):
        sites.extend(
            _top_level_third_party_imports(
                path.read_text(encoding="utf-8"), path=str(path), stdlib=stdlib
            )
        )
    return tuple(sorted(sites, key=lambda site: (site.path, site.line, site.column)))


def build_report(
    pyproject_path: Path, source_paths: Iterable[Path], policy: DepContractPolicy
) -> ContractReport:
    """Reconcile DECLARED against IMPORTED and return both sides of the report."""
    declared = declared_import_map(pyproject_path, policy)
    sites = scan_imports(source_paths)

    used_import_names: set[str] = set()
    undeclared: list[UndeclaredImport] = []
    for site in sites:
        used_import_names.add(site.import_name)
        if site.import_name in policy.allowed_undeclared:
            continue
        if site.import_name not in declared:
            undeclared.append(UndeclaredImport(site.import_name, site.path, site.line, site.column))

    dead: tuple[DeadDeclaration, ...] = ()
    if policy.report_dead_declarations:
        dead = tuple(
            DeadDeclaration(import_name, tuple(sorted(locations)))
            for import_name, locations in sorted(declared.items())
            if import_name not in used_import_names
        )

    return ContractReport(
        undeclared=tuple(
            sorted(undeclared, key=lambda finding: (finding.path, finding.line, finding.column))
        ),
        dead=dead,
    )


def _render_text(report: ContractReport) -> str:
    """Render one greppable finding per line without relying on ``print``."""
    lines = [
        f"{finding.path}:{finding.line}:{finding.column}: UNDECLARED_IMPORT "
        f"{finding.import_name!r} is imported under src/ but declared nowhere in pyproject.toml"
        for finding in report.undeclared
    ]
    lines.extend(
        f"DEAD_DECLARATION {dead.import_name!r} declared by {list(dead.declared_by)} "
        "but never imported under src/orbital_drift"
        for dead in report.dead
    )
    return "".join(f"{line}\n" for line in lines)


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser separately so tests exercise the same production interface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        help="Python files or directories to scan (default: policy source_paths)",
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="TOML file declaring dependencies and dep_contract policy",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    failure_group = parser.add_mutually_exclusive_group()
    failure_group.add_argument(
        "--fail-on-undeclared", dest="fail_on_undeclared", action="store_true"
    )
    failure_group.add_argument(
        "--no-fail-on-undeclared", dest="fail_on_undeclared", action="store_false"
    )
    parser.set_defaults(fail_on_undeclared=None)
    return parser


def main(argv: Sequence[str] | None = None, *, stream: TextIO | None = None) -> int:
    """Run the build gate and return a shell-compatible success or failure status."""
    arguments = _build_parser().parse_args(argv)
    pyproject_path = Path(arguments.pyproject)
    policy = DepContractPolicy.from_pyproject(pyproject_path)
    selected_paths = tuple(Path(value) for value in arguments.paths) or tuple(
        Path(value) for value in policy.source_paths
    )

    report = build_report(pyproject_path, selected_paths, policy)

    output = stream if stream is not None else sys.stdout
    if arguments.format == "json":
        payload = {
            "undeclared": [finding.as_json() for finding in report.undeclared],
            "dead": [dead.as_json() for dead in report.dead],
        }
        output.write(json.dumps(payload, sort_keys=True) + "\n")
    else:
        output.write(_render_text(report))

    fail_on_undeclared = policy.fail_on_undeclared
    if arguments.fail_on_undeclared is not None:
        fail_on_undeclared = arguments.fail_on_undeclared
    return 1 if report.undeclared and fail_on_undeclared else 0  # pin: conventional process status


if __name__ == "__main__":
    raise SystemExit(main())
